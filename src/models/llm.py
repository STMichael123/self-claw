"""LLM 适配器抽象与实现 — 对应 SPEC FR-010 / §18 src/models。"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field
import structlog


# ── 消息与响应模型 ──────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # system | user | assistant | tool
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finish_reason: str = ""


class ToolCallRequest(BaseModel):
    id: str = ""
    name: str = ""
    arguments: str = ""  # JSON string


class StreamChunk(BaseModel):
    delta: str = ""
    finish_reason: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)


# ── 抽象基类 ────────────────────────────────────────────

class LLMAdapter(ABC):
    """LLM 适配器抽象基类。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        ...
        yield  # type: ignore[misc]


# ── OpenAI 适配器 ───────────────────────────────────────

class OpenAIAdapter(LLMAdapter):
    """OpenAI / 兼容 API 适配器。"""

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None, base_url: str | None = None):
        from openai import AsyncOpenAI

        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            structlog.get_logger().warning(
                "api_key_missing", adapter="openai", model=model,
                hint="Set OPENAI_API_KEY env var or pass api_key parameter",
            )
        self.model = model
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or os.environ.get("OPENAI_BASE_URL") or None,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        tc = []
        if choice.message.tool_calls:
            tc = [
                ToolCallRequest(id=t.id, name=t.function.name, arguments=t.function.arguments)
                for t in choice.message.tool_calls
            ]
        return LLMResponse(
            content=choice.message.content or "",
            tool_calls=tc,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            model=resp.model,
            finish_reason=choice.finish_reason or "",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            tc = []
            if delta.tool_calls:
                tc = [
                    ToolCallRequest(id=t.id or "", name=t.function.name if t.function else "", arguments=t.function.arguments if t.function else "")
                    for t in delta.tool_calls
                ]
            yield StreamChunk(
                delta=delta.content or "",
                finish_reason=chunk.choices[0].finish_reason,
                tool_calls=tc,
            )


# ── Anthropic 适配器 ────────────────────────────────────

class AnthropicAdapter(LLMAdapter):
    """Anthropic Claude 适配器。"""

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str | None = None):
        from anthropic import AsyncAnthropic

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            structlog.get_logger().warning(
                "api_key_missing", adapter="anthropic", model=model,
                hint="Set ANTHROPIC_API_KEY env var or pass api_key parameter",
            )
        self.model = model
        self._client = AsyncAnthropic(api_key=resolved_key)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        system_msg = ""
        conv_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                conv_messages.append({"role": m.role, "content": m.content})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": conv_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        resp = await self._client.messages.create(**kwargs)
        content = ""
        tc = []
        for block in resp.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                import json

                tc.append(ToolCallRequest(id=block.id, name=block.name, arguments=json.dumps(block.input)))

        return LLMResponse(
            content=content,
            tool_calls=tc,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=resp.model,
            finish_reason=resp.stop_reason or "",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        system_msg = ""
        conv_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                conv_messages.append({"role": m.role, "content": m.content})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": conv_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield StreamChunk(delta=text)

    @staticmethod
    def _convert_tools(openai_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 OpenAI function 格式转换为 Anthropic tool 格式。"""
        result = []
        for t in openai_tools:
            fn = t.get("function", t)
            result.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return result
