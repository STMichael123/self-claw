"""会话生命周期管理 — 对应 SPEC FR-013。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

from src.contracts.models import SessionStatus

logger = structlog.get_logger()

DEFAULT_SESSION_TIMEOUT_MIN = 30


class SessionManager:
    """管理多轮对话的会话生命周期与上下文。"""

    def __init__(self, db: sqlite3.Connection, *, timeout_min: int = DEFAULT_SESSION_TIMEOUT_MIN) -> None:
        self._db = db
        self._timeout = timedelta(minutes=timeout_min)

    def create_session(self, user_id: str, *, channel_type: str = "web") -> str:
        """创建新会话。"""
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """INSERT INTO sessions (id, user_id, channel_type, status, created_at, last_active_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, channel_type, SessionStatus.ACTIVE, now, now),
        )
        self._db.commit()
        logger.info("session_created", session_id=session_id, user_id=user_id)
        return session_id

    def get_or_create_session(self, user_id: str, *, channel_type: str = "web") -> str:
        """查找用户的活跃会话，超时则创建新会话。"""
        cutoff = (datetime.now(timezone.utc) - self._timeout).isoformat()
        row = self._db.execute(
            """SELECT id FROM sessions
               WHERE user_id = ? AND status = ? AND last_active_at > ?
               ORDER BY last_active_at DESC LIMIT 1""",
            (user_id, SessionStatus.ACTIVE, cutoff),
        ).fetchone()

        if row:
            session_id = row["id"]
            self.touch(session_id)
            return session_id
        return self.create_session(user_id, channel_type=channel_type)

    def touch(self, session_id: str) -> None:
        """更新会话最后活跃时间。"""
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "UPDATE sessions SET last_active_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._db.commit()

    def expire_stale_sessions(self) -> int:
        """将超时的活跃会话标记为过期。返回处理数量。"""
        cutoff = (datetime.now(timezone.utc) - self._timeout).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._db.execute(
            """UPDATE sessions SET status = ?, expired_at = ?
               WHERE status = ? AND last_active_at < ?""",
            (SessionStatus.EXPIRED, now, SessionStatus.ACTIVE, cutoff),
        )
        self._db.commit()
        count = cursor.rowcount
        if count:
            logger.info("sessions_expired", count=count)
        return count

    def archive_session(self, session_id: str, *, summary: str = "") -> None:
        """归档会话（将摘要写入记录）。"""
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """UPDATE sessions SET status = ?, summary = ?, expired_at = COALESCE(expired_at, ?)
               WHERE id = ?""",
            (SessionStatus.ARCHIVED, summary, now, session_id),
        )
        self._db.commit()
        logger.info("session_archived", session_id=session_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """获取会话详情。"""
        row = self._db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, *, status: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """查询会话列表。"""
        query = "SELECT * FROM sessions WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY last_active_at DESC"
        rows = self._db.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def close_session(self, session_id: str, *, summary: str = "") -> None:
        """手动关闭会话。"""
        self.archive_session(session_id, summary=summary)
