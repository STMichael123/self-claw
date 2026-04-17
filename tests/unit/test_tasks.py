"""任务调度与自然语言解析测试。"""

from __future__ import annotations

import pytest

from src.services.task_service import describe_task_status, parse_schedule_text


class TestTaskScheduleParsing:
    def test_parse_once_after(self) -> None:
        parsed = parse_schedule_text("10分钟后")
        assert parsed["schedule_type"] == "once"
        assert parsed["schedule_expr"]

    def test_parse_interval(self) -> None:
        parsed = parse_schedule_text("每30分钟执行一次")
        assert parsed["schedule_type"] == "interval"
        assert parsed["schedule_expr"] == "1800"

    def test_parse_daily(self) -> None:
        parsed = parse_schedule_text("每天 09:30")
        assert parsed["schedule_type"] == "cron"
        assert parsed["schedule_expr"] == "30 9 * * *"

    def test_reject_unsupported_schedule(self) -> None:
        with pytest.raises(ValueError):
            parse_schedule_text("有空的时候执行")

    def test_reject_invalid_cron(self) -> None:
        with pytest.raises(ValueError):
            parse_schedule_text("61 25 * * *")

    def test_reject_invalid_daily_clock(self) -> None:
        with pytest.raises(ValueError):
            parse_schedule_text("每天 25:61")


class TestTaskStatusDescription:
    def test_status_description_with_pending_approval(self) -> None:
        text = describe_task_status({"status": "active", "last_result": {"pending_approval": {"tool_name": "exec"}}})
        assert "等待工具审批" in text