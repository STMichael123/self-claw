"""离线开发用 LLM 适配器。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from src.models.llm import ChatMessage, LLMAdapter, LLMResponse, StreamChunk


class DevLLMAdapter(LLMAdapter):
    """当未配置外部模型凭据时使用的轻量本地适配器。"""

    def __init__(self, model: str = "dev-local") -> None:
        self.model = model

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        user_message = _last_user_message(messages)
        content = _build_reply(user_message=user_message, tools=tools)
        return LLMResponse(
            content=content,
            input_tokens=max(1, len(user_message) // 4),
            output_tokens=max(1, len(content) // 4),
            model=self.model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        resp = await self.chat(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield StreamChunk(delta=resp.content, finish_reason=resp.finish_reason)


def _last_user_message(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content.strip()
    return ""


def _build_reply(user_message: str, tools: list[dict[str, Any]] | None) -> str:
    if not user_message:
        return "我已准备好接收任务。"

    segments = [
        f"已接收任务：{user_message}",
        "当前运行在开发模式下，可用于验证多会话、多主 Agent 与状态管理链路。",
    ]

    if tools:
        tool_names = ", ".join(item.get("function", {}).get("name", "") for item in tools)
        if tool_names:
            segments.append(f"可用工具：{tool_names}。")

    if any(keyword in user_message for keyword in ["分析", "调研", "比较", "拆解"]):
        segments.append("建议：该任务适合拆成子步骤执行，状态页中会展示主/子运行记录。")
    else:
        segments.append("建议：可以继续在当前线程补充约束，系统会保持该线程的独立上下文。")

    return "\n".join(segments)
