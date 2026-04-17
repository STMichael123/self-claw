"""调度服务 — 对应 SPEC FR-001 / §18 src/services/scheduler。"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

logger = structlog.get_logger()


class SchedulerService:
    """任务调度服务 — 封装 APScheduler。"""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("scheduler_started")

    def shutdown(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("scheduler_shutdown")

    def add_task(
        self,
        job_id: str,
        func: Callable[..., Awaitable[Any]],
        *,
        schedule_type: str,
        schedule_expr: str,
        **kwargs: Any,
    ) -> None:
        """添加调度任务。"""
        trigger = self._build_trigger(schedule_type, schedule_expr)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs=kwargs,
        )
        logger.info("task_added", task_id=job_id, schedule_type=schedule_type, schedule_expr=schedule_expr)

    def validate_schedule(self, *, schedule_type: str, schedule_expr: str) -> None:
        """校验调度表达式是否合法。"""
        self._build_trigger(schedule_type, schedule_expr)

    def pause_task(self, task_id: str) -> None:
        self._scheduler.pause_job(task_id)
        logger.info("task_paused", task_id=task_id)

    def resume_task(self, task_id: str) -> None:
        self._scheduler.resume_job(task_id)
        logger.info("task_resumed", task_id=task_id)

    def remove_task(self, task_id: str) -> None:
        self._scheduler.remove_job(task_id)
        logger.info("task_removed", task_id=task_id)

    def get_task(self, task_id: str) -> Any | None:
        return self._scheduler.get_job(task_id)

    def get_next_run_at(self, task_id: str) -> str | None:
        job = self.get_task(task_id)
        if job is None or job.next_run_time is None:
            return None
        return job.next_run_time.isoformat()

    @staticmethod
    def _build_trigger(schedule_type: str, schedule_expr: str) -> Any:
        if schedule_type == "cron":
            return CronTrigger.from_crontab(schedule_expr)
        elif schedule_type == "interval":
            return IntervalTrigger(seconds=int(schedule_expr))
        elif schedule_type == "once":
            from datetime import datetime
            return DateTrigger(run_date=datetime.fromisoformat(schedule_expr))
        else:
            raise ValueError(f"Unknown schedule_type: {schedule_type}")
