"""工具注册与执行测试 — 对应 SPEC FR-012 测试矩阵。"""

from __future__ import annotations

import pytest

from src.tools.registry import ToolDescriptor, ToolError, ToolExecutor, ToolRegistry


async def _echo(**kwargs) -> str:  # type: ignore[override]
    return f"echo: {kwargs}"


async def _slow() -> str:
    import asyncio
    await asyncio.sleep(10)
    return "done"


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolDescriptor(
        name="echo",
        description="Echo tool",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
        handler=_echo,
    ))
    reg.register(ToolDescriptor(
        name="slow",
        description="Slow tool",
        handler=_slow,
        timeout_sec=1,
    ))
    reg.register(ToolDescriptor(
        name="needs_approval",
        description="Needs approval",
        requires_approval=True,
        handler=_echo,
    ))
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> ToolExecutor:
    return ToolExecutor(registry)


class TestToolRegistry:
    def test_register_and_get(self, registry: ToolRegistry) -> None:
        assert registry.get("echo") is not None

    def test_list_tools(self, registry: ToolRegistry) -> None:
        assert len(registry.list_tools()) == 3

    def test_get_unknown(self, registry: ToolRegistry) -> None:
        assert registry.get("nope") is None


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_execute_success(self, executor: ToolExecutor) -> None:
        result = await executor.execute("echo", {"msg": "hi"})
        assert "hi" in str(result)

    @pytest.mark.asyncio
    async def test_execute_not_found(self, executor: ToolExecutor) -> None:
        with pytest.raises(ToolError):
            await executor.execute("nope", {})

    @pytest.mark.asyncio
    async def test_execute_timeout(self, executor: ToolExecutor) -> None:
        with pytest.raises(ToolError) as exc_info:
            await executor.execute("slow", {})
        assert "timed out" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_execute_approval_pending(self, executor: ToolExecutor) -> None:
        with pytest.raises(ToolError) as exc_info:
            await executor.execute("needs_approval", {})
        assert "approval" in exc_info.value.message.lower()
