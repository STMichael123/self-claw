"""内置工具实现 — 对应 SPEC FR-012 内置工具。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
import getpass
import os
from pathlib import Path
import shlex
from typing import Any

import httpx

from src.contracts.errors import ErrorCode
from src.services.file_workspace import FileWorkspaceError, FileWorkspaceService
from src.services.skill_service import SkillService
from src.skills.registry import SkillRegistryError
from src.tools.registry import ToolDescriptor, ToolError

# ── exec 命令白名单 ─────────────────────────────────────
DEFAULT_EXEC_WHITELIST = {"ls", "dir", "echo", "cat", "head", "tail", "wc", "date", "whoami", "pwd"}
DEFAULT_TEXT_LIMIT = 5000
DEFAULT_HEAD_LINES = 10


async def web_fetch(url: str, *, timeout: int = 15) -> str:
    """抓取指定 URL 的网页内容（纯文本）。"""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text[:10000]  # 截断避免过大


async def web_search(query: str) -> str:
    """简单网页搜索（[实验性] 需接入实际搜索 API 后方可使用）。"""
    return (
        f"[实验性] web_search 尚未接入实际搜索 API，无法返回 '{query}' 的搜索结果。"
        "请联系管理员配置搜索服务后重试。"
    )


async def exec_command(command: str, *, whitelist: set[str] | None = None) -> str:
    """执行白名单内的安全内置命令。"""
    allowed = whitelist or DEFAULT_EXEC_WHITELIST
    try:
        parts = _split_command(command)
    except ValueError as exc:
        return f"Error: {exc}"

    if not parts:
        return "Error: command is empty"

    cmd_name = parts[0].lower()
    if cmd_name not in allowed:
        return f"Error: command '{cmd_name}' is not in the allowed whitelist: {sorted(allowed)}"

    handler = _EXEC_HANDLERS[cmd_name]
    try:
        output = await asyncio.to_thread(handler, parts[1:])
    except Exception as exc:
        return f"Error: {exc}"
    return _truncate_text(output)


def _split_command(command: str) -> list[str]:
    raw = command.strip()
    if not raw:
        return []
    return shlex.split(raw, posix=os.name != "nt")


def _cmd_echo(args: list[str]) -> str:
    return " ".join(args)


def _cmd_pwd(args: list[str]) -> str:
    _ensure_no_extra_args(args)
    return str(Path.cwd())


def _cmd_whoami(args: list[str]) -> str:
    _ensure_no_extra_args(args)
    return getpass.getuser()


def _cmd_date(args: list[str]) -> str:
    _ensure_no_extra_args(args)
    return datetime.now().astimezone().isoformat()


def _cmd_ls(args: list[str]) -> str:
    path = _single_optional_path(args)
    target = _resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"path not found: {target}")
    if target.is_file():
        return target.name
    entries = []
    for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        suffix = "/" if entry.is_dir() else ""
        entries.append(f"{entry.name}{suffix}")
    return "\n".join(entries)


def _cmd_cat(args: list[str]) -> str:
    target = _require_single_path(args)
    return _read_text(target)


def _cmd_head(args: list[str]) -> str:
    line_count, target = _parse_line_command(args)
    return "\n".join(_read_text(target).splitlines()[:line_count])


def _cmd_tail(args: list[str]) -> str:
    line_count, target = _parse_line_command(args)
    lines = _read_text(target).splitlines()
    return "\n".join(lines[-line_count:])


def _cmd_wc(args: list[str]) -> str:
    target = _require_single_path(args)
    text = _read_text(target)
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    words = len(text.split())
    chars = len(text)
    return f"{lines} {words} {chars} {target}"


def _parse_line_command(args: list[str]) -> tuple[int, Path]:
    if not args:
        raise ValueError("file path is required")
    line_count = DEFAULT_HEAD_LINES
    path_args = args
    if len(args) >= 3 and args[0] == "-n":
        line_count = int(args[1])
        path_args = args[2:]
    if line_count <= 0:
        raise ValueError("line count must be greater than zero")
    return line_count, _require_single_path(path_args)


def _single_optional_path(args: list[str]) -> str | None:
    if len(args) > 1:
        raise ValueError("too many arguments")
    return args[0] if args else None


def _require_single_path(args: list[str]) -> Path:
    path = _single_optional_path(args)
    if not path:
        raise ValueError("file path is required")
    return _resolve_path(path)


def _resolve_path(path: str | None) -> Path:
    normalized = (path or ".").strip().strip('"').strip("'")
    return Path(normalized).expanduser().resolve()


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"path not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"path is a directory: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _ensure_no_extra_args(args: list[str]) -> None:
    if args:
        raise ValueError("this command does not accept arguments")


def _truncate_text(text: str) -> str:
    return text[:DEFAULT_TEXT_LIMIT]


_EXEC_HANDLERS = {
    "echo": _cmd_echo,
    "pwd": _cmd_pwd,
    "whoami": _cmd_whoami,
    "date": _cmd_date,
    "ls": _cmd_ls,
    "dir": _cmd_ls,
    "cat": _cmd_cat,
    "head": _cmd_head,
    "tail": _cmd_tail,
    "wc": _cmd_wc,
}


# ── 内置工具描述 ────────────────────────────────────────

def build_builtin_tools(
    file_workspace: FileWorkspaceService | None = None,
    skill_service: SkillService | None = None,
    exec_whitelist: set[str] | None = None,
) -> list[ToolDescriptor]:
    effective_whitelist = exec_whitelist or DEFAULT_EXEC_WHITELIST

    async def list_dir(path: str = ".", runtime_context: dict[str, Any] | None = None) -> str:
        workspace = _require_workspace(file_workspace)
        return _to_json(workspace.list_dir(path, runtime_context=runtime_context))

    async def read_file(path: str, runtime_context: dict[str, Any] | None = None) -> str:
        workspace = _require_workspace(file_workspace)
        return _to_json(workspace.read_file(path, runtime_context=runtime_context))

    async def write_file(
        path: str,
        content: str,
        expected_checksum: str | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        workspace = _require_workspace(file_workspace)
        return _to_json(
            workspace.write_file(
                path,
                content,
                expected_checksum=expected_checksum,
                runtime_context=runtime_context,
            )
        )

    async def patch_file(
        path: str,
        old_text: str,
        new_text: str,
        expected_checksum: str | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        workspace = _require_workspace(file_workspace)
        return _to_json(
            workspace.patch_file(
                path,
                old_text,
                new_text,
                expected_checksum=expected_checksum,
                runtime_context=runtime_context,
            )
        )

    async def activate_skill(
        skill_name: str,
        resource_paths: list[str] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        if skill_service is None:
            raise ToolError(ErrorCode.TOOL_EXECUTION_FAILED, "skill service is not configured")
        try:
            payload = skill_service.activate_skill(skill_name, resource_paths=resource_paths)
        except SkillRegistryError as exc:
            raise ToolError(exc.code, exc.message) from exc

        if runtime_context is not None:
            activated_skills = runtime_context.setdefault("activated_skills", [])
            if skill_name not in activated_skills:
                activated_skills.append(skill_name)
            if resource_paths is None:
                runtime_context["skill_tool_allowlist_active"] = True
                skill_tool_allowlist = runtime_context.setdefault("skill_tool_allowlist", [])
                merged_tools = set(skill_tool_allowlist)
                merged_tools.update(payload.get("allowed_tools") or [])
                runtime_context["skill_tool_allowlist"] = sorted(merged_tools)
        return _to_json(payload)

    tools = [
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
            concurrency_safe=True,
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
            concurrency_safe=True,
            category="builtin",
            handler=web_search,
        ),
        ToolDescriptor(
            name="list_dir",
            display_name="目录列表",
            description="列出沙箱目录下的文件和子目录",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的路径", "default": "."},
                },
            },
            returns={"type": "string"},
            concurrency_safe=True,
            category="builtin",
            handler=list_dir,
        ),
        ToolDescriptor(
            name="read_file",
            display_name="读取文件",
            description="读取沙箱目录中的文本文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的文件路径"},
                },
                "required": ["path"],
            },
            returns={"type": "string"},
            concurrency_safe=True,
            category="builtin",
            handler=read_file,
        ),
        ToolDescriptor(
            name="write_file",
            display_name="写入文件",
            description="在沙箱目录中原子写入文本文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的文件路径"},
                    "content": {"type": "string", "description": "要写入的文本内容"},
                    "expected_checksum": {"type": "string", "description": "写入前预期校验和", "default": ""},
                },
                "required": ["path", "content"],
            },
            returns={"type": "string"},
            requires_approval=True,
            category="builtin",
            handler=write_file,
        ),
        ToolDescriptor(
            name="patch_file",
            display_name="补丁写入",
            description="对沙箱目录中的文本文件执行一次精确替换",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的文件路径"},
                    "old_text": {"type": "string", "description": "待替换的旧文本"},
                    "new_text": {"type": "string", "description": "替换后的新文本"},
                    "expected_checksum": {"type": "string", "description": "写入前预期校验和", "default": ""},
                },
                "required": ["path", "old_text", "new_text"],
            },
            returns={"type": "string"},
            requires_approval=True,
            category="builtin",
            handler=patch_file,
        ),
        ToolDescriptor(
            name="activate_skill",
            display_name="激活 Skill",
            description="按需加载 Skill 正文与可选资源，返回正文、允许工具和资源清单",
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "要激活的 Skill 名称"},
                    "resource_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要额外加载的 Skill 资源相对路径列表",
                    },
                },
                "required": ["skill_name"],
            },
            returns={"type": "string"},
            category="builtin",
            handler=activate_skill,
        ),
    ]

    # exec handler must be defined outside the list literal
    async def _exec_with_whitelist(command: str) -> str:
        return await exec_command(command, whitelist=effective_whitelist)

    tools.append(ToolDescriptor(
        name="exec",
        display_name="命令执行",
        description=f"执行系统命令（仅限白名单: {', '.join(sorted(effective_whitelist))}）",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
            },
            "required": ["command"],
        },
        returns={"type": "string"},
        requires_approval=True,
        category="builtin",
        handler=_exec_with_whitelist,
    ))

    return tools

BUILTIN_TOOLS = build_builtin_tools()


def _require_workspace(file_workspace: FileWorkspaceService | None) -> FileWorkspaceService:
    if file_workspace is None:
        raise ToolError("TOOL_EXECUTION_FAILED", "file workspace service is not configured")
    return file_workspace


def _to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
