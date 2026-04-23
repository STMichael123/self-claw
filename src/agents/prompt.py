"""提示词编排器 — 对应 SPEC §18 src/agents/prompt。

注入顺序（SPEC FR-005）：
base prompt -> principle（全文） -> long-term（索引摘要） -> short-term
-> available skills catalog -> activated skill content -> tool descriptions
"""

from __future__ import annotations

from typing import Any

from src.models.llm import ChatMessage


def compose_system_prompt(
    *,
    base_prompt: str = "",
    principle: str = "",
    long_term_context: str = "",
    short_term_context: str = "",
    available_skills_catalog: list[dict[str, Any]] | None = None,
    activated_skills: list[dict[str, Any]] | None = None,
    tool_descriptions: list[dict[str, Any]] | None = None,
) -> str:
    """按 SPEC FR-005 注入顺序组装 system prompt。"""
    parts: list[str] = []

    # 1. Base prompt
    if base_prompt:
        parts.append(base_prompt)
    else:
        parts.append(
            "你是一个专业的 AI 助手。请根据用户的请求，完成任务并给出清晰的回复。\n"
            "当你需要执行工具时，请严格使用工具调用格式。\n"
            "当任务明显需要某个 Skill 时，先查看可用 Skill 目录，再调用 activate_skill 按需加载对应 Skill。"
        )

    # 2. Principle（全局约束）
    if principle:
        parts.append(f"\n## 系统原则\n{principle}")

    # 3. Long-term 记忆（全局共享知识）
    if long_term_context:
        parts.append(f"\n## 长期记忆\n{long_term_context}")

    # 4. Short-term / 会话快照
    if short_term_context:
        parts.append(f"\n## 会话记忆\n{short_term_context}")

    # 5. Available skills catalog
    if available_skills_catalog:
        catalog_lines = [
            f"- {item.get('skill_name') or item.get('name')}: {item.get('description', '')}".strip()
            for item in available_skills_catalog
        ]
        if catalog_lines:
            parts.append("\n## 可用 Skill 目录\n" + "\n".join(catalog_lines))

    # 6. Activated skill content
    if activated_skills:
        blocks = []
        for item in activated_skills:
            resources = item.get("resource_manifest") or []
            resource_lines = [f"- {resource.get('path')}" for resource in resources[:20]]
            block = [
                f"### {item.get('skill_name') or item.get('name')}",
                str(item.get("content") or "").strip(),
            ]
            if resource_lines:
                block.append("Resources:\n" + "\n".join(resource_lines))
            blocks.append("\n".join(part for part in block if part))
        if blocks:
            parts.append("\n## 已激活 Skills\n" + "\n\n".join(blocks))

    # 7. Tool descriptions
    if tool_descriptions:
        tool_lines = [
            f"- {item.get('name')}: {item.get('description', '')}".strip()
            for item in tool_descriptions
        ]
        if tool_lines:
            parts.append("\n## 可用工具\n" + "\n".join(tool_lines))

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
