"""内置工具实现 — 对应 SPEC FR-012 内置工具。"""

from __future__ import annotations

import subprocess
from typing import Any

import httpx

from src.tools.registry import ToolDescriptor

# ── exec 命令白名单 ─────────────────────────────────────
EXEC_WHITELIST = {"ls", "dir", "echo", "cat", "head", "tail", "wc", "date", "whoami", "pwd"}


async def web_fetch(url: str, *, timeout: int = 15) -> str:
    """抓取指定 URL 的网页内容（纯文本）。"""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text[:10000]  # 截断避免过大


async def web_search(query: str) -> str:
    """简单网页搜索（占位实现，需接入实际搜索 API）。"""
    return f"[web_search] 搜索 '{query}' 的结果需要接入实际搜索 API。"


async def exec_command(command: str) -> str:
    """执行白名单内的系统命令。"""
    cmd_name = command.strip().split()[0] if command.strip() else ""
    if cmd_name not in EXEC_WHITELIST:
        return f"Error: command '{cmd_name}' is not in the allowed whitelist: {EXEC_WHITELIST}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout or result.stderr
        return output[:5000]
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except Exception as exc:
        return f"Error: {exc}"


# ── 内置工具描述 ────────────────────────────────────────

BUILTIN_TOOLS = [
    ToolDescriptor(
        name="web_fetch",
        display_name="网页抓取",
        description="抓取指定 URL 的网页内容",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的 URL"},
                "timeout": {"type": "integer", "description": "超时秒数", "default": 15},
            },
            "required": ["url"],
        },
        returns={"type": "string"},
        category="builtin",
        handler=web_fetch,
    ),
    ToolDescriptor(
        name="web_search",
        display_name="网页搜索",
        description="搜索指定关键词",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
        returns={"type": "string"},
        category="builtin",
        handler=web_search,
    ),
    ToolDescriptor(
        name="exec",
        display_name="命令执行",
        description=f"执行系统命令（仅限白名单: {', '.join(sorted(EXEC_WHITELIST))}）",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
            },
            "required": ["command"],
        },
        returns={"type": "string"},
        requires_approval=False,
        category="builtin",
        handler=exec_command,
    ),
]
