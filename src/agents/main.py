"""主 Agent 编排 — 对应 SPEC FR-009 / FR-010。"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

import structlog

from src.agents.loop import AgentLoop, AgentResult
from src.agents.prompt import compose_system_prompt, build_messages
from src.agents.sub import SubAgentExecutor
from src.contracts.models import SubAgentRequest
from src.models.llm import ChatMessage, LLMAdapter

logger = structlog.get_logger()


class MainAgent:
    """主 Agent — 负责任务拆解、路由与子 Agent 调度。"""

    def __init__(
        self,
        llm: LLMAdapter,
        *,
        tools: dict[str, Any] | None = None,
        tool_executor: Any | None = None,
        max_steps: int = 10,
        hook_registry: Any | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools or {}
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.hook_registry = hook_registry
        self.sub_executor = SubAgentExecutor(llm, tool_executor=tool_executor)

    async def chat(
        self,
        user_message: str,
        *,
        history: list[ChatMessage] | None = None,
        available_skills_catalog: list[dict[str, Any]] | None = None,
        activated_skills: list[dict[str, Any]] | None = None,
        principle: str = "",
        long_term_context: str = "",
        short_term_context: str = "",
        memory_context: str = "",
        run_id: str | None = None,
        cancellation_checker: Callable[[], bool] | None = None,
        cancellation_waiter: Callable[[], Awaitable[None]] | None = None,
        approval_requester: Callable[[str, dict[str, Any], Any], Awaitable[dict[str, Any]]] | None = None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        resume_state: dict[str, Any] | None = None,
        approved_approval: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """处理用户消息并返回结果。"""
        run_id = run_id or str(uuid.uuid4())

        system_prompt = compose_system_prompt(
            principle=principle,
            long_term_context=long_term_context,
            short_term_context=short_term_context or memory_context,
            available_skills_catalog=available_skills_catalog,
            activated_skills=activated_skills,
        )

        messages = build_messages(
            system_prompt=system_prompt,
            history=history,
            user_message=user_message,
        )

        loop = AgentLoop(
            self.llm,
            tools=self.tools,
            tool_executor=self.tool_executor,
            max_steps=self.max_steps,
            cancellation_checker=cancellation_checker,
            cancellation_waiter=cancellation_waiter,
            approval_requester=approval_requester,
            hook_registry=self.hook_registry,
        )

        return await loop.run(
            system_prompt=system_prompt,
            messages=messages,
            run_id=run_id,
            event_callback=event_callback,
            resume_state=resume_state,
            approved_approval=approved_approval,
            runtime_context=runtime_context,
        )
