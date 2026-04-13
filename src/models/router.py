"""模型路由与 Token 管理 — 对应 SPEC FR-010。"""

from __future__ import annotations

import os

import tiktoken

from src.models.llm import AnthropicAdapter, LLMAdapter, OpenAIAdapter


# ── Token 计数 ──────────────────────────────────────────

def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """使用 tiktoken 估算 token 数（OpenAI 模型）。对 Anthropic 模型做近似估算。"""
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def max_context_tokens(model: str) -> int:
    """返回模型的上下文窗口大小。"""
    defaults: dict[str, int] = {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
        "claude-sonnet-4-20250514": 200_000,
        "claude-3-haiku-20240307": 200_000,
    }
    return defaults.get(model, 128_000)


# ── 模型路由 ────────────────────────────────────────────

class ModelRouter:
    """根据配置创建 LLM 适配器，支持 fallback。"""

    def __init__(self) -> None:
        self._primary_provider = os.environ.get("LLM_PROVIDER", "openai")
        self._primary_model = os.environ.get("LLM_MODEL", "gpt-4o")
        self._fallback_provider = os.environ.get("LLM_FALLBACK_PROVIDER", "")
        self._fallback_model = os.environ.get("LLM_FALLBACK_MODEL", "")

    def get_primary(self) -> LLMAdapter:
        return self._create(self._primary_provider, self._primary_model)

    def get_fallback(self) -> LLMAdapter | None:
        if not self._fallback_provider or not self._fallback_model:
            return None
        return self._create(self._fallback_provider, self._fallback_model)

    @staticmethod
    def _create(provider: str, model: str) -> LLMAdapter:
        if provider == "anthropic":
            return AnthropicAdapter(model=model)
        return OpenAIAdapter(model=model)
