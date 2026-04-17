"""ChannelAdapter 抽象基类与 TestChannelAdapter — 对应 SPEC FR-007。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from src.contracts.models import InboundMessage, OutboundMessage, SendResult, UserIdentity


class ChannelAdapter(ABC):
    """消息渠道抽象基类。所有渠道适配器必须实现此接口。"""

    @abstractmethod
    async def receive_message(self, raw_event: dict[str, Any]) -> InboundMessage:
        """将平台原始事件解析为统一入站消息。"""
        ...

    @abstractmethod
    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        """将统一出站消息转换为平台特定格式并发送。"""
        ...

    @abstractmethod
    async def verify_callback(self, request: dict[str, Any]) -> bool:
        """校验平台回调请求的合法性。"""
        ...

    @abstractmethod
    async def refresh_credentials(self) -> None:
        """刷新平台认证凭据。"""
        ...

    @abstractmethod
    async def get_user_identity(self, platform_uid: str) -> UserIdentity:
        """将平台用户 ID 映射为系统内用户标识。"""
        ...


class TestChannelAdapter(ChannelAdapter):
    """内存实现的测试渠道适配器，用于开发、测试与演示。"""

    __test__ = False

    def __init__(self) -> None:
        self.sent_messages: list[OutboundMessage] = []
        self.users: dict[str, UserIdentity] = {}
        self.inbound_queue: list[InboundMessage] = []
        self._message_counter = 0

    async def receive_message(self, raw_event: dict[str, Any]) -> InboundMessage:
        return InboundMessage(
            channel_type="test",
            platform_uid=raw_event.get("uid", "test_user"),
            message_type=raw_event.get("type", "text"),
            content=raw_event.get("content", ""),
            raw_payload=raw_event,
        )

    async def send_message(self, outbound: OutboundMessage) -> SendResult:
        self._message_counter += 1
        self.sent_messages.append(outbound)
        return SendResult(success=True, message_id=f"test_msg_{self._message_counter}")

    async def verify_callback(self, request: dict[str, Any]) -> bool:
        return request.get("token") == "test_token"

    async def refresh_credentials(self) -> None:
        pass  # 测试适配器无需刷新凭据

    async def get_user_identity(self, platform_uid: str) -> UserIdentity:
        if platform_uid in self.users:
            return self.users[platform_uid]
        return UserIdentity(
            user_id=platform_uid,
            display_name=f"TestUser_{platform_uid}",
            channel_type="test",
            platform_uid=platform_uid,
        )


class ChannelRegistry:
    """渠道注册表 — 按 channel_type 动态加载适配器。"""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, channel_type: str, adapter: ChannelAdapter) -> None:
        self._adapters[channel_type] = adapter

    def get(self, channel_type: str) -> ChannelAdapter | None:
        return self._adapters.get(channel_type)

    def list_channels(self) -> list[str]:
        return list(self._adapters.keys())
