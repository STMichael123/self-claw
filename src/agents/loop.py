"""Agent 核心执行循环（ReAct） — 对应 SPEC FR-010。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import structlog

from src.contracts.errors import ErrorCode
from src.contracts.models import ReActStep, RunStatus
from src.models.llm import ChatMessage, LLMAdapter, LLMResponse
from src.tools.registry import ToolApprovalRequired, ToolCancelledError
from src.agents.prompt import build_messages, compose_system_prompt

logger = structlog.get_logger()

HOOK_REGISTRY_ATTR = "hook_registry"

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
        cancellation_checker: Callable[[], bool] | None = None,
        cancellation_waiter: Callable[[], Awaitable[None]] | None = None,
        approval_requester: Callable[[str, dict[str, Any], Any], Awaitable[dict[str, Any]]] | None = None,
        hook_registry: Any | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools or {}  # name -> tool_descriptor
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.cancellation_checker = cancellation_checker
        self.cancellation_waiter = cancellation_waiter
        self.approval_requester = approval_requester
        self._hook_registry = hook_registry

    async def run(
        self,
        *,
        system_prompt: str,
        messages: list[ChatMessage],
        run_id: str | None = None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        resume_state: dict[str, Any] | None = None,
        approved_approval: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """执行完整的 ReAct 循环（阻塞模式）。"""
        run_id = run_id or str(uuid.uuid4())
        state = self._load_state(messages=messages, resume_state=resume_state, runtime_context=runtime_context)
        steps = state["steps"]
        total_input = state["total_input"]
        total_output = state["total_output"]
        current_messages = state["current_messages"]
        next_step = state["next_step"]
        pending_bundle = state["pending_bundle"]
        runtime_state = state["runtime_context"]

        tool_defs = self._build_tool_defs() if self.tools else None

        try:
            if pending_bundle is not None:
                await self._drain_pending_tool_calls(
                    pending_bundle=pending_bundle,
                    current_messages=current_messages,
                    steps=steps,
                    run_id=run_id,
                    runtime_context=runtime_state,
                    event_callback=event_callback,
                    approved_approval=approved_approval,
                )
                next_step = int(pending_bundle["step"]) + 1

            for step_num in range(next_step, self.max_steps + 1):
                log = logger.bind(run_id=run_id, step=step_num)
                self._ensure_not_cancelled(run_id)

                await self._run_hook("pre_agent_loop_step", {
                    "step_number": step_num,
                    "activated_skills": list(runtime_state.get("activated_skills") or []),
                    "session_id": runtime_state.get("session_id"),
                    "run_id": run_id,
                })

                resp = await self._await_with_cancellation(
                    self.llm.chat(current_messages, tools=tool_defs),
                    run_id=run_id,
                )
                total_input += resp.input_tokens
                total_output += resp.output_tokens
                self._ensure_not_cancelled(run_id)

                await self._emit_event(
                    event_callback,
                    {
                        "event": "thinking",
                        "step": step_num,
                        "content": resp.content,
                    },
                )

                # 无工具调用 → 直接回复，结束循环
                if not resp.tool_calls:
                    steps.append(ReActStep(step=step_num, thinking=resp.content))
                    log.info("agent_loop_reply", content_length=len(resp.content))
                    await self._emit_event(
                        event_callback,
                        {
                            "event": "reply",
                            "content": resp.content,
                        },
                    )
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
                pending_bundle = {
                    "step": step_num,
                    "thinking": resp.content,
                    "tool_calls": [tc.model_dump() for tc in resp.tool_calls],
                    "next_tool_index": 0,
                }
                await self._drain_pending_tool_calls(
                    pending_bundle=pending_bundle,
                    current_messages=current_messages,
                    steps=steps,
                    run_id=run_id,
                    runtime_context=runtime_state,
                    event_callback=event_callback,
                    approved_approval=None,
                )
        except RunCancelledError:
            logger.info("agent_loop_cancelled", run_id=run_id)
            return AgentResult(
                run_id=run_id,
                reply="该运行已被取消。",
                steps=steps,
                status=RunStatus.CANCELLED,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        except LoopApprovalPending as exc:
            await self._emit_event(
                event_callback,
                {
                    "event": "approval_pending",
                    **exc.pending_approval,
                },
            )
            return AgentResult(
                run_id=run_id,
                reply=f"工具 {exc.pending_approval['tool_name']} 正在等待审批。",
                steps=steps,
                status=RunStatus.RUNNING,
                error_code=ErrorCode.TOOL_APPROVAL_PENDING,
                input_tokens=total_input,
                output_tokens=total_output,
                pending_approval=exc.pending_approval,
                resume_state=self._dump_state(
                    current_messages=current_messages,
                    steps=steps,
                    total_input=total_input,
                    total_output=total_output,
                    next_step=int(exc.pending_bundle["step"]),
                    pending_bundle=exc.pending_bundle,
                    runtime_context=runtime_state,
                ),
            )

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

    async def _execute_tool(
        self,
        tool_name: str,
        arguments_json: str,
        *,
        run_id: str,
        runtime_context: dict[str, Any],
        approval_context: dict[str, Any] | None = None,
    ) -> str:
        """执行单个工具调用。"""
        if self.tool_executor is None:
            return f"Error: no tool executor configured"

        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            return f"Error: invalid JSON arguments"

        # pre_tool_call hook
        hook_ctx = await self._run_hook("pre_tool_call", {
            "tool_name": tool_name,
            "parameters": args,
            "agent_run_id": run_id,
        })
        if hook_ctx.get("abort"):
            logger.info("tool_aborted_by_hook", run_id=run_id, tool=tool_name)
            return f"Error: tool '{tool_name}' aborted by pre_tool_call hook"

        try:
            runtime_context["run_id"] = run_id
            import time
            start = time.monotonic()
            result = await self.tool_executor.execute(
                tool_name,
                args,
                cancellation_waiter=self.cancellation_waiter,
                approval_context=approval_context,
                runtime_context=runtime_context,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            result_str = str(result)

            # post_tool_call hook
            await self._run_hook("post_tool_call", {
                "tool_name": tool_name,
                "parameters": args,
                "result": result_str,
                "duration_ms": duration_ms,
                "agent_run_id": run_id,
            })

            return result_str
        except ToolApprovalRequired:
            raise
        except ToolCancelledError as exc:
            logger.info("tool_execution_cancelled", run_id=run_id, tool=tool_name)
            raise RunCancelledError(run_id) from exc
        except Exception as exc:
            logger.error("tool_execution_failed", run_id=run_id, tool=tool_name, error=str(exc))
            return f"Error: {exc}"

    async def _drain_pending_tool_calls(
        self,
        *,
        pending_bundle: dict[str, Any],
        current_messages: list[ChatMessage],
        steps: list[ReActStep],
        run_id: str,
        runtime_context: dict[str, Any],
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        approved_approval: dict[str, Any] | None,
    ) -> None:
        step_num = int(pending_bundle["step"])
        thinking = str(pending_bundle.get("thinking", ""))
        tool_calls = pending_bundle.get("tool_calls", [])
        next_tool_index = int(pending_bundle.get("next_tool_index", 0))
        approval_context = approved_approval

        # Group remaining tool calls by concurrency_safe
        remaining_calls = []
        for tool_index in range(next_tool_index, len(tool_calls)):
            raw_tool_call = tool_calls[tool_index]
            tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else {}
            tool_name = str(tool_call.get("name", ""))
            tool_arguments = str(tool_call.get("arguments", ""))
            tool_call_id = str(tool_call.get("id", ""))
            action_input = json.loads(tool_arguments) if tool_arguments else {}
            is_safe = self._is_concurrency_safe(tool_name)
            remaining_calls.append({
                "index": tool_index,
                "tool_name": tool_name,
                "tool_arguments": tool_arguments,
                "tool_call_id": tool_call_id,
                "action_input": action_input,
                "concurrency_safe": is_safe,
            })

        # Execute in two phases: safe tools in parallel, then unsafe tools serially
        for phase_safe in (True, False):
            phase_calls = [tc for tc in remaining_calls if tc["concurrency_safe"] == phase_safe]
            if not phase_calls:
                continue

            if phase_safe and len(phase_calls) > 1:
                # Parallel execution for concurrency_safe tools
                await self._execute_tool_calls_parallel(
                    phase_calls,
                    current_messages=current_messages,
                    steps=steps,
                    run_id=run_id,
                    step_num=step_num,
                    thinking=thinking,
                    runtime_context=runtime_context,
                    approval_context=approval_context,
                    event_callback=event_callback,
                    pending_bundle=pending_bundle,
                )
            else:
                # Serial execution for unsafe tools or single safe tool
                for tc in phase_calls:
                    await self._execute_single_tool_call(
                        tc,
                        current_messages=current_messages,
                        steps=steps,
                        run_id=run_id,
                        step_num=step_num,
                        thinking=thinking,
                        runtime_context=runtime_context,
                        approval_context=approval_context,
                        event_callback=event_callback,
                        pending_bundle=pending_bundle,
                    )
                    approval_context = None

        # post_agent_loop_step hook
        last_observation = steps[-1].observation if steps else ""
        await self._run_hook("post_agent_loop_step", {
            "step_number": step_num,
            "action_type": "tool_calls" if tool_calls else "reply",
            "observation_summary": last_observation[:200],
        })

    async def _execute_single_tool_call(
        self,
        tc: dict[str, Any],
        *,
        current_messages: list[ChatMessage],
        steps: list[ReActStep],
        run_id: str,
        step_num: int,
        thinking: str,
        runtime_context: dict[str, Any],
        approval_context: dict[str, Any] | None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        pending_bundle: dict[str, Any],
    ) -> None:
        """Execute a single tool call and record the result."""
        tool_name = tc["tool_name"]
        tool_arguments = tc["tool_arguments"]
        tool_call_id = tc["tool_call_id"]
        action_input = tc["action_input"]

        await self._emit_event(event_callback, {
            "event": "action",
            "step": step_num,
            "name": tool_name,
            "input": action_input,
        })

        self._ensure_not_cancelled(run_id)
        try:
            observation = await self._execute_tool(
                tool_name,
                tool_arguments,
                run_id=run_id,
                runtime_context=runtime_context,
                approval_context=approval_context,
            )
        except ToolApprovalRequired as exc:
            pending_bundle["next_tool_index"] = tc["index"]
            raise LoopApprovalPending(
                pending_approval={
                    "approval_id": exc.approval_id,
                    "tool_name": exc.tool_name,
                    "arguments": exc.args,
                },
                pending_bundle=pending_bundle,
            ) from exc

        self._ensure_not_cancelled(run_id)
        steps.append(ReActStep(
            step=step_num,
            thinking=thinking,
            action=tool_name,
            action_input=action_input,
            observation=observation,
        ))
        current_messages.append(ChatMessage(role="tool", content=observation, tool_call_id=tool_call_id))
        if tool_name == "activate_skill":
            await self._emit_event(event_callback, {
                "event": "skill_activation",
                "step": step_num,
                "skill_name": action_input.get("skill_name"),
                "content": observation,
            })
        await self._emit_event(event_callback, {
            "event": "observation",
            "step": step_num,
            "content": observation,
        })

    async def _execute_tool_calls_parallel(
        self,
        calls: list[dict[str, Any]],
        *,
        current_messages: list[ChatMessage],
        steps: list[ReActStep],
        run_id: str,
        step_num: int,
        thinking: str,
        runtime_context: dict[str, Any],
        approval_context: dict[str, Any] | None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        pending_bundle: dict[str, Any],
    ) -> None:
        """Execute multiple concurrency_safe tool calls in parallel."""
        async def _run_one(tc: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
            tool_name = tc["tool_name"]
            tool_arguments = tc["tool_arguments"]
            action_input = tc["action_input"]

            await self._emit_event(event_callback, {
                "event": "action",
                "step": step_num,
                "name": tool_name,
                "input": action_input,
            })

            self._ensure_not_cancelled(run_id)
            try:
                observation = await self._execute_tool(
                    tool_name,
                    tool_arguments,
                    run_id=run_id,
                    runtime_context=runtime_context,
                    approval_context=None,
                )
            except ToolApprovalRequired:
                # Safe tools requiring approval fall back to serial handling
                return tc, None
            except RunCancelledError:
                raise
            except Exception as exc:
                observation = f"Error: {exc}"
            return tc, observation

        results = await asyncio.gather(*[_run_one(tc) for tc in calls], return_exceptions=True)

        for result_item in results:
            if isinstance(result_item, BaseException):
                logger.warning("parallel_tool_call_failed", error=str(result_item))
                continue
            tc, observation = result_item
            if observation is None:
                # Approval required — skip, will be handled on retry
                continue

            tool_name = tc["tool_name"]
            tool_call_id = tc["tool_call_id"]
            action_input = tc["action_input"]

            steps.append(ReActStep(
                step=step_num,
                thinking=thinking,
                action=tool_name,
                action_input=action_input,
                observation=observation,
            ))
            current_messages.append(ChatMessage(role="tool", content=observation, tool_call_id=tool_call_id))
            await self._emit_event(event_callback, {
                "event": "observation",
                "step": step_num,
                "content": observation,
            })

    def _is_concurrency_safe(self, tool_name: str) -> bool:
        """Check if a tool is marked concurrency_safe."""
        descriptor = self.tools.get(tool_name)
        if descriptor and isinstance(descriptor, dict):
            return descriptor.get("concurrency_safe", False)
        return False

    async def _run_hook(self, hook_point: str, context: dict[str, Any]) -> dict[str, Any]:
        """Run hooks from the hook registry if available."""
        registry = self._hook_registry
        if registry is None:
            return context
        try:
            return await registry.run_hooks(hook_point, context)
        except Exception as exc:
            logger.warning("hook_run_error", hook_point=hook_point, error=str(exc))
            return context

    def _ensure_not_cancelled(self, run_id: str) -> None:
        if self.cancellation_checker and self.cancellation_checker():
            raise RunCancelledError(run_id)

    async def _await_with_cancellation(self, awaitable: Awaitable[LLMResponse], *, run_id: str) -> LLMResponse:
        if self.cancellation_waiter is None:
            return await awaitable

        task = asyncio.create_task(awaitable)
        cancel_task = asyncio.create_task(self.cancellation_waiter())
        try:
            done, _ = await asyncio.wait({task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_task in done:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                raise RunCancelledError(run_id)
            return task.result()
        finally:
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task

    @staticmethod
    async def _emit_event(
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        payload: dict[str, Any],
    ) -> None:
        if event_callback is None:
            return
        await event_callback(payload)

    @staticmethod
    def _load_state(
        *,
        messages: list[ChatMessage],
        resume_state: dict[str, Any] | None,
        runtime_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not resume_state:
            return {
                "current_messages": list(messages),
                "steps": [],
                "total_input": 0,
                "total_output": 0,
                "next_step": 1,
                "pending_bundle": None,
                "runtime_context": dict(runtime_context or {}),
            }

        current_messages = [ChatMessage.model_validate(item) for item in resume_state.get("current_messages", [])]
        steps = [ReActStep.model_validate(item) for item in resume_state.get("steps", [])]
        merged_runtime_context = dict(resume_state.get("runtime_context") or {})
        if runtime_context:
            merged_runtime_context.update(runtime_context)

        return {
            "current_messages": current_messages or list(messages),
            "steps": steps,
            "total_input": int(resume_state.get("total_input", 0)),
            "total_output": int(resume_state.get("total_output", 0)),
            "next_step": int(resume_state.get("next_step", 1)),
            "pending_bundle": resume_state.get("pending_bundle"),
            "runtime_context": merged_runtime_context,
        }

    @staticmethod
    def _dump_state(
        *,
        current_messages: list[ChatMessage],
        steps: list[ReActStep],
        total_input: int,
        total_output: int,
        next_step: int,
        pending_bundle: dict[str, Any] | None,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "current_messages": [message.model_dump(exclude_none=True) for message in current_messages],
            "steps": [step.model_dump() for step in steps],
            "total_input": total_input,
            "total_output": total_output,
            "next_step": next_step,
            "pending_bundle": pending_bundle,
            "runtime_context": runtime_context,
        }

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
        pending_approval: dict[str, Any] | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.reply = reply
        self.steps = steps
        self.status = status
        self.error_code = error_code
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.pending_approval = pending_approval
        self.resume_state = resume_state


class RunCancelledError(Exception):
    """运行被取消。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Run '{run_id}' cancelled")


class LoopApprovalPending(Exception):
    """Agent 循环在工具审批点暂停。"""

    def __init__(self, *, pending_approval: dict[str, Any], pending_bundle: dict[str, Any]) -> None:
        self.pending_approval = pending_approval
        self.pending_bundle = pending_bundle
        super().__init__(f"Tool '{pending_approval.get('tool_name', '')}' approval pending")
