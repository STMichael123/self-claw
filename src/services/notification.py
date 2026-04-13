"""通知服务 — 通过渠道抽象层发送通知 — 对应 SPEC FR-003。"""

from __future__ import annotations

import structlog

from src.channels.adapter import ChannelRegistry
from src.contracts.models import OutboundMessage, SendResult

logger = structlog.get_logger()


class NotificationService:
    """通过已注册的渠道适配器发送通知。"""

    def __init__(self, channel_registry: ChannelRegistry) -> None:
        self._channels = channel_registry

    async def notify(
        self,
        *,
        channel_type: str,
        target_uid: str,
        content: str,
        format: str = "text",
    ) -> SendResult:
        adapter = self._channels.get(channel_type)
        if adapter is None:
            logger.warning("channel_not_configured", channel_type=channel_type)
            return SendResult(success=False, error=f"Channel '{channel_type}' not configured")

        msg = OutboundMessage(
            channel_type=channel_type,
            target_uid=target_uid,
            format=format,
            content=content,
        )
        result = await adapter.send_message(msg)
        if not result.success:
            logger.error("notification_send_failed", channel_type=channel_type, error=result.error)
        return result
