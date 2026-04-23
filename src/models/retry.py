"""LLM 分类重试与熔断 — 对应 SPEC NFR-001。

重试策略：
- 可重试错误（UPSTREAM_MODEL_ERROR、RATE_LIMITED、EMBEDDING_FAILED）：
  指数退避，最多 3 次，base 1s，max 30s。
- 不可重试错误（SCHEMA_VALIDATION_FAILED、TOOL_NOT_ALLOWED 等）：立即失败。
- 连续可重试错误超过阈值（默认 5 次/分钟）时触发熔断，返回 MODEL_FALLBACK。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

import structlog

from src.contracts.errors import ErrorCode
from src.models.llm import ChatMessage, LLMAdapter, LLMResponse, StreamChunk

logger = structlog.get_logger()

RETRYABLE_ERRORS = frozenset({
    ErrorCode.UPSTREAM_MODEL_ERROR,
    ErrorCode.RATE_LIMITED,
    ErrorCode.EMBEDDING_FAILED,
})

MAX_RETRIES = 3
BASE_DELAY_SEC = 1.0
MAX_DELAY_SEC = 30.0
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_WINDOW_SEC = 60.0


class LLMRetryWrapper(LLMAdapter):
    """包装 LLMAdapter，添加分类重试与熔断。"""

    def __init__(
        self,
        wrapped: LLMAdapter,
        *,
        fallback: LLMAdapter | None = None,
        max_retries: int = MAX_RETRIES,
        base_delay: float = BASE_DELAY_SEC,
        max_delay: float = MAX_DELAY_SEC,
        cb_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
        cb_window: float = CIRCUIT_BREAKER_WINDOW_SEC,
    ) -> None:
        self._wrapped = wrapped
        self._fallback = fallback
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._cb_threshold = cb_threshold
        self._cb_window = cb_window
        self._failure_timestamps: list[float] = []

    @property
    def model(self) -> str:
        return getattr(self._wrapped, "model", "")

    def _is_circuit_open(self) -> bool:
        """检查熔断器是否开启。"""
        now = time.monotonic()
        cutoff = now - self._cb_window
        self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]
        return len(self._failure_timestamps) >= self._cb_threshold

    def _record_failure(self) -> None:
        self._failure_timestamps.append(time.monotonic())

    def _classify_error(self, exc: Exception) -> str:
        """将异常映射到 ErrorCode 字符串。"""
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code:
            return str(code)
        exc_type = type(exc).__name__.lower()
        if "ratelimit" in exc_type or "rate" in exc_type or "429" in str(exc):
            return ErrorCode.RATE_LIMITED
        if "timeout" in exc_type or "timedout" in exc_type:
            return ErrorCode.UPSTREAM_MODEL_ERROR
        if "connection" in exc_type:
            return ErrorCode.UPSTREAM_MODEL_ERROR
        return str(code or ErrorCode.UPSTREAM_MODEL_ERROR)

    def _is_retryable(self, error_code: str) -> bool:
        return error_code in RETRYABLE_ERRORS

    def _delay_for_attempt(self, attempt: int) -> float:
        """指数退避：base * 2^attempt，上限 max_delay。"""
        delay = self._base_delay * (2 ** attempt)
        return min(delay, self._max_delay)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        if self._is_circuit_open():
            logger.warning("circuit_breaker_open, trying fallback")
            return await self._try_fallback(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
            )

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._wrapped.chat(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as exc:
                last_error = exc
                error_code = self._classify_error(exc)

                if not self._is_retryable(error_code):
                    logger.error("llm_non_retryable_error", error_code=error_code, attempt=attempt)
                    raise

                self._record_failure()
                if attempt < self._max_retries:
                    delay = self._delay_for_attempt(attempt)
                    logger.warning(
                        "llm_retryable_error_retrying",
                        error_code=error_code,
                        attempt=attempt + 1,
                        max_retries=self._max_retries,
                        delay_sec=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "llm_retries_exhausted",
                        error_code=error_code,
                        attempts=self._max_retries + 1,
                    )

        return await self._try_fallback(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
            exhausted_error=last_error,
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        if self._is_circuit_open():
            logger.warning("circuit_breaker_open_stream, trying fallback")
            if self._fallback:
                async for chunk in self._fallback.chat_stream(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
                ):
                    yield chunk
                return

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                stream = self._wrapped.chat_stream(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
                )
                async for chunk in stream:
                    yield chunk
                return
            except Exception as exc:
                last_error = exc
                error_code = self._classify_error(exc)

                if not self._is_retryable(error_code):
                    raise

                self._record_failure()
                if attempt < self._max_retries:
                    delay = self._delay_for_attempt(attempt)
                    logger.warning(
                        "llm_stream_retry",
                        error_code=error_code,
                        attempt=attempt + 1,
                        delay_sec=delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("llm_stream_retries_exhausted", error_code=error_code)

        if self._fallback:
            async for chunk in self._fallback.chat_stream(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
            ):
                yield chunk

    async def _try_fallback(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        exhausted_error: Exception | None = None,
    ) -> LLMResponse:
        if self._fallback is not None:
            logger.warning("llm_falling_back_to_fallback_model")
            try:
                return await self._fallback.chat(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as fb_exc:
                logger.error("fallback_model_also_failed", error=str(fb_exc))

        if exhausted_error is not None:
            raise exhausted_error
        raise RuntimeError("circuit breaker open and no fallback configured")
