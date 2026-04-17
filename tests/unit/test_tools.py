"""工具注册与执行测试 — 对应 SPEC FR-012 测试矩阵。"""

from __future__ import annotations

import json
import pytest

from src.tools.builtins import BUILTIN_TOOLS, exec_command
from src.tools.registry import ToolApprovalRequired, ToolDescriptor, ToolError, ToolExecutor, ToolRegistry


async def _echo(**kwargs) -> str:  # type: ignore[override]
    return f"echo: {kwargs}"


async def _runtime_echo(*, runtime_context: dict[str, str] | None = None) -> str:
    return json.dumps(runtime_context or {}, ensure_ascii=False)


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
    reg.register(ToolDescriptor(
        name="runtime_echo",
        description="Runtime aware tool",
        handler=_runtime_echo,
    ))
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> ToolExecutor:
    return ToolExecutor(registry)


class TestToolRegistry:
    def test_register_and_get(self, registry: ToolRegistry) -> None:
        assert registry.get("echo") is not None

    def test_list_tools(self, registry: ToolRegistry) -> None:
        assert len(registry.list_tools()) == 4

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
        with pytest.raises(ToolApprovalRequired) as exc_info:
            await executor.execute("needs_approval", {})
        assert "approval" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_execute_invalid_parameters(self, executor: ToolExecutor) -> None:
        with pytest.raises(ToolError) as exc_info:
            await executor.execute("echo", {"msg": 123})
        assert exc_info.value.code == "SCHEMA_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_execute_passes_runtime_context(self, executor: ToolExecutor) -> None:
        result = await executor.execute("runtime_echo", {}, runtime_context={"run_id": "run-123"})
        assert json.loads(result)["run_id"] == "run-123"


class TestBuiltinExec:
    def test_exec_builtin_requires_approval(self) -> None:
        descriptor = next(item for item in BUILTIN_TOOLS if item.name == "exec")
        assert descriptor.requires_approval is True

    def test_file_write_tools_require_approval(self) -> None:
        write_descriptor = next(item for item in BUILTIN_TOOLS if item.name == "write_file")
        patch_descriptor = next(item for item in BUILTIN_TOOLS if item.name == "patch_file")
        read_descriptor = next(item for item in BUILTIN_TOOLS if item.name == "read_file")
        assert write_descriptor.requires_approval is True
        assert patch_descriptor.requires_approval is True
        assert read_descriptor.requires_approval is False

    @pytest.mark.asyncio
    async def test_exec_command_does_not_execute_shell_suffix(self) -> None:
        result = await exec_command("echo hello & whoami")
        assert result == "hello & whoami"

    @pytest.mark.asyncio
    async def test_exec_command_blocks_non_whitelisted_command(self) -> None:
        result = await exec_command("powershell Get-Date")
        assert "not in the allowed whitelist" in result
