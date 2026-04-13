"""主 Agent 编排 — 对应 SPEC FR-009 / FR-010。"""

from __future__ import annotations

import uuid
from typing import Any

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
    ) -> None:
        self.llm = llm
        self.tools = tools or {}
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.sub_executor = SubAgentExecutor(llm, tool_executor=tool_executor)

    async def chat(
        self,
        user_message: str,
        *,
        history: list[ChatMessage] | None = None,
        skill_prompt: str | None = None,
        memory_context: str = "",
        run_id: str | None = None,
    ) -> AgentResult:
        """处理用户消息并返回结果。"""
        run_id = run_id or str(uuid.uuid4())

        system_prompt = compose_system_prompt(
            skill_prompt=skill_prompt,
            memory_context=memory_context,
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
        )

        return await loop.run(
            system_prompt=system_prompt,
            messages=messages,
            run_id=run_id,
        )
