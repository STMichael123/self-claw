"""主 Agent 编排 — 对应 SPEC FR-009 / FR-010。"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

import structlog

from src.agents.loop import AgentLoop, AgentResult
from src.agents.prompt import compose_system_prompt, build_messages
from src.agents.sub import SubAgentExecutor
from src.contracts.models import SubAgentRequest
from src.models.llm import ChatMessage, LLMAdapter, LLMResponse

logger = structlog.get_logger()


class RouteDecision:
    def __init__(
        self,
        *,
        should_delegate: bool,
        route_input_tokens: int = 0,
        route_output_tokens: int = 0,
        prefetched_response: LLMResponse | None = None,
    ) -> None:
        self.should_delegate = should_delegate
        self.route_input_tokens = route_input_tokens
        self.route_output_tokens = route_output_tokens
        self.prefetched_response = prefetched_response


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
        sub_agent_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        sub_agent_fallback_keywords: list[str] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """处理用户消息并返回结果。"""
        run_id = run_id or str(uuid.uuid4())
        route_input_tokens = 0
        route_output_tokens = 0
        route_decision: RouteDecision | None = None
        prefetched_response: LLMResponse | None = None

        if not activated_skills:
            route_decision = await self._should_delegate_sub_agent(
                user_message,
                fallback_keywords=sub_agent_fallback_keywords or [],
            )
        if route_decision is not None:
            route_input_tokens += route_decision.route_input_tokens
            route_output_tokens += route_decision.route_output_tokens
            prefetched_response = route_decision.prefetched_response

        if route_decision is not None and route_decision.should_delegate and sub_agent_runner is not None:
            child_run = await sub_agent_runner(
                parent_run_id=run_id,
                goal=user_message,
                session_title=str((runtime_context or {}).get("session_title") or ""),
                sub_agent_role="analyst",
                allowed_skills=[],
                context_pack={
                    "session_title": str((runtime_context or {}).get("session_title") or "未命名会话"),
                },
            )
            child_reply = _extract_child_reply(child_run)
            if child_reply:
                short_term_context = f"{short_term_context}\n子 Agent 结果:\n{child_reply}".strip()

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

        result = await loop.run(
            system_prompt=system_prompt,
            messages=messages,
            run_id=run_id,
            event_callback=event_callback,
            resume_state=resume_state,
            approved_approval=approved_approval,
            runtime_context=runtime_context,
            prefetched_response=prefetched_response,
        )
        result.input_tokens += route_input_tokens
        result.output_tokens += route_output_tokens
        return result

    async def _should_delegate_sub_agent(
        self,
        user_message: str,
        *,
        fallback_keywords: list[str],
    ) -> RouteDecision | None:
        matched_keywords = [keyword for keyword in fallback_keywords if keyword in user_message]
        route_messages = [
            ChatMessage(
                role="system",
                content=(
                    "你是主 Agent 的路由器。"
                    "如果当前请求明显需要先做分析、调研、比较、拆解或汇总，再回答主问题，"
                    "只输出 DELEGATE；否则只输出 DIRECT。"
                ),
            ),
            ChatMessage(role="user", content=user_message),
        ]
        try:
            response = await self.llm.chat(route_messages, tools=None, temperature=0.0, max_tokens=8)
        except Exception:
            logger.warning("sub_agent_route_llm_failed", run_id=None)
            logger.info("sub_agent_route_decided", route="rule_fallback")
            return RouteDecision(should_delegate=bool(matched_keywords))

        decision = str(response.content or "").strip().upper()
        if "DELEGATE" in decision:
            logger.info("sub_agent_route_decided", route="llm_delegate")
            return RouteDecision(
                should_delegate=True,
                route_input_tokens=response.input_tokens,
                route_output_tokens=response.output_tokens,
            )
        if "DIRECT" in decision:
            logger.info("sub_agent_route_decided", route="llm_direct")
            return RouteDecision(
                should_delegate=False,
                route_input_tokens=response.input_tokens,
                route_output_tokens=response.output_tokens,
            )

        if matched_keywords:
            logger.info("sub_agent_route_decided", route="rule_fallback")
            return RouteDecision(
                should_delegate=True,
                route_input_tokens=response.input_tokens,
                route_output_tokens=response.output_tokens,
            )

        logger.info("sub_agent_route_decided", route="llm_passthrough")
        return RouteDecision(
            should_delegate=False,
            prefetched_response=response,
        )


def _extract_child_reply(child_run: dict[str, Any]) -> str:
    result_ref = child_run.get("result_ref") or {}
    output = result_ref.get("output") or {}
    reply = result_ref.get("reply") or output.get("reply") or ""
    return str(reply).strip()
