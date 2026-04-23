"""Hook 服务单元测试 — 对应 SPEC FR-016。"""

from __future__ import annotations

import asyncio
import pytest

from src.services.hook_service import HookRegistry, VALID_HOOK_POINTS


# ── fixtures ────────────────────────────────────────────


@pytest.fixture
def registry() -> HookRegistry:
    return HookRegistry()


# ── discover_hooks ──────────────────────────────────────


class TestDiscoverHooks:
    def test_empty_directory(self, registry: HookRegistry, tmp_path):
        result = registry.discover_hooks(tmp_path / "nonexistent")
        assert result == 0

    def test_empty_hooks_dir(self, registry: HookRegistry, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        result = registry.discover_hooks(hooks_dir)
        assert result == 0

    def test_skips_underscored_files(self, registry: HookRegistry, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "__init__.py").write_text("# init")
        (hooks_dir / "_helper.py").write_text("def pre_tool_call(ctx): return ctx")
        result = registry.discover_hooks(hooks_dir)
        assert result == 0

    def test_loads_hook_with_declared_point(self, registry: HookRegistry, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "my_hook.py").write_text(
            'HOOK_POINT = "pre_tool_call"\n'
            "def my_fn(ctx): return ctx\n",
        )
        result = registry.discover_hooks(hooks_dir)
        assert result == 1
        assert len(registry.get_hooks("pre_tool_call")) == 1

    def test_loads_hook_by_name_inference(self, registry: HookRegistry, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "audit.py").write_text(
            "def on_session_archive(ctx): return ctx\n",
        )
        result = registry.discover_hooks(hooks_dir)
        assert result == 1
        assert len(registry.get_hooks("on_session_archive")) == 1


# ── register ────────────────────────────────────────────


class TestRegister:
    def test_register_valid_point(self, registry: HookRegistry):
        def my_hook(ctx):
            return ctx

        registry.register("pre_tool_call", my_hook)
        hooks = registry.get_hooks("pre_tool_call")
        assert len(hooks) == 1
        assert hooks[0] is my_hook

    def test_register_invalid_point_raises(self, registry: HookRegistry):
        with pytest.raises(ValueError, match="invalid hook point"):
            registry.register("nonexistent_point", lambda ctx: ctx)

    @pytest.mark.parametrize("point", list(VALID_HOOK_POINTS))
    def test_all_valid_points_accepted(self, registry: HookRegistry, point: str):
        registry.register(point, lambda ctx: ctx)
        assert len(registry.get_hooks(point)) == 1


# ── run_hooks ───────────────────────────────────────────


class TestRunHooks:
    @pytest.mark.asyncio
    async def test_no_hooks_returns_original_context(self, registry: HookRegistry):
        ctx = {"a": 1}
        result = await registry.run_hooks("pre_tool_call", ctx)
        assert result == ctx

    @pytest.mark.asyncio
    async def test_hooks_merge_context(self, registry: HookRegistry):
        def hook1(ctx):
            return {"extra": "from_hook1"}

        def hook2(ctx):
            return {"extra": "from_hook2", "added": True}

        registry.register("pre_tool_call", hook1)
        registry.register("pre_tool_call", hook2)
        result = await registry.run_hooks("pre_tool_call", {"original": True})
        assert result["original"] is True
        assert result["extra"] == "from_hook2"
        assert result["added"] is True

    @pytest.mark.asyncio
    async def test_async_hook(self, registry: HookRegistry):
        async def async_hook(ctx):
            return {"async": True}

        registry.register("post_tool_call", async_hook)
        result = await registry.run_hooks("post_tool_call", {})
        assert result["async"] is True

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_block(self, registry: HookRegistry):
        def bad_hook(ctx):
            raise RuntimeError("boom")

        def good_hook(ctx):
            return {"ok": True}

        registry.register("pre_agent_loop_step", bad_hook)
        registry.register("pre_agent_loop_step", good_hook)
        result = await registry.run_hooks("pre_agent_loop_step", {"start": 1})
        assert result["start"] == 1
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_hook_timeout_does_not_block(self, registry: HookRegistry):
        async def slow_hook(ctx):
            await asyncio.sleep(10)
            return {"never": True}

        registry.register("post_agent_loop_step", slow_hook)
        result = await registry.run_hooks("post_agent_loop_step", {"timeout_test": True})
        # Should return original context since slow_hook timed out
        assert result.get("never") is None
