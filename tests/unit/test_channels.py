"""渠道适配器测试 — 对应 SPEC FR-007 测试矩阵。"""

from __future__ import annotations

import pytest

from src.channels.adapter import ChannelRegistry, TestChannelAdapter
from src.contracts.models import OutboundMessage


@pytest.fixture
def adapter() -> TestChannelAdapter:
    return TestChannelAdapter()


@pytest.fixture
def registry(adapter: TestChannelAdapter) -> ChannelRegistry:
    reg = ChannelRegistry()
    reg.register("test", adapter)
    return reg


class TestTestChannelAdapter:
    """TestChannelAdapter 全流程验证。"""

    @pytest.mark.asyncio
    async def test_receive_message(self, adapter: TestChannelAdapter) -> None:
        msg = await adapter.receive_message({"uid": "u1", "content": "hello"})
        assert msg.channel_type == "test"
        assert msg.platform_uid == "u1"
        assert msg.content == "hello"

    @pytest.mark.asyncio
    async def test_send_message(self, adapter: TestChannelAdapter) -> None:
        out = OutboundMessage(channel_type="test", target_uid="u1", content="hi")
        result = await adapter.send_message(out)
        assert result.success
        assert result.message_id
        assert len(adapter.sent_messages) == 1

    @pytest.mark.asyncio
    async def test_verify_callback_valid(self, adapter: TestChannelAdapter) -> None:
        assert await adapter.verify_callback({"token": "test_token"})

    @pytest.mark.asyncio
    async def test_verify_callback_invalid(self, adapter: TestChannelAdapter) -> None:
        assert not await adapter.verify_callback({"token": "wrong"})

    @pytest.mark.asyncio
    async def test_get_user_identity(self, adapter: TestChannelAdapter) -> None:
        identity = await adapter.get_user_identity("u42")
        assert identity.user_id == "u42"
        assert identity.channel_type == "test"


class TestChannelRegistry:
    def test_register_and_get(self, registry: ChannelRegistry, adapter: TestChannelAdapter) -> None:
        assert registry.get("test") is adapter

    def test_get_unknown(self, registry: ChannelRegistry) -> None:
        assert registry.get("unknown") is None

    def test_list_channels(self, registry: ChannelRegistry) -> None:
        assert "test" in registry.list_channels()
