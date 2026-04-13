"""提示词编排器 — 对应 SPEC §18 src/agents/prompt。"""

from __future__ import annotations

from typing import Any

from src.models.llm import ChatMessage


def compose_system_prompt(
    *,
    base_prompt: str = "",
    skill_prompt: str | None = None,
    tool_descriptions: list[dict[str, Any]] | None = None,
    memory_context: str = "",
) -> str:
    """组装 system prompt。"""
    parts: list[str] = []

    if base_prompt:
        parts.append(base_prompt)
    else:
        parts.append(
            "你是一个专业的 AI 助手。请根据用户的请求，完成任务并给出清晰的回复。\n"
            "当你需要执行工具时，请严格使用工具调用格式。"
        )

    if skill_prompt:
        parts.append(f"\n## Skill 指令\n{skill_prompt}")

    if memory_context:
        parts.append(f"\n## 相关记忆\n{memory_context}")

    return "\n".join(parts)


def build_messages(
    *,
    system_prompt: str,
    history: list[ChatMessage] | None = None,
    user_message: str = "",
) -> list[ChatMessage]:
    """构建完整的消息列表。"""
    msgs = [ChatMessage(role="system", content=system_prompt)]
    if history:
        msgs.extend(history)
    if user_message:
        msgs.append(ChatMessage(role="user", content=user_message))
    return msgs
