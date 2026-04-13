"""工具注册表与执行器 — 对应 SPEC FR-012。"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, Awaitable

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


class ToolExecutor:
    """工具执行器 — 带超时与日志记录。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, tool_name: str, args: dict[str, Any]) -> Any:
        descriptor = self.registry.get(tool_name)
        if descriptor is None:
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' not found")

        if descriptor.handler is None:
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' has no handler")

        if descriptor.requires_approval:
            raise ToolError(ErrorCode.TOOL_APPROVAL_PENDING, f"Tool '{tool_name}' requires approval")

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                descriptor.handler(**args),
                timeout=descriptor.timeout_sec,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("tool_timeout", tool=tool_name, elapsed_ms=elapsed)
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, f"Tool '{tool_name}' timed out after {descriptor.timeout_sec}s")
        except ToolError:
            raise
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("tool_execution_failed", tool=tool_name, error=str(exc), elapsed_ms=elapsed)
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, str(exc)) from exc

        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("tool_executed", tool=tool_name, elapsed_ms=elapsed)
        return result


class ToolError(Exception):
    """工具执行错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# ── 装饰器注册 ──────────────────────────────────────────

def tool(
    name: str,
    *,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    requires_approval: bool = False,
    timeout_sec: int = 30,
) -> Callable:
    """装饰器：将 async 函数注册为工具。"""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        fn._tool_descriptor = ToolDescriptor(  # type: ignore[attr-defined]
            name=name,
            description=description or fn.__doc__ or "",
            parameters=parameters,
            requires_approval=requires_approval,
            timeout_sec=timeout_sec,
            handler=fn,
        )
        return fn

    return decorator
