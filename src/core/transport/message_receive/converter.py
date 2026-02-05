"""消息转换器。

负责 MessageEnvelope 和 Message 之间的双向转换。
参考 old/chat/message_receive/message_processor.py 的转换逻辑。
"""

import re
import time
from typing import Any

from mofox_wire import MessageEnvelope
from mofox_wire.types import SegPayload
from src.kernel.logger import get_logger
from src.core.models.message import Message, MessageType

logger = get_logger("message_converter")

# 预编译正则表达式
_AT_PATTERN = re.compile(r"^([^:]+):(.+)$")

# 常量定义：段类型集合
RECURSIVE_SEGMENT_TYPES = frozenset(["seglist"])
MEDIA_SEGMENT_TYPES = frozenset(["image", "emoji", "voice", "video"])
METADATA_SEGMENT_TYPES = frozenset(["mention_bot", "priority_info"])
SPECIAL_SEGMENT_TYPES = frozenset(["at", "reply", "file"])


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

        # 提取 reply_to
        reply_to = self._extract_reply_from_segment(message_segment)

        # 处理时间戳
        message_time = message_info.get("time", time.time())
        if isinstance(message_time, int):
            message_time = float(message_time / 1000)

        # 提取 additional_config 信息
        additional_config = message_info.get("additional_config", {})
        is_notify = False
        is_public_notice = False
        notice_type = None
        
        if isinstance(additional_config, dict):
            is_notify = additional_config.get("is_notice", False)
            is_public_notice = additional_config.get("is_public_notice", False)
            notice_type = additional_config.get("notice_type")

        # 创建 Message 对象
        message = Message(
            message_id=message_info.get("message_id", ""),
            time=message_time,
            stream_id=stream_id,
            reply_to=reply_to,
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

        # 设置运行时属性（不是 Message 的核心字段）
        if is_notify:
            setattr(message, "is_notify", True)
            setattr(message, "is_public_notice", is_public_notice)
            setattr(message, "notice_type", notice_type)

        # 从 message_segment 中提取的状态信息
        # 注：这些信息已经在 _parse_message_segment 中记录到 state 中，
        # 但是因为没有返回，所以我们需要重新解析
        state = {}
        self._process_segments_recursive(message_segment, state)
        
        if state.get("is_mentioned"):
            setattr(message, "is_mentioned", True)
        if state.get("is_at"):
            setattr(message, "is_at", True)

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

    def _parse_message_segment(self, segments: SegPayload | list[SegPayload]) -> tuple[MessageType, Any, str]:
        """解析消息段（递归处理）。

        Args:
            segments: 消息段或消息段列表

        Returns:
            tuple[MessageType, Any, str]: (消息类型, 消息内容, 处理后的纯文本)
        """
        if not segments:
            return MessageType.TEXT, "", ""

        # 初始化解析状态
        state = {
            "message_type": MessageType.TEXT,
            "has_image": False,
            "has_voice": False,
            "has_video": False,
            "has_emoji": False,
            "has_file": False,
            "is_at": False,
            "is_mentioned": False,
        }

        # 递归解析消息段
        processed_text = self._process_segments_recursive(segments, state)

        # 确定主消息类型（优先级：video > voice > image > emoji > file > text）
        if state["has_video"]:
            message_type = MessageType.VIDEO
        elif state["has_voice"]:
            message_type = MessageType.VOICE
        elif state["has_image"]:
            message_type = MessageType.IMAGE
        elif state["has_emoji"]:
            message_type = MessageType.EMOJI
        elif state["has_file"]:
            message_type = MessageType.FILE
        else:
            message_type = state["message_type"]

        return message_type, processed_text, processed_text

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

    def _process_segments_recursive(
        self,
        segment: SegPayload | list[SegPayload],
        state: dict[str, Any],
    ) -> str:
        """递归处理消息段，转换为文字描述。

        Args:
            segment: 要处理的消息段（单个或列表）
            state: 处理状态字典（用于记录消息类型标记）

        Returns:
            str: 处理后的文本
        """
        # 如果是列表，遍历处理
        if isinstance(segment, list):
            segments_text = []
            for seg in segment:
                processed = self._process_segments_recursive(seg, state)
                if processed:
                    segments_text.append(processed)
            return " ".join(segments_text)

        # 如果是单个段
        if isinstance(segment, dict):
            seg_type = segment.get("type", "")
            seg_data = segment.get("data")

            # 处理 seglist 类型（递归）
            if seg_type == "seglist" and isinstance(seg_data, list):
                segments_text = []
                for sub_seg in seg_data:
                    processed = self._process_segments_recursive(sub_seg, state)
                    if processed:
                        segments_text.append(processed)
                return " ".join(segments_text)

            # 处理其他类型
            return self._process_single_segment(segment, state)

        return ""

    def _process_single_segment(self, segment: SegPayload, state: dict[str, Any]) -> str:
        """处理单个消息段。

        Args:
            segment: 消息段
            state: 处理状态字典

        Returns:
            str: 处理后的文本
        """
        seg_type = segment.get("type", "")
        seg_data = segment.get("data")

        try:
            if seg_type == "text":
                return str(seg_data) if seg_data else ""

            elif seg_type == "at":
                state["is_at"] = True
                # 处理at消息，格式为"@<昵称:QQ号>"
                if isinstance(seg_data, str):
                    match = _AT_PATTERN.match(seg_data)
                    if match:
                        nickname, qq_id = match.groups()
                        return f"@<{nickname}:{qq_id}>"
                    logger.warning(f"[at处理] 无法解析格式: '{seg_data}'")
                    return f"@{seg_data}"
                logger.warning(f"[at处理] 数据类型异常: {type(seg_data)}")
                return f"@{seg_data}" if isinstance(seg_data, str) else "@未知用户"

            elif seg_type == "image":
                state["has_image"] = True
                # 图片消息简化描述
                return "[图片]"

            elif seg_type == "emoji":
                state["has_emoji"] = True
                return "[表情包]"

            elif seg_type == "voice":
                state["has_voice"] = True
                return "[语音]"

            elif seg_type == "video":
                state["has_video"] = True
                return "[视频]"

            elif seg_type == "file":
                state["has_file"] = True
                if isinstance(seg_data, dict):
                    file_name = seg_data.get("name", "未知文件")
                    file_size = seg_data.get("size", "未知大小")
                    return f"[文件：{file_name} ({file_size}字节)]"
                return "[文件]"

            elif seg_type == "mention_bot":
                # 机器人被@提及
                if isinstance(seg_data, (int, float)):
                    state["is_mentioned"] = seg_data != 0
                return ""

            elif seg_type == "priority_info":
                # 优先级信息，不显示在文本中
                return ""

            elif seg_type == "reply":
                # 回复消息，不显示在文本中（reply_to 已单独处理）
                return ""

            elif seg_type == "location":
                state["message_type"] = MessageType.LOCATION
                if isinstance(seg_data, dict):
                    lat = seg_data.get("lat", "")
                    lon = seg_data.get("lon", "")
                    title = seg_data.get("title", "某个位置")
                    return f"[位置：{title} ({lat}, {lon})]"
                return "[位置]"

            elif seg_type == "notice":
                state["message_type"] = MessageType.NOTICE
                return str(seg_data) if seg_data else "[通知]"

            else:
                logger.warning(f"未知的消息段类型: {seg_type}")
                return f"[{seg_type}]"

        except Exception as e:
            logger.error(f"处理消息段失败: {e!s}, 类型: {seg_type}, 数据: {seg_data}")
            return f"[处理失败的{seg_type}消息]"

    def _determine_chat_type(self, group_info: dict) -> str:
        """确定聊天类型。

        Args:
            group_info: 群组信息字典

        Returns:
            str: 聊天类型，"group" 或 "private"
        """
        return "group" if group_info else "private"

    def _extract_reply_from_segment(self, segment: SegPayload | list[SegPayload]) -> str | None:
        """从消息段中提取 reply_to 信息。

        Args:
            segment: 消息段（单个或列表）

        Returns:
            str | None: 回复的消息 ID，如果没有则返回 None
        """
        try:
            # 如果是列表，遍历查找
            if isinstance(segment, list):
                for seg in segment:
                    reply_id = self._extract_reply_from_segment(seg)
                    if reply_id:
                        return reply_id
                return None

            # 如果是字典
            if isinstance(segment, dict):
                seg_type = segment.get("type", "")
                seg_data = segment.get("data")

                # 如果是 seglist，递归搜索
                if seg_type == "seglist" and isinstance(seg_data, list):
                    for sub_seg in seg_data:
                        reply_id = self._extract_reply_from_segment(sub_seg)
                        if reply_id:
                            return reply_id

                # 如果是 reply 段，返回 message_id
                elif seg_type == "reply":
                    return str(seg_data) if seg_data else None

        except Exception as e:
            logger.warning(f"提取 reply_to 信息失败: {e}")

        return None


__all__ = ["MessageConverter"]
