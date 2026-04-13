"""子 Agent 执行器 — 对应 SPEC FR-009。"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from src.contracts.errors import ErrorCode
from src.contracts.models import (
    ErrorInfo,
    RunStatus,
    SubAgentRequest,
    SubAgentResponse,
    UsageInfo,
)
from src.agents.loop import AgentLoop, AgentResult
from src.agents.prompt import compose_system_prompt
from src.models.llm import ChatMessage, LLMAdapter

logger = structlog.get_logger()


class SubAgentExecutor:
    """管理子 Agent 的创建与执行 — 上下文严格隔离。"""

    def __init__(self, llm: LLMAdapter, *, tool_executor: Any | None = None) -> None:
        self.llm = llm
        self.tool_executor = tool_executor

    async def run(self, request: SubAgentRequest) -> SubAgentResponse:
        """执行子 Agent 任务，返回结构化结果。"""
        log = logger.bind(run_id=request.run_id, parent_run_id=request.parent_run_id)
        log.info("sub_agent_start", role=request.sub_agent_role, goal=request.goal)

        system_prompt = compose_system_prompt(
            base_prompt=f"你是一个子 Agent，角色为 {request.sub_agent_role}。\n你的目标: {request.goal}\n"
                        f"以下是你需要的上下文:\n{_format_context(request.context_pack)}",
        )

        loop = AgentLoop(
            self.llm,
            tool_executor=self.tool_executor,
            max_steps=10,
        )

        try:
            result = await loop.run(
                system_prompt=system_prompt,
                messages=[ChatMessage(role="user", content=request.goal)],
                run_id=request.run_id,
            )
        except Exception as exc:
            log.error("sub_agent_failed", error=str(exc))
            return SubAgentResponse(
                run_id=request.run_id,
                status=RunStatus.FAILED,
                error=ErrorInfo(
                    code=ErrorCode.SUBAGENT_TIMEOUT,
                    message=str(exc),
                    category="execution",
                ),
            )

        status = result.status
        return SubAgentResponse(
            run_id=request.run_id,
            status=status,
            output={"reply": result.reply, "steps_count": len(result.steps)},
            usage=UsageInfo(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ),
        )


def _format_context(context_pack: dict[str, Any]) -> str:
    """将 context_pack 格式化为可注入提示词的文本。"""
    if not context_pack:
        return "(无附加上下文)"
    parts = []
    for k, v in context_pack.items():
        parts.append(f"- {k}: {v}")
    return "\n".join(parts)
