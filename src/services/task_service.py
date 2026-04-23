"""任务调度主链路服务。"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.triggers.cron import CronTrigger
import structlog

from src.contracts.models import RunStatus, TaskStatus
from src.services.agent_service import DEFAULT_WEB_USER_ID, AgentService
from src.services.scheduler import SchedulerService

logger = structlog.get_logger()


class TaskService:
    """封装任务 CRUD、自然语言调度解析与执行。"""

    def __init__(self, db: sqlite3.Connection, *, scheduler: SchedulerService, agent_service: AgentService) -> None:
        self._db = db
        self.scheduler = scheduler
        self.agent_service = agent_service

    def bootstrap(self) -> None:
        rows = self._db.execute("SELECT * FROM tasks WHERE status IN (?, ?)", (TaskStatus.ACTIVE.value, TaskStatus.PAUSED.value)).fetchall()
        for row in rows:
            task = self._row_to_task(row)
            try:
                self._schedule_task(task)
                if task["status"] == TaskStatus.PAUSED.value:
                    self.scheduler.pause_task(task["id"])
            except Exception as exc:
                logger.warning("task_bootstrap_schedule_failed", task_id=task["id"], error=str(exc))
                self._update_task(
                    task["id"],
                    status=TaskStatus.PAUSED.value,
                    next_run_at=None,
                    last_result={
                        "status": RunStatus.FAILED.value,
                        "error": str(exc),
                    },
                )

    def list_tasks(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self._db.execute(query, params).fetchall()
        return [self._serialize_task(self._row_to_task(row)) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._serialize_task(self._row_to_task(row), include_history=True)

    def parse_schedule(self, schedule_text: str) -> dict[str, Any]:
        """解析调度文本并返回预览结果，不创建任务。"""
        parsed = parse_schedule_text(schedule_text)
        self.scheduler.validate_schedule(
            schedule_type=str(parsed["schedule_type"]),
            schedule_expr=str(parsed["schedule_expr"]),
        )
        return {
            "schedule_text": schedule_text,
            "schedule_type": parsed["schedule_type"],
            "schedule_expr": parsed["schedule_expr"],
            "next_run_at": parsed["next_run_at"],
            "human_schedule": describe_schedule(parsed["schedule_type"], parsed["schedule_expr"]),
        }

    def create_task(
        self,
        *,
        title: str,
        prompt: str,
        schedule_text: str,
        requested_skill_name: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_schedule_text(schedule_text)
        self.scheduler.validate_schedule(
            schedule_type=str(parsed["schedule_type"]),
            schedule_expr=str(parsed["schedule_expr"]),
        )
        task_id = str(uuid.uuid4())
        now = _utcnow()
        task = {
            "id": task_id,
            "title": title,
            "prompt": prompt,
            "skill_id": None,
            "requested_skill_name": requested_skill_name,
            "session_id": None,
            "schedule_type": parsed["schedule_type"],
            "schedule_expr": parsed["schedule_expr"],
            "schedule_text": schedule_text,
            "status": TaskStatus.ACTIVE.value,
            "next_run_at": parsed["next_run_at"],
            "last_run_at": None,
            "last_result": {},
            "created_at": now,
            "updated_at": now,
        }
        try:
            self._db.execute("BEGIN")
            self._db.execute(
                """
                INSERT INTO tasks (
                    id, title, prompt, skill_id, requested_skill_name, session_id, schedule_type, schedule_expr, schedule_text,
                    status, next_run_at, last_run_at, last_result, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title,
                    prompt,
                    None,
                    requested_skill_name,
                    None,
                    parsed["schedule_type"],
                    parsed["schedule_expr"],
                    schedule_text,
                    TaskStatus.ACTIVE.value,
                    parsed["next_run_at"],
                    None,
                    None,
                    now,
                    now,
                ),
            )
            self._schedule_task(task, commit=False)
            self._db.commit()
        except Exception:
            self._db.rollback()
            if self.scheduler.get_task(task_id) is not None:
                self.scheduler.remove_task(task_id)
            raise

        task = self.get_task(task_id)
        if task is None:
            raise ValueError("task create failed")
        return task

    def pause_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        self.scheduler.pause_task(task_id)
        self._update_task(task_id, status=TaskStatus.PAUSED.value, next_run_at=self.scheduler.get_next_run_at(task_id))
        return self.get_task(task_id)

    def resume_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        self.scheduler.resume_task(task_id)
        self._update_task(task_id, status=TaskStatus.ACTIVE.value, next_run_at=self.scheduler.get_next_run_at(task_id))
        return self.get_task(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        if self.scheduler.get_task(task_id) is not None:
            self.scheduler.remove_task(task_id)
        self._update_task(task_id, status=TaskStatus.CANCELLED.value, next_run_at=None)
        return self.get_task(task_id)

    async def run_task_now(self, task_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        asyncio.create_task(self._execute_task(task_id))
        return self.get_task(task_id)

    async def _execute_task(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task is None:
            return

        run_log_id = str(uuid.uuid4())
        started_at = _utcnow()
        self._db.execute(
            "INSERT INTO run_logs (id, task_id, started_at, ended_at, status, error_category, error_detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_log_id, task_id, started_at, None, "running", None, None),
        )
        self._db.commit()

        try:
            result = await self.agent_service.chat(
                message=task.get("prompt") or task["title"],
                task_mode="continue" if task.get("session_id") else "new_task",
                session_id=task.get("session_id") or None,
                session_title=task["title"],
                user_id=DEFAULT_WEB_USER_ID,
                requested_skill_name=task.get("requested_skill_name") or task.get("skill_id") or None,
                task_id=task_id,
                task_run_log_id=run_log_id,
            )
            status = str(result.get("status") or RunStatus.SUCCESS.value)
            finished_at = _utcnow()
            next_run_at = self.scheduler.get_next_run_at(task_id)
            last_result = {
                "reply": result.get("reply", ""),
                "run_id": result.get("run_id"),
                "status": status,
            }
            if result.get("pending_approval"):
                last_result["pending_approval"] = result.get("pending_approval")

            task_fields: dict[str, Any] = {
                "session_id": result.get("session_id") or task.get("session_id"),
                "last_run_at": finished_at,
                "last_result": last_result,
            }
            if result.get("pending_approval"):
                task_fields["next_run_at"] = next_run_at
            elif task.get("schedule_type") == "once":
                task_fields["status"] = TaskStatus.COMPLETED.value
                task_fields["next_run_at"] = None
            else:
                task_fields["next_run_at"] = next_run_at

            self._update_task(task_id, **task_fields)

            run_log_error_category = None
            run_log_error_detail = None
            ended_at = finished_at
            if result.get("pending_approval"):
                ended_at = None
            elif status in {RunStatus.FAILED.value, RunStatus.TIMEOUT.value}:
                run_log_error_category = "task_execution"
                run_log_error_detail = result.get("reply", "")
            elif status == RunStatus.CANCELLED.value:
                run_log_error_category = "task_cancelled"
                run_log_error_detail = result.get("reply", "")

            self._db.execute(
                "UPDATE run_logs SET ended_at = ?, status = ?, error_category = ?, error_detail = ? WHERE id = ?",
                (
                    ended_at,
                    status,
                    run_log_error_category,
                    run_log_error_detail,
                    run_log_id,
                ),
            )
            self._db.commit()

            if status in {RunStatus.FAILED.value, RunStatus.TIMEOUT.value}:
                await self.agent_service._notify_task_failure(
                    task_id=task_id,
                    session_id=result.get("session_id") or task.get("session_id") or "-",
                    reply=result.get("reply", ""),
                )
        except Exception as exc:
            finished_at = _utcnow()
            task_fields = {
                "last_run_at": finished_at,
                "next_run_at": self.scheduler.get_next_run_at(task_id),
                "last_result": {
                    "status": RunStatus.FAILED.value,
                    "error": str(exc),
                },
            }
            if task.get("schedule_type") == "once":
                task_fields["status"] = TaskStatus.COMPLETED.value
            self._update_task(
                task_id,
                **task_fields,
            )
            self._db.execute(
                "UPDATE run_logs SET ended_at = ?, status = ?, error_category = ?, error_detail = ? WHERE id = ?",
                (finished_at, RunStatus.FAILED.value, "task_execution", str(exc), run_log_id),
            )
            self._db.commit()
            await self.agent_service._notify_task_failure(
                task_id=task_id,
                session_id=task.get("session_id") or "-",
                reply=str(exc),
            )
            logger.error("task_execution_failed", task_id=task_id, error=str(exc))

    def _schedule_task(self, task: dict[str, Any], *, commit: bool = True) -> None:
        if task["status"] == TaskStatus.CANCELLED.value:
            return
        self.scheduler.add_task(
            task["id"],
            self._execute_task,
            schedule_type=task["schedule_type"],
            schedule_expr=task["schedule_expr"],
            task_id=task["id"],
        )
        self._update_task(task["id"], next_run_at=self.scheduler.get_next_run_at(task["id"]), commit=commit)

    def _update_task(self, task_id: str, *, commit: bool = True, **fields: Any) -> None:
        if not fields:
            return
        updates = []
        values: list[Any] = []
        for key, value in fields.items():
            updates.append(f"{key} = ?")
            if key == "last_result" and isinstance(value, dict):
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                values.append(value)
        updates.append("updated_at = ?")
        values.append(_utcnow())
        values.append(task_id)
        self._db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
        if commit:
            self._db.commit()

    def _serialize_task(self, task: dict[str, Any], *, include_history: bool = False) -> dict[str, Any]:
        payload = {
            **task,
            "human_schedule": task.get("schedule_text") or describe_schedule(task["schedule_type"], task["schedule_expr"]),
            "status_text": describe_task_status(task),
            "cost": self.agent_service.cost.get_task_summary(task["id"]),
        }
        if include_history:
            rows = self._db.execute(
                "SELECT * FROM run_logs WHERE task_id = ? ORDER BY started_at DESC LIMIT 20",
                (task["id"],),
            ).fetchall()
            payload["run_history"] = [dict(row) for row in rows]
        return payload

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["last_result"] = _from_json(item.get("last_result"), default={})
        return item


def parse_schedule_text(text: str) -> dict[str, str | None]:
    raw = text.strip()
    now = datetime.now(timezone.utc)

    if re.fullmatch(r"\S+\s+\S+\s+\S+\s+\S+\s+\S+", raw):
        _validate_cron_expr(raw)
        return {
            "schedule_type": "cron",
            "schedule_expr": raw,
            "next_run_at": None,
        }

    after_match = re.fullmatch(r"(\d+)\s*(秒|分钟|小时|天)后", raw)
    if after_match:
        amount = int(after_match.group(1))
        delta = _to_delta(amount, after_match.group(2))
        run_at = now + delta
        return {
            "schedule_type": "once",
            "schedule_expr": run_at.replace(microsecond=0).isoformat(),
            "next_run_at": run_at.replace(microsecond=0).isoformat(),
        }

    every_match = re.fullmatch(r"每(\d+)\s*(秒|分钟|小时|天)(执行)?一次", raw)
    if every_match:
        amount = int(every_match.group(1))
        seconds = int(_to_delta(amount, every_match.group(2)).total_seconds())
        return {
            "schedule_type": "interval",
            "schedule_expr": str(seconds),
            "next_run_at": (now + timedelta(seconds=seconds)).replace(microsecond=0).isoformat(),
        }

    if raw == "每小时":
        return {
            "schedule_type": "interval",
            "schedule_expr": "3600",
            "next_run_at": (now + timedelta(hours=1)).replace(microsecond=0).isoformat(),
        }

    day_match = re.fullmatch(r"每天\s*(\d{1,2})(?:[:：点](\d{1,2}))?", raw)
    if day_match:
        hour = int(day_match.group(1))
        minute = int(day_match.group(2) or 0)
        _validate_clock(hour, minute)
        return {
            "schedule_type": "cron",
            "schedule_expr": f"{minute} {hour} * * *",
            "next_run_at": None,
        }

    tomorrow_match = re.fullmatch(r"明天\s*(\d{1,2})[:：](\d{1,2})", raw)
    if tomorrow_match:
        hour = int(tomorrow_match.group(1))
        minute = int(tomorrow_match.group(2))
        _validate_clock(hour, minute)
        run_at = (now + timedelta(days=1)).astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return {
            "schedule_type": "once",
            "schedule_expr": run_at.isoformat(),
            "next_run_at": run_at.isoformat(),
        }

    raise ValueError("暂不支持该调度描述，支持示例：10分钟后、每30分钟执行一次、每天 09:30、明天 18:00、*/5 * * * *")


def describe_schedule(schedule_type: str, schedule_expr: str) -> str:
    if schedule_type == "interval":
        seconds = int(schedule_expr)
        if seconds % 3600 == 0:
            return f"每 {seconds // 3600} 小时执行一次"
        if seconds % 60 == 0:
            return f"每 {seconds // 60} 分钟执行一次"
        return f"每 {seconds} 秒执行一次"
    if schedule_type == "once":
        return f"在 {schedule_expr} 执行一次"
    return f"Cron: {schedule_expr}"


def describe_task_status(task: dict[str, Any]) -> str:
    mapping = {
        TaskStatus.ACTIVE.value: "已启用",
        TaskStatus.PAUSED.value: "已暂停",
        TaskStatus.CANCELLED.value: "已取消",
        TaskStatus.COMPLETED.value: "已完成",
    }
    base = mapping.get(task.get("status") or "", task.get("status") or "未知")
    last_result = task.get("last_result") or {}
    pending = last_result.get("pending_approval")
    if pending:
        return f"{base}，等待工具审批"
    last_status = last_result.get("status")
    if last_status == RunStatus.CANCELLED.value:
        return f"{base}，最近一次运行已取消"
    if last_status == RunStatus.FAILED.value:
        return f"{base}，最近一次运行失败"
    if last_status == RunStatus.TIMEOUT.value:
        return f"{base}，最近一次运行超时"
    return base


def _validate_cron_expr(expr: str) -> None:
    CronTrigger.from_crontab(expr)


def _validate_clock(hour: int, minute: int) -> None:
    if not 0 <= hour <= 23:
        raise ValueError("小时必须在 0-23 之间")
    if not 0 <= minute <= 59:
        raise ValueError("分钟必须在 0-59 之间")


def _to_delta(amount: int, unit: str) -> timedelta:
    if unit == "秒":
        return timedelta(seconds=amount)
    if unit == "分钟":
        return timedelta(minutes=amount)
    if unit == "小时":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default