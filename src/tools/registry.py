"""工具注册表与执行器 — 对应 SPEC FR-012。"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import time
from typing import Any, Callable, Awaitable

from jsonschema import ValidationError as JSONSchemaValidationError, validate as jsonschema_validate
import structlog

from src.contracts.errors import ErrorCode

logger = structlog.get_logger()


class ToolDescriptor:
    """工具描述 — 对应 SPEC FR-012 工具描述格式。"""

    def __init__(
        self,
        *,
        name: str,
        display_name: str = "",
        description: str = "",
        parameters: dict[str, Any] | None = None,
        returns: dict[str, Any] | None = None,
        requires_approval: bool = False,
        timeout_sec: int = 30,
        concurrency_safe: bool = False,
        category: str = "custom",
        handler: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.name = name
        self.display_name = display_name or name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.returns = returns or {}
        self.requires_approval = requires_approval
        self.timeout_sec = timeout_sec
        self.concurrency_safe = concurrency_safe
        self.category = category
        self.handler = handler

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
            "requires_approval": self.requires_approval,
            "concurrency_safe": self.concurrency_safe,
            "category": self.category,
        }


class ToolRegistry:
    """工具注册表 — 注册、发现与调用。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> None:
        self._tools[descriptor.name] = descriptor
        logger.info("tool_registered", tool=descriptor.name, category=descriptor.category)

    def get(self, name: str) -> ToolDescriptor | None:
        return self._tools.get(name)

    def list_tools(self, *, category: str | None = None) -> list[ToolDescriptor]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def get_tool_defs(self, allowed: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """返回工具描述字典，用于注入 Agent 循环。"""
        result = {}
        for name, desc in self._tools.items():
            if allowed is not None and name not in allowed:
                continue
            result[name] = {
                "description": desc.description,
                "parameters": desc.parameters,
            }
        return result

    def clone_filtered(self, allowed: list[str] | None = None) -> ToolRegistry:
        """返回按 allowed 过滤后的注册表副本。"""
        if allowed is None:
            clone = ToolRegistry()
            clone._tools = dict(self._tools)
            return clone

        clone = ToolRegistry()
        clone._tools = {
            name: desc
            for name, desc in self._tools.items()
            if name in allowed
        }
        return clone


class ToolExecutor:
    """工具执行器 — 带超时与日志记录。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        cancellation_waiter: Callable[[], Awaitable[None]] | None = None,
        approval_context: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> Any:
        descriptor = self.registry.get(tool_name)
        if descriptor is None:
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' not found")

        if runtime_context is not None:
            allowlist = runtime_context.get("skill_tool_allowlist")
            if runtime_context.get("skill_tool_allowlist_active") and tool_name not in (allowlist or []) and tool_name != "activate_skill":
                raise ToolError(
                    ErrorCode.TOOL_NOT_ALLOWED,
                    f"Tool '{tool_name}' is not allowed by the activated skill set",
                )

        if descriptor.handler is None:
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' has no handler")

        self._validate_args(descriptor, args)

        if descriptor.requires_approval:
            if approval_context and approval_context.get("status") == "approved":
                pass
            elif approval_context and approval_context.get("status") == "rejected":
                raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' approval was rejected")
            else:
                raise ToolApprovalRequired(
                    approval_id="",
                    tool_name=tool_name,
                    args=args,
                    message=f"Tool '{tool_name}' requires approval",
                )

        start = time.monotonic()
        handler_task: asyncio.Task[Any] | None = None
        cancel_task: asyncio.Task[None] | None = None
        try:
            handler_task = asyncio.create_task(self._invoke_handler(descriptor, args, runtime_context=runtime_context))
            if cancellation_waiter is None:
                result = await asyncio.wait_for(handler_task, timeout=descriptor.timeout_sec)
            else:
                cancel_task = asyncio.create_task(cancellation_waiter())
                done, _ = await asyncio.wait(
                    {handler_task, cancel_task},
                    timeout=descriptor.timeout_sec,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if handler_task in done:
                    result = handler_task.result()
                elif cancel_task in done:
                    handler_task.cancel()
                    with contextlib.suppress(BaseException):
                        await handler_task
                    elapsed = int((time.monotonic() - start) * 1000)
                    logger.info("tool_cancelled", tool=tool_name, elapsed_ms=elapsed)
                    raise ToolCancelledError(tool_name)
                else:
                    handler_task.cancel()
                    with contextlib.suppress(BaseException):
                        await handler_task
                    raise asyncio.TimeoutError
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("tool_timeout", tool=tool_name, elapsed_ms=elapsed)
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' timed out after {descriptor.timeout_sec}s")
        except ToolCancelledError:
            raise
        except ToolError:
            raise
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            code = str(getattr(exc, "code", ErrorCode.TOOL_EXECUTION_FAILED))
            message = str(getattr(exc, "message", str(exc)))
            logger.error("tool_execution_failed", tool=tool_name, code=code, error=message, elapsed_ms=elapsed)
            raise ToolError(code, message) from exc
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                with contextlib.suppress(BaseException):
                    await cancel_task

        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("tool_executed", tool=tool_name, elapsed_ms=elapsed)
        return result

    @staticmethod
    def _validate_args(descriptor: ToolDescriptor, args: dict[str, Any]) -> None:
        schema = descriptor.parameters or {"type": "object", "properties": {}}
        try:
            jsonschema_validate(instance=args, schema=schema)
        except JSONSchemaValidationError as exc:
            raise ToolError(ErrorCode.SCHEMA_VALIDATION_FAILED, f"Tool '{descriptor.name}' arguments invalid: {exc.message}") from exc

    @staticmethod
    async def _invoke_handler(
        descriptor: ToolDescriptor,
        args: dict[str, Any],
        *,
        runtime_context: dict[str, Any] | None,
    ) -> Any:
        handler = descriptor.handler
        if handler is None:
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{descriptor.name}' has no handler")
        kwargs = dict(args)
        signature = inspect.signature(handler)
        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
        if runtime_context is not None and ("runtime_context" in signature.parameters or accepts_kwargs):
            kwargs["runtime_context"] = runtime_context
        return await handler(**kwargs)


class ToolError(Exception):
    """工具执行错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ToolCancelledError(Exception):
    """工具调用被运行取消信号中断。"""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' cancelled")


class ToolApprovalRequired(Exception):
    """工具调用需要审批。"""

    def __init__(self, approval_id: str, tool_name: str, args: dict[str, Any], message: str) -> None:
        self.approval_id = approval_id
        self.tool_name = tool_name
        self.args = args
        super().__init__(message)


# ── 装饰器注册 ──────────────────────────────────────────

def tool(
    name: str,
    *,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    requires_approval: bool = False,
    timeout_sec: int = 30,
    concurrency_safe: bool = False,
) -> Callable:
    """装饰器：将 async 函数注册为工具。"""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        fn._tool_descriptor = ToolDescriptor(  # type: ignore[attr-defined]
            name=name,
            description=description or fn.__doc__ or "",
            parameters=parameters,
            requires_approval=requires_approval,
            timeout_sec=timeout_sec,
            concurrency_safe=concurrency_safe,
            handler=fn,
        )
        return fn

    return decorator
