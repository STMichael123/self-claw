"""会话生命周期管理 — 对应 SPEC FR-013。"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

from src.contracts.models import RunStatus, SessionStatus

logger = structlog.get_logger()

DEFAULT_SESSION_TIMEOUT_MIN = 30
MAX_ACTIVE_TOP_LEVEL_SESSIONS = 5
AGENT_TITLE_PATTERN = re.compile(r"^Agent (\d+)$")


class SessionManager:
    """管理多轮对话的会话生命周期与上下文。"""

    def __init__(self, db: sqlite3.Connection, *, timeout_min: int = DEFAULT_SESSION_TIMEOUT_MIN) -> None:
        self._db = db
        self._timeout = timedelta(minutes=timeout_min)

    def create_session(self, user_id: str, *, title: str | None = None, channel_type: str = "web") -> str:
        """创建新会话。"""
        if self.count_active_sessions(user_id) >= MAX_ACTIVE_TOP_LEVEL_SESSIONS:
            raise ValueError(f"最多只能保留 {MAX_ACTIVE_TOP_LEVEL_SESSIONS} 个顶层会话，请先删除一个现有会话")
        session_id = str(uuid.uuid4())
        now = _utcnow()
        title = (title or "").strip() or self._next_session_title(user_id)
        self._db.execute(
            """INSERT INTO sessions (id, title, user_id, channel_type, status, created_at, last_active_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, title, user_id, channel_type, SessionStatus.ACTIVE, now, now),
        )
        self._db.commit()
        logger.info("session_created", session_id=session_id, user_id=user_id, title=title)
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
        now = _utcnow()
        self._db.execute(
            "UPDATE sessions SET last_active_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._db.commit()

    def set_current_run(self, session_id: str, run_id: str | None) -> None:
        """更新会话当前顶层运行指针。"""
        if run_id is None:
            self._db.execute(
                "UPDATE sessions SET current_run_id = ?, last_active_at = ? WHERE id = ?",
                (run_id, _utcnow(), session_id),
            )
        else:
            self._db.execute(
                """
                UPDATE sessions
                SET current_run_id = ?, last_active_at = ?
                WHERE id = ?
                  AND EXISTS (
                      SELECT 1 FROM agent_runs
                      WHERE id = ? AND status != ?
                  )
                """,
                (run_id, _utcnow(), session_id, run_id, RunStatus.CANCELLED.value),
            )
        self._db.commit()

    def update_title(self, session_id: str, title: str) -> None:
        """更新会话标题。"""
        self._db.execute(
            "UPDATE sessions SET title = ?, last_active_at = ? WHERE id = ?",
            (title.strip(), _utcnow(), session_id),
        )
        self._db.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        guard_run_not_cancelled: bool = False,
    ) -> str:
        """向会话写入一条消息。"""
        message_id = str(uuid.uuid4())
        now = _utcnow()
        if guard_run_not_cancelled and run_id:
            cursor = self._db.execute(
                """
                INSERT INTO messages (id, session_id, role, content, run_id, metadata, created_at)
                SELECT ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM agent_runs
                    WHERE id = ? AND status != ?
                )
                """,
                (message_id, session_id, role, content, run_id, _to_json(metadata), now, run_id, RunStatus.CANCELLED.value),
            )
            if cursor.rowcount:
                self._db.execute(
                    "UPDATE sessions SET last_active_at = ? WHERE id = ?",
                    (now, session_id),
                )
        else:
            self._db.execute(
                """INSERT INTO messages (id, session_id, role, content, run_id, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (message_id, session_id, role, content, run_id, _to_json(metadata), now),
            )
            self._db.execute(
                "UPDATE sessions SET last_active_at = ? WHERE id = ?",
                (now, session_id),
            )
        self._db.commit()
        if guard_run_not_cancelled and run_id:
            inserted = self._db.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone()
            if inserted is None:
                return ""
        self._refresh_context_snapshot(session_id)
        return message_id

    def list_messages(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """查询会话消息历史。"""
        query = "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC"
        params: list[Any] = [session_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._db.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _from_json(item.get("metadata"), default={})
            result.append(item)
        return result

    def expire_stale_sessions(self) -> int:
        """将超时的活跃会话标记为过期。返回处理数量。"""
        cutoff = (datetime.now(timezone.utc) - self._timeout).isoformat()
        now = _utcnow()
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
        now = _utcnow()
        final_summary = summary.strip() or self.generate_summary(session_id)
        self._db.execute(
            """UPDATE sessions SET status = ?, summary = ?, current_run_id = NULL, expired_at = COALESCE(expired_at, ?)
               WHERE id = ?""",
            (SessionStatus.ARCHIVED, final_summary, now, session_id),
        )
        self._db.commit()
        logger.info("session_archived", session_id=session_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """获取会话详情。"""
        row = self._db.execute(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                   (SELECT status FROM agent_runs ar WHERE ar.id = s.current_run_id) AS current_run_status,
                   (SELECT COUNT(*) FROM agent_runs ar WHERE ar.session_id = s.id AND ar.agent_role = 'sub' AND ar.status = 'running') AS active_child_runs
            FROM sessions s
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, *, status: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """查询会话列表。"""
        query = """
            SELECT s.*,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                   (SELECT status FROM agent_runs ar WHERE ar.id = s.current_run_id) AS current_run_status,
                   (SELECT COUNT(*) FROM agent_runs ar WHERE ar.session_id = s.id AND ar.agent_role = 'sub' AND ar.status = 'running') AS active_child_runs
            FROM sessions s
            WHERE 1=1
        """
        params: list[Any] = []
        if status:
            query += " AND s.status = ?"
            params.append(status)
        if user_id:
            query += " AND s.user_id = ?"
            params.append(user_id)
        query += " ORDER BY s.last_active_at DESC"
        rows = self._db.execute(query, params).fetchall()
        return [self._row_to_session(row) for row in rows]

    def count_active_sessions(self, user_id: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) AS total FROM sessions WHERE user_id = ? AND status = ?",
            (user_id, SessionStatus.ACTIVE),
        ).fetchone()
        return int(row["total"]) if row else 0

    def close_session(self, session_id: str, *, summary: str = "") -> None:
        """手动关闭会话。"""
        self.archive_session(session_id, summary=summary)

    def generate_summary(self, session_id: str) -> str:
        """基于历史消息生成轻量摘要。"""
        session = self.get_session(session_id)
        if session is None:
            return ""

        messages = self.list_messages(session_id)
        if not messages:
            return session.get("title") or "空会话"

        preview = []
        for item in messages[-3:]:
            role = "用户" if item["role"] == "user" else "Agent"
            preview.append(f"{role}:{item['content'][:30]}")
        return " | ".join(preview)

    def _refresh_context_snapshot(self, session_id: str) -> None:
        """当消息较多时生成轻量上下文快照。"""
        rows = self._db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        if len(rows) < 12:
            return

        tail = rows[-8:]
        snapshot = "\n".join(f"{row['role']}: {row['content'][:120]}" for row in tail)
        self._db.execute(
            "UPDATE sessions SET context_snapshot = ? WHERE id = ?",
            (snapshot, session_id),
        )
        self._db.commit()

    def _next_session_title(self, user_id: str) -> str:
        rows = self._db.execute(
            "SELECT title FROM sessions WHERE user_id = ? AND status = ? ORDER BY last_active_at DESC",
            (user_id, SessionStatus.ACTIVE),
        ).fetchall()
        used_slots = {
            int(match.group(1))
            for row in rows
            if (match := AGENT_TITLE_PATTERN.match((row["title"] or "").strip()))
        }
        for slot in range(1, MAX_ACTIVE_TOP_LEVEL_SESSIONS + 1):
            if slot not in used_slots:
                return f"Agent {slot}"
        return f"Agent {len(used_slots) + 1}"

    def _row_to_session(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["message_count"] = int(item.get("message_count") or 0)
        item["active_child_runs"] = int(item.get("active_child_runs") or 0)
        return item


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _from_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default
