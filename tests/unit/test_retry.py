"""LLM 重试与熔断单元测试 — 对应 SPEC NFR-001。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from src.contracts.errors import ErrorCode
from src.models.llm import ChatMessage, LLMAdapter, LLMResponse, StreamChunk
from src.models.retry import LLMRetryWrapper


# ── mock adapters ───────────────────────────────────────


class _FailingAdapter(LLMAdapter):
    """Always fails with a controlled exception."""

    def __init__(self, error_code: str = ErrorCode.UPSTREAM_MODEL_ERROR, fail_n: int = 999):
        self.model = "test-fail"
        self._error_code = error_code
        self._fail_n = fail_n
        self._call_count = 0

    async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=4096):
        self._call_count += 1
        if self._call_count > self._fail_n:
            return LLMResponse(content="ok")
        exc = RuntimeError("model error")
        exc.code = self._error_code
        raise exc

    async def chat_stream(self, messages, **kwargs) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count > self._fail_n:
            yield StreamChunk(delta="ok")
            return
        exc = RuntimeError("stream error")
        exc.code = self._error_code
        raise exc


class _SuccessAdapter(LLMAdapter):
    """Always succeeds."""

    def __init__(self):
        self.model = "test-ok"

    async def chat(self, messages, **kwargs):
        return LLMResponse(content="success")

    async def chat_stream(self, messages, **kwargs):
        yield StreamChunk(delta="success")


class _NonRetryableErrorAdapter(LLMAdapter):
    """Fails with a non-retryable error code."""

    def __init__(self):
        self.model = "test-non-retry"
        self._call_count = 0

    async def chat(self, messages, **kwargs):
        self._call_count += 1
        exc = RuntimeError("schema error")
        exc.code = ErrorCode.SCHEMA_VALIDATION_FAILED
        raise exc

    async def chat_stream(self, messages, **kwargs):
        self._call_count += 1
        exc = RuntimeError("schema error")
        exc.code = ErrorCode.SCHEMA_VALIDATION_FAILED
        raise exc


# ── tests ───────────────────────────────────────────────


class TestRetry:
    @pytest.mark.asyncio
    async def test_retryable_error_retries_then_falls_back(self):
        primary = _FailingAdapter(fail_n=999)
        fallback = _SuccessAdapter()
        wrapper = LLMRetryWrapper(primary, fallback=fallback, base_delay=0.01, max_delay=0.05)

        result = await wrapper.chat([ChatMessage(role="user", content="hi")])
        assert result.content == "success"
        # Should have retried max_retries + 1 = 4 times (0,1,2,3)
        assert primary._call_count == 4

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_immediately(self):
        primary = _NonRetryableErrorAdapter()
        fallback = _SuccessAdapter()
        wrapper = LLMRetryWrapper(primary, fallback=fallback, base_delay=0.01)

        with pytest.raises(RuntimeError, match="schema error"):
            await wrapper.chat([ChatMessage(role="user", content="hi")])
        assert primary._call_count == 1

    @pytest.mark.asyncio
    async def test_succeeds_after_transient_failures(self):
        primary = _FailingAdapter(fail_n=2)
        wrapper = LLMRetryWrapper(primary, base_delay=0.01, max_delay=0.05)

        result = await wrapper.chat([ChatMessage(role="user", content="hi")])
        assert result.content == "ok"
        assert primary._call_count == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff_delay(self):
        wrapper = LLMRetryWrapper(_FailingAdapter(), base_delay=1.0, max_delay=30.0)
        assert wrapper._delay_for_attempt(0) == 1.0
        assert wrapper._delay_for_attempt(1) == 2.0
        assert wrapper._delay_for_attempt(2) == 4.0
        assert wrapper._delay_for_attempt(10) == 30.0  # capped

    @pytest.mark.asyncio
    async def test_retries_exhausted_without_fallback_raises(self):
        primary = _FailingAdapter()
        wrapper = LLMRetryWrapper(primary, base_delay=0.01, max_delay=0.05)

        with pytest.raises(RuntimeError, match="model error"):
            await wrapper.chat([ChatMessage(role="user", content="hi")])


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_triggers_fallback(self):
        primary = _FailingAdapter()
        fallback = _SuccessAdapter()
        wrapper = LLMRetryWrapper(
            primary, fallback=fallback,
            cb_threshold=2, cb_window=60.0,
            base_delay=0.01, max_delay=0.05,
        )

        # Trigger failures to open circuit
        wrapper._failure_timestamps = [100.0, 100.1]
        import time
        original_monotonic = time.monotonic
        time.monotonic = lambda: 100.2  # type: ignore[attr-defined]

        try:
            result = await wrapper.chat([ChatMessage(role="user", content="hi")])
            assert result.content == "success"
            # Should NOT have called primary since circuit was open
            assert primary._call_count == 0
        finally:
            time.monotonic = original_monotonic  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_after_window(self):
        wrapper = LLMRetryWrapper(_FailingAdapter(), cb_threshold=2, cb_window=1.0)

        # Old timestamps outside window
        import time
        now = time.monotonic()
        wrapper._failure_timestamps = [now - 10.0, now - 5.0]
        assert not wrapper._is_circuit_open()

    @pytest.mark.asyncio
    async def test_no_fallback_with_open_circuit_raises(self):
        primary = _FailingAdapter()
        wrapper = LLMRetryWrapper(primary, cb_threshold=1, cb_window=60.0)
        wrapper._failure_timestamps = [100.0]

        import time
        original_monotonic = time.monotonic
        time.monotonic = lambda: 100.1  # type: ignore[attr-defined]

        try:
            with pytest.raises(RuntimeError, match="circuit breaker open"):
                await wrapper.chat([ChatMessage(role="user", content="hi")])
        finally:
            time.monotonic = original_monotonic  # type: ignore[attr-defined]


class TestErrorClassification:
    def test_classify_from_code_attribute(self):
        wrapper = LLMRetryWrapper(_FailingAdapter())
        exc = RuntimeError("test")
        exc.code = ErrorCode.RATE_LIMITED
        assert wrapper._classify_error(exc) == ErrorCode.RATE_LIMITED

    def test_classify_rate_limit_from_name(self):
        wrapper = LLMRetryWrapper(_FailingAdapter())
        exc = RateLimitError("429")
        assert wrapper._classify_error(exc) == ErrorCode.RATE_LIMITED

    def test_classify_timeout(self):
        wrapper = LLMRetryWrapper(_FailingAdapter())
        exc = TimeoutError("timed out")
        assert wrapper._is_retryable(wrapper._classify_error(exc))

    def test_is_retryable(self):
        wrapper = LLMRetryWrapper(_FailingAdapter())
        assert wrapper._is_retryable(ErrorCode.UPSTREAM_MODEL_ERROR)
        assert wrapper._is_retryable(ErrorCode.RATE_LIMITED)
        assert not wrapper._is_retryable(ErrorCode.SCHEMA_VALIDATION_FAILED)
        assert not wrapper._is_retryable(ErrorCode.TOOL_NOT_ALLOWED)


class RateLimitError(Exception):
    pass
