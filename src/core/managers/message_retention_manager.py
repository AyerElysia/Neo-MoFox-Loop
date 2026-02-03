"""消息保留管理器

负责实现短期上下文策略，包括：
1. 定期修剪超出的旧消息
2. 清理过期消息
3. 维护消息序号
"""

import time
from typing import TYPE_CHECKING

from src.kernel.db import CRUDBase, QueryBuilder
from src.kernel.scheduler import unified_scheduler, TriggerType
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.sql_alchemy import Messages, ChatStreams

logger = get_logger("message_retention", display="MsgRetention")


class MessageRetentionManager:
    """消息保留管理器"""

    def __init__(self) -> None:
        """初始化消息保留管理器"""
        # 延迟导入避免循环依赖
        from src.core.models.sql_alchemy import Messages, ChatStreams

        self.messages_crud = CRUDBase[Messages](Messages)
        self.streams_crud = CRUDBase[ChatStreams](ChatStreams)
        self._Messages = Messages
        self._ChatStreams = ChatStreams

    async def trim_stream_messages(
        self,
        stream_id: str,
        max_count: int | None = None,
    ) -> int:
        """修剪指定聊天流的消息，保留最近的 max_count 条

        Args:
            stream_id: 聊天流ID
            max_count: 保留的最大消息数，None时从 ChatStreams.context_window_size 读取

        Returns:
            删除的消息数量
        """
        # 1. 获取保留窗口大小
        if max_count is None:
            stream = await self.streams_crud.get_by(stream_id=stream_id)
            if not stream:
                logger.warning(f"聊天流 {stream_id} 不存在")
                return 0
            max_count = stream.context_window_size

        # 2. 查询当前消息总数
        total_count = await QueryBuilder(self._Messages).filter(
            stream_id=stream_id
        ).count()

        if total_count <= max_count:
            return 0

        # 3. 计算需要删除的数量
        delete_count = total_count - max_count

        # 4. 使用复合索引 (stream_id, sequence_number) 高效查询要删除的消息
        messages_to_delete = await QueryBuilder(self._Messages).filter(
            stream_id=stream_id
        ).order_by("sequence_number").limit(delete_count).all(as_dict=True)

        # 5. 批量删除
        deleted = 0
        for msg in messages_to_delete:
            if await self.messages_crud.delete(msg["id"]):
                deleted += 1

        logger.info(
            f"修剪聊天流 {stream_id} 的消息："
            f"删除 {deleted}/{delete_count} 条，保留 {max_count} 条"
        )

        return deleted

    async def clean_expired_messages(self, batch_size: int = 1000) -> int:
        """清理所有过期的消息

        Args:
            batch_size: 每批处理的数量

        Returns:
            清理的消息数量
        """
        now = time.time()

        # 1. 查询所有过期的消息ID
        expired_messages = await QueryBuilder(self._Messages).filter(
            expires_at__lt=now
        ).all(as_dict=True)

        # 2. 批量删除
        deleted = 0
        for msg in expired_messages[:batch_size]:
            if await self.messages_crud.delete(msg["id"]):
                deleted += 1

        if deleted > 0:
            logger.info(f"清理了 {deleted} 条过期消息")

        return deleted

    async def assign_sequence_number(self, stream_id: str) -> int:
        """为聊天流的下一条消息分配序号

        Args:
            stream_id: 聊天流ID

        Returns:
            下一条消息的序号
        """
        # 1. 查询当前最大序号
        max_seq_msg = (
            await QueryBuilder(self._Messages)
            .filter(stream_id=stream_id)
            .order_by("-sequence_number")
            .first(as_dict=True)
        )

        if max_seq_msg:
            return max_seq_msg["sequence_number"] + 1
        else:
            return 1

    async def add_message_with_retention(
        self,
        stream_id: str,
        message_data: dict,
        ttl_seconds: int | None = None,
    ):
        """添加消息并自动执行保留策略

        Args:
            stream_id: 聊天流ID
            message_data: 消息数据
            ttl_seconds: 消息存活时间（秒），None 表示永不过期

        Returns:
            添加的消息实例
        """
        # 1. 分配序号
        sequence = await self.assign_sequence_number(stream_id)

        # 2. 设置过期时间
        if ttl_seconds is not None:
            expires_at = time.time() + ttl_seconds
        else:
            expires_at = None

        # 3. 构建消息数据
        message_data["sequence_number"] = sequence
        message_data["expires_at"] = expires_at

        # 4. 创建消息
        message = await self.messages_crud.create(message_data)

        # 5. 获取保留窗口大小
        stream = await self.streams_crud.get_by(stream_id=stream_id)
        if stream:
            max_count = stream.context_window_size

            # 6. 修剪超出的旧消息
            await self.trim_stream_messages(stream_id, max_count)

        return message

    def start_periodic_cleanup(self, interval_seconds: int = 3600) -> None:
        """启动定期清理任务

        Args:
            interval_seconds: 清理间隔（秒）
        """
        async def cleanup_task():
            await self.clean_expired_messages()

        unified_scheduler.create_schedule(
            callback=cleanup_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"interval_seconds": interval_seconds},
            is_recurring=True,
            task_name="message_retention_cleanup",
        )

        logger.info(f"启动消息定期清理任务，间隔：{interval_seconds}秒")


# 全局单例
_retention_manager: MessageRetentionManager | None = None


def get_message_retention_manager() -> MessageRetentionManager:
    """获取消息保留管理器单例

    Returns:
        MessageRetentionManager: 消息保留管理器实例
    """
    global _retention_manager
    if _retention_manager is None:
        _retention_manager = MessageRetentionManager()
    return _retention_manager


__all__ = [
    "MessageRetentionManager",
    "get_message_retention_manager",
]
