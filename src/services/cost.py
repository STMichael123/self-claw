"""成本追踪服务 — 对应 SPEC FR-004。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


class CostService:
    """Token 用量与成本统计。"""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def record_usage(
        self,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
        model_name: str = "",
        commit: bool = True,
    ) -> str:
        """记录一次模型调用的用量。"""
        log_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """INSERT INTO usage_logs
               (id, task_id, session_id, agent_run_id, input_tokens, output_tokens, estimated_cost, model_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (log_id, task_id, session_id, agent_run_id, input_tokens, output_tokens, estimated_cost, model_name, now),
        )
        if commit:
            self._db.commit()
        return log_id

    def get_daily_summary(self, date: str | None = None) -> dict[str, Any]:
        """按天聚合统计。"""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._db.execute(
            """SELECT
                 COALESCE(SUM(input_tokens), 0) as total_input,
                 COALESCE(SUM(output_tokens), 0) as total_output,
                 COALESCE(SUM(estimated_cost), 0) as total_cost,
                 COUNT(*) as call_count
               FROM usage_logs
               WHERE created_at LIKE ?""",
            (f"{date}%",),
        ).fetchone()
        return {
            "date": date,
            "total_input_tokens": row["total_input"],
            "total_output_tokens": row["total_output"],
            "total_cost": row["total_cost"],
            "call_count": row["call_count"],
        }

    def get_task_summary(self, task_id: str) -> dict[str, Any]:
        """按任务聚合统计。"""
        row = self._db.execute(
            """SELECT
                 COALESCE(SUM(input_tokens), 0) as total_input,
                 COALESCE(SUM(output_tokens), 0) as total_output,
                 COALESCE(SUM(estimated_cost), 0) as total_cost,
                 COUNT(*) as call_count
               FROM usage_logs
               WHERE task_id = ?""",
            (task_id,),
        ).fetchone()
        return {
            "task_id": task_id,
            "total_input_tokens": row["total_input"],
            "total_output_tokens": row["total_output"],
            "total_cost": row["total_cost"],
            "call_count": row["call_count"],
        }
