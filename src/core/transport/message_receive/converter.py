"""消息转换器。

负责 MessageEnvelope 和 Message 之间的双向转换。
参考 old/chat/message_receive/message_processor.py 的转换逻辑。
"""

from typing import Any

from mofox_wire import MessageEnvelope
from src.kernel.logger import get_logger
from src.core.models.message import Message, MessageType

logger = get_logger("message_converter")


class MessageConverter:
    """消息转换器。

    负责在 MessageEnvelope 和 Message 之间进行转换。
    Adapter使用MessageEnvelope，Core使用Message。

    Examples:
        >>> converter = MessageConverter()
        >>> message = await converter.envelope_to_message(envelope)
        >>> envelope = await converter.message_to_envelope(message)
    """

    async def envelope_to_message(self, envelope: MessageEnvelope) -> Message:
        """将 MessageEnvelope 转换为 Message。

        Args:
            envelope: 消息信封

        Returns:
            Message: 标准消息对象

        Raises:
            ValueError: 如果消息信封格式不正确
        """
        # 提取消息信息
        message_info = envelope.get("message_info", {})
        message_segment = envelope.get("message_segment", [])

        if not message_info:
            raise ValueError("消息信封缺少 message_info")

        # 解析用户和群组信息
        user_info = message_info.get("user_info", {})
        group_info = message_info.get("group_info", {})

        # 生成 stream_id
        platform = message_info.get("platform", "")
        stream_id = self._generate_stream_id(platform, user_info, group_info)

        # 解析消息类型和内容
        message_type, content, processed_text = self._parse_message_segment(message_segment)

        # 创建 Message 对象
        message = Message(
            message_id=message_info.get("message_id", ""),
            time=message_info.get("time"),
            stream_id=stream_id,
            content=content,
            processed_plain_text=processed_text,
            message_type=message_type,
            sender_id=str(user_info.get("user_id", "")),
            sender_name=user_info.get("user_nickname", ""),
            sender_cardname=user_info.get("user_cardname"),
            platform=platform,
            chat_type=self._determine_chat_type(group_info),
            raw_data=envelope.get("raw_message"),
        )

        logger.debug(f"转换信封为消息: {message.message_id}")
        return message

    async def message_to_envelope(self, message: Message) -> MessageEnvelope:
        """将 Message 转换为 MessageEnvelope。

        Args:
            message: 标准消息对象

        Returns:
            MessageEnvelope: 消息信封

        Raises:
            ValueError: 如果消息对象缺少必要字段
        """
        from mofox_wire import MessageDirection

        # 验证必要字段
        if not message.message_id:
            raise ValueError("消息缺少 message_id")

        if not message.platform:
            raise ValueError("消息缺少 platform")

        if not message.sender_id:
            raise ValueError("消息缺少 sender_id")

        # 构建消息信封
        envelope = MessageEnvelope(
            direction="outgoing",
            message_info={
                "platform": message.platform,
                "message_id": message.message_id,
                "time": message.time,
                "user_id": message.sender_id,
                "stream_id": message.stream_id,
            },
            message_segment=self._build_message_segment(message),
            raw_message=message.raw_data,
        )

        logger.debug(f"转换消息为信封: {message.message_id}")
        return envelope

    def _generate_stream_id(self, platform: str, user_info: dict, group_info: dict) -> str:
        """生成 stream_id。

        使用 ChatStream.generate_stream_id() 生成符合规范的 stream_id。

        Args:
            platform: 平台标识
            user_info: 用户信息字典
            group_info: 群组信息字典

        Returns:
            str: 聊天流ID，使用 SHA-256 哈希

        Raises:
            ValueError: 如果既没有 user_id 也没有 group_id
        """
        from src.core.models.stream import ChatStream

        # 提取 user_id 和 group_id
        user_id = str(user_info.get("user_id", "")) if user_info else ""
        group_id = str(group_info.get("group_id", "")) if group_info else ""

        # 使用 ChatStream.generate_stream_id() 生成 stream_id
        return ChatStream.generate_stream_id(
            platform=platform,
            user_id=user_id,
            group_id=group_id,
        )

    def _parse_message_segment(self, segments: list) -> tuple[MessageType, Any, str]:
        """解析消息段。

        Args:
            segments: 消息段列表

        Returns:
            tuple[MessageType, Any, str]: (消息类型, 消息内容, 处理后的纯文本)
        """
        if not segments:
            return MessageType.TEXT, "", ""

        # 简化实现：只处理第一个段
        first_seg = segments[0]

        if isinstance(first_seg, dict):
            seg_type = first_seg.get("type", "text")
            seg_data = first_seg.get("data", "")

            # 映射类型
            type_mapping = {
                "text": MessageType.TEXT,
                "image": MessageType.IMAGE,
                "voice": MessageType.VOICE,
                "video": MessageType.VIDEO,
                "file": MessageType.FILE,
                "location": MessageType.LOCATION,
                "emoji": MessageType.EMOJI,
                "notice": MessageType.NOTICE,
            }

            message_type = type_mapping.get(seg_type, MessageType.UNKNOWN)

            # 提取纯文本
            if seg_type == "text":
                processed_text = str(seg_data)
            else:
                processed_text = f"[{seg_type}]"

            return message_type, seg_data, processed_text

        # 如果不是字典，直接作为文本处理
        return MessageType.TEXT, str(segments), str(segments)

    def _build_message_segment(self, message: Message) -> list:
        """构建消息段。

        Args:
            message: 消息对象

        Returns:
            list: 消息段列表
        """
        if message.message_type == MessageType.TEXT:
            return [{"type": "text", "data": message.content}]
        elif message.message_type == MessageType.IMAGE:
            return [{"type": "image", "data": message.content}]
        elif message.message_type == MessageType.VOICE:
            return [{"type": "voice", "data": message.content}]
        elif message.message_type == MessageType.VIDEO:
            return [{"type": "video", "data": message.content}]
        elif message.message_type == MessageType.FILE:
            return [{"type": "file", "data": message.content}]
        elif message.message_type == MessageType.EMOJI:
            return [{"type": "emoji", "data": message.content}]
        elif message.message_type == MessageType.NOTICE:
            return [{"type": "notice", "data": message.content}]
        else:
            # 未知类型，默认为文本
            return [{"type": "text", "data": str(message.content)}]

    def _determine_chat_type(self, group_info: dict) -> str:
        """确定聊天类型。

        Args:
            group_info: 群组信息字典

        Returns:
            str: 聊天类型，"group" 或 "private"
        """
        return "group" if group_info else "private"


__all__ = ["MessageConverter"]
