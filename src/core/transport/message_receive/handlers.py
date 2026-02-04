"""消息处理器集合。

提供 Notice 等特殊消息类型的处理器。
"""

from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.message import Message

logger = get_logger("message_handlers")


class NoticeHandler:
    """Notice 消息处理器。

    处理通知类消息（戳一戳、禁言等）。

    Notice 消息与普通消息不同，它们不需要完整的消息处理链：
    1. 不触发命令处理
    2. 存储到数据库（可选）
    3. 添加到全局 Notice 管理器（可选）
    4. 触发 ON_NOTICE_RECEIVED 事件供插件监听

    Attributes:
        _enabled: 是否启用 Notice 处理

    Examples:
        >>> handler = NoticeHandler()
        >>> await handler.handle(message)
    """

    def __init__(self) -> None:
        """初始化 Notice 处理器。"""
        self._enabled = True
        logger.debug("NoticeHandler 初始化完成")

    async def handle(self, message: "Message") -> None:
        """处理 Notice 消息。

        Args:
            message: 消息对象
        """
        if not self._enabled:
            logger.debug("Notice 处理器已禁用")
            return

        try:
            # 提取 notice 类型
            notice_type = getattr(message, "notice_type", "unknown")

            logger.info(
                f"处理 Notice 消息: type={notice_type}, "
                f"message_id={message.message_id}"
            )

            # 触发 Notice 事件
            await self._emit_notice_event(message, notice_type)

        except Exception as e:
            logger.error(f"处理 Notice 消息时出错: {e}", exc_info=True)

    async def _emit_notice_event(self, message: "Message", notice_type: str) -> None:
        """触发 Notice 事件。

        Args:
            message: 消息对象
            notice_type: Notice 类型
        """
        try:
            # 尝试从事件管理器获取
            from src.core.managers.event_manager import get_event_manager
            from src.core.components.types import EventType

            event_mgr = get_event_manager()
            await event_mgr.publish_event(
                EventType.ON_NOTICE_RECEIVED,
                {
                    "message": message,
                    "notice_type": notice_type,
                },
            )
        except Exception as e:
            logger.warning(f"触发 Notice 事件失败: {e}")

    def enable(self) -> None:
        """启用 Notice 处理器。

        Examples:
            >>> handler.enable()
        """
        self._enabled = True
        logger.debug("Notice 处理器已启用")

    def disable(self) -> None:
        """禁用 Notice 处理器。

        Examples:
            >>> handler.disable()
        """
        self._enabled = False
        logger.debug("Notice 处理器已禁用")


__all__ = ["NoticeHandler"]
