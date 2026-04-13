"""Agent 核心执行循环（ReAct） — 对应 SPEC FR-010。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import structlog

from src.contracts.errors import ErrorCode
from src.contracts.models import ReActStep, RunStatus
from src.models.llm import ChatMessage, LLMAdapter, LLMResponse
from src.agents.prompt import build_messages, compose_system_prompt

logger = structlog.get_logger()

DEFAULT_MAX_STEPS = 10


class AgentLoop:
    """ReAct 推理-执行循环。"""

    def __init__(
        self,
        llm: LLMAdapter,
        *,
        tools: dict[str, Any] | None = None,
        tool_executor: Any | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.llm = llm
        self.tools = tools or {}  # name -> tool_descriptor
        self.tool_executor = tool_executor
        self.max_steps = max_steps

    async def run(
        self,
        *,
        system_prompt: str,
        messages: list[ChatMessage],
        run_id: str | None = None,
    ) -> AgentResult:
        """执行完整的 ReAct 循环（阻塞模式）。"""
        run_id = run_id or str(uuid.uuid4())
        steps: list[ReActStep] = []
        total_input = 0
        total_output = 0
        current_messages = list(messages)

        tool_defs = self._build_tool_defs() if self.tools else None

        for step_num in range(1, self.max_steps + 1):
            log = logger.bind(run_id=run_id, step=step_num)

            resp = await self.llm.chat(current_messages, tools=tool_defs)
            total_input += resp.input_tokens
            total_output += resp.output_tokens

            # 无工具调用 → 直接回复，结束循环
            if not resp.tool_calls:
                steps.append(ReActStep(step=step_num, thinking=resp.content))
                log.info("agent_loop_reply", content_length=len(resp.content))
                return AgentResult(
                    run_id=run_id,
                    reply=resp.content,
                    steps=steps,
                    status=RunStatus.SUCCESS,
                    input_tokens=total_input,
                    output_tokens=total_output,
                )

            # 有工具调用 → 执行工具
            current_messages.append(
                ChatMessage(role="assistant", content=resp.content, tool_calls=[
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in resp.tool_calls
                ])
            )

            for tc in resp.tool_calls:
                observation = await self._execute_tool(tc.name, tc.arguments, run_id=run_id)
                steps.append(ReActStep(
                    step=step_num,
                    thinking=resp.content,
                    action=tc.name,
                    action_input=json.loads(tc.arguments) if tc.arguments else {},
                    observation=observation,
                ))
                current_messages.append(
                    ChatMessage(role="tool", content=observation, tool_call_id=tc.id)
                )
                log.info("tool_call", tool=tc.name, observation_length=len(observation))

        # 超过最大步数
        logger.warning("max_steps_exceeded", run_id=run_id, max_steps=self.max_steps)
        return AgentResult(
            run_id=run_id,
            reply="已达到最大推理步数，以下是中间结果。",
            steps=steps,
            status=RunStatus.FAILED,
            error_code=ErrorCode.MAX_STEPS_EXCEEDED,
            input_tokens=total_input,
            output_tokens=total_output,
        )

    async def _execute_tool(self, tool_name: str, arguments_json: str, *, run_id: str) -> str:
        """执行单个工具调用。"""
        if self.tool_executor is None:
            return f"Error: no tool executor configured"

        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            return f"Error: invalid JSON arguments"

        try:
            result = await self.tool_executor.execute(tool_name, args)
            return str(result)
        except Exception as exc:
            logger.error("tool_execution_failed", run_id=run_id, tool=tool_name, error=str(exc))
            return f"Error: {exc}"

    def _build_tool_defs(self) -> list[dict[str, Any]]:
        """将注册的工具转换为 OpenAI function calling 格式。"""
        defs = []
        for name, desc in self.tools.items():
            defs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc.get("description", ""),
                    "parameters": desc.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return defs


class AgentResult:
    """Agent 循环执行结果。"""

    def __init__(
        self,
        *,
        run_id: str,
        reply: str,
        steps: list[ReActStep],
        status: RunStatus,
        error_code: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self.run_id = run_id
        self.reply = reply
        self.steps = steps
        self.status = status
        self.error_code = error_code
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
