"""Hook/Extension 注册与执行 — 对应 SPEC FR-016。

Hook 以 Python 函数形式定义在 .agents/hooks/<hook_name>.py 中。
系统启动时自动扫描并注册。

Hook 函数签名：接受 context: dict，返回 dict。
5 个 hook 点：pre_tool_call / post_tool_call / pre_agent_loop_step / post_agent_loop_step / on_session_archive。
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger()

HOOK_TIMEOUT_SEC = 5
VALID_HOOK_POINTS = {
    "pre_tool_call",
    "post_tool_call",
    "pre_agent_loop_step",
    "post_agent_loop_step",
    "on_session_archive",
}


class HookRegistry:
    """Hook 发现、注册与执行。"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., Any]]] = {hp: [] for hp in VALID_HOOK_POINTS}

    def discover_hooks(self, hooks_dir: str | Path) -> int:
        """扫描 .agents/hooks/ 目录，自动加载合法 Hook 函数。返回注册数量。"""
        hooks_path = Path(hooks_dir)
        if not hooks_path.exists():
            logger.info("hooks_dir_not_found", path=str(hooks_path))
            return 0

        count = 0
        for py_file in sorted(hooks_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                loaded = self._load_hooks_from_file(py_file)
                count += loaded
            except Exception as exc:
                logger.warning("hook_load_failed", file=py_file.name, error=str(exc))
        logger.info("hooks_discovered", total=count)
        return count

    def register(self, hook_point: str, fn: Callable[..., Any]) -> None:
        """手动注册一个 Hook 函数。"""
        if hook_point not in VALID_HOOK_POINTS:
            raise ValueError(f"invalid hook point: {hook_point}, valid: {VALID_HOOK_POINTS}")
        self._hooks[hook_point].append(fn)
        logger.info("hook_registered", hook_point=hook_point, function=fn.__name__)

    def get_hooks(self, hook_point: str) -> list[Callable[..., Any]]:
        return list(self._hooks.get(hook_point, []))

    async def run_hooks(self, hook_point: str, context: dict[str, Any]) -> dict[str, Any]:
        """依次执行指定 hook 点的所有函数。失败仅记日志不阻断。

        返回合并后的 context（最后一个 hook 的返回值覆盖）。
        """
        hooks = self._hooks.get(hook_point, [])
        if not hooks:
            return context

        merged = dict(context)
        for fn in hooks:
            try:
                result = await self._invoke_with_timeout(fn, merged)
                if isinstance(result, dict):
                    merged.update(result)
            except Exception as exc:
                logger.warning(
                    "hook_execution_failed",
                    hook_point=hook_point,
                    function=fn.__name__,
                    error=str(exc),
                )
        return merged

    async def _invoke_with_timeout(self, fn: Callable[..., Any], context: dict[str, Any]) -> Any:
        """执行 Hook 函数，5 秒超时。"""
        if asyncio.iscoroutinefunction(fn):
            return await asyncio.wait_for(fn(context), timeout=HOOK_TIMEOUT_SEC)

        result = fn(context)
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=HOOK_TIMEOUT_SEC)
        return result

    def _load_hooks_from_file(self, py_file: Path) -> int:
        """从单个 Python 文件加载 Hook 函数。"""
        module_name = f"hooks.{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(py_file))
        if spec is None or spec.loader is None:
            return 0

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        count = 0
        # 优先读取模块级 HOOK_POINT 声明
        declared_point = getattr(module, "HOOK_POINT", None)
        if declared_point and declared_point in VALID_HOOK_POINTS:
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if name.startswith("_"):
                    continue
                self.register(declared_point, obj)
                count += 1
            return count

        # 否则扫描所有公开函数，通过名称前缀匹配 hook 点
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_"):
                continue
            matched_point = self._infer_hook_point(name)
            if matched_point:
                self.register(matched_point, obj)
                count += 1

        return count

    @staticmethod
    def _infer_hook_point(name: str) -> str | None:
        """从函数名推断 hook 点。"""
        lower = name.lower()
        for hp in VALID_HOOK_POINTS:
            if hp in lower:
                return hp
        return None
