"""跨模块数据契约 — 对应 SPEC 8/9。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ── 枚举 ──────────────────────────────────────────────

class TaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ScheduleType(StrEnum):
    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class AgentRole(StrEnum):
    MAIN = "main"
    SUB = "sub"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SkillStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class MemoryScope(StrEnum):
    PRINCIPLE = "principle"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class VectorSourceType(StrEnum):
    PRINCIPLE_MEMORY = "principle_memory"
    SESSION_MESSAGE = "session_message"
    SESSION_SUMMARY = "session_summary"
    TASK_RESULT = "task_result"
    SKILL_OUTPUT = "skill_output"
    LONG_TERM_MEMORY = "long_term_memory"


# ── Agent 执行契约 ─────────────────────────────────────

class SubAgentRequest(BaseModel):
    """主 Agent -> 子 Agent 调用契约 — SPEC 9.2。"""

    run_id: str
    parent_run_id: str
    sub_agent_role: str
    goal: str
    allowed_skills: list[str] = Field(default_factory=list)
    context_pack: dict[str, Any] = Field(default_factory=dict)
    timeout_sec: int = 120


class SubAgentResponse(BaseModel):
    """子 Agent -> 主 Agent 回传契约 — SPEC 9.2。"""

    run_id: str
    status: RunStatus
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    usage: UsageInfo | None = None
    error: ErrorInfo | None = None


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0


class ErrorInfo(BaseModel):
    code: str
    message: str
    category: str = ""


# ── 消息渠道契约 — SPEC FR-007 ─────────────────────────

class InboundMessage(BaseModel):
    """统一入站消息。"""

    channel_type: str
    platform_uid: str
    message_type: str = "text"  # text|event|image|file
    content: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class OutboundMessage(BaseModel):
    """统一出站消息。"""

    channel_type: str
    target_uid: str
    format: str = "text"  # text|markdown|card
    content: str = ""
    reply_to: str | None = None


class SendResult(BaseModel):
    """渠道发送结果。"""

    success: bool
    message_id: str | None = None
    error: str | None = None


class UserIdentity(BaseModel):
    """统一用户身份。"""

    user_id: str
    display_name: str = ""
    channel_type: str = ""
    platform_uid: str = ""


# ── ReAct 步骤 ─────────────────────────────────────────

class ReActStep(BaseModel):
    """Agent 循环单步记录。"""

    step: int
    thinking: str = ""
    action: str = ""
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""


# 修复 SubAgentResponse 前向引用
SubAgentResponse.model_rebuild()
