"""消息路由器。

负责将 Message 路由到合适的处理器（命令、Chatter 等）。
参考 old/chat/message_receive/message_handler.py 的路由逻辑。
"""

from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.message import Message

logger = get_logger("message_router")


class MessageRouter:
    """消息路由器。

    负责将消息路由到合适的处理器。

    路由优先级：
    1. Notice 消息 → NoticeHandler
    2. 命令消息 → CommandManager
    3. 普通消息 → ChatterManager

    Examples:
        >>> router = MessageRouter()
        >>> await router.route_message(message)
    """

    def __init__(self) -> None:
        """初始化消息路由器。"""
        from src.core.transport.message_receive.handlers import NoticeHandler

        self._notice_handler = NoticeHandler()
        logger.info("MessageRouter 初始化完成")

    async def route_message(self, message: "Message") -> None:
        """路由消息到合适的处理器。

        Args:
            message: 标准消息对象
        """
        try:
            # 1. 检查是否为 Notice 消息
            if message.message_type.value == "notice":
                await self._handle_notice_message(message)
                return

            # 2. 检查是否为命令
            if self._is_command_message(message):
                await self._handle_command_message(message)
                return

            # 3. 默认为普通消息，路由到 Chatter
            await self._handle_normal_message(message)

        except Exception as e:
            logger.error(f"路由消息失败: {e}", exc_info=True)

    def _is_command_message(self, message: "Message") -> bool:
        """检查是否为命令消息。

        Args:
            message: 消息对象

        Returns:
            bool: 是否为命令
        """
        try:
            from src.core.managers.command_manager import get_command_manager

            cmd_manager = get_command_manager()
            text = message.processed_plain_text or ""
            return cmd_manager.is_command(text)
        except Exception:
            return False

    async def _handle_notice_message(self, message: "Message") -> None:
        """处理 Notice 消息。

        Args:
            message: 消息对象
        """
        logger.debug(f"路由 Notice 消息: {message.message_id}")
        await self._notice_handler.handle(message)

    async def _handle_command_message(self, message: "Message") -> None:
        """处理命令消息。

        Args:
            message: 消息对象
        """
        logger.debug(f"路由命令消息: {message.message_id}")

        try:
            from src.core.managers.command_manager import get_command_manager

            cmd_manager = get_command_manager()
            text = message.processed_plain_text or ""
            success, result = await cmd_manager.execute_command(message, text)

            if success:
                logger.info(f"命令执行成功: {result}")
            else:
                logger.warning(f"命令执行失败: {result}")
        except Exception as e:
            logger.error(f"处理命令消息时出错: {e}", exc_info=True)

    async def _handle_normal_message(self, message: "Message") -> None:
        """处理普通消息。

        Args:
            message: 消息对象
        """
        logger.debug(f"路由普通消息: {message.message_id}")

        try:
            # 获取或创建聊天流
            from src.core.managers.stream_manager import get_stream_manager

            stream_mgr = get_stream_manager()
            stream = await stream_mgr.get_or_create_stream(
                stream_id=message.stream_id,
                platform=message.platform,
            )

            # 添加消息到流
            await stream_mgr.add_message(message)

            # 查找合适的 Chatter 并执行
            from src.core.managers.chatter_manager import get_chatter_manager

            chatter_mgr = get_chatter_manager()
            chatter = chatter_mgr.get_chatter_by_stream(message.stream_id)

            if chatter:
                # 获取未读消息
                unreads = [message]

                # 执行 Chatter（生成器模式）
                async for result in chatter.execute(unreads):
                    # 处理 Wait/Success/Failure
                    # 这里可以添加结果处理逻辑
                    pass
            else:
                logger.debug(f"未找到 Chatter: {message.stream_id}")

        except Exception as e:
            logger.error(f"处理普通消息时出错: {e}", exc_info=True)


__all__ = ["MessageRouter"]
