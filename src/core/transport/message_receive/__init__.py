"""消息接收模块。

负责接收、转换和路由来自 Adapter 的消息。
"""

from src.core.transport.message_receive.converter import MessageConverter
from src.core.transport.message_receive.handlers import NoticeHandler
from src.core.transport.message_receive.message_receiver import (
    MessageReceiver,
    get_message_receiver,
    reset_message_receiver,
    set_message_receiver,
)
from src.core.transport.message_receive.router import MessageRouter

__all__ = [
    # 核心组件
    "MessageReceiver",
    "MessageConverter",
    "MessageRouter",
    "NoticeHandler",
    # 全局函数
    "get_message_receiver",
    "set_message_receiver",
    "reset_message_receiver",
]
