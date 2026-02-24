"""LLM 响应模块

提供 LLMResponse 类，统一处理流式和非流式响应。

LLMResponse 支持：
- await 模式：收集完整响应
- async for 模式：流式处理响应
- 自动追加响应到上下文
- 工具调用处理
"""

from __future__ import annotations

import json
from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import Any, AsyncIterator, Self, TYPE_CHECKING

from .exceptions import LLMResponseConsumedError
from .model_client import StreamEvent
from .payload import LLMPayload, Text, ToolCall
from .roles import ROLE
from .tool_call_compat import parse_tool_call_compat_response

if TYPE_CHECKING:
    from .request import LLMRequest
    from .types import ModelSet
    from .context import LLMContextManager


@dataclass(slots=True)
class LLMResponse:
    """LLMResponse：既可 await（收集全量）也可 async for（流式吐出）。"""

    _stream: AsyncIterator[StreamEvent] | None
    _upper: "LLMRequest | LLMResponse"
    _auto_append_response: bool

    payloads: list[LLMPayload]
    model_set: "ModelSet"
    context_manager: LLMContextManager | None = None

    message: str | None = None
    call_list: list[ToolCall] | None = None
    tool_call_compat: bool = False

    _consumed: bool = False

    def __post_init__(self) -> None:
        """Initialize fields that need special handling."""
        if self.call_list is None:
            object.__setattr__(self, "call_list", [])
        if self.context_manager is None:
            ctx = getattr(self._upper, "context_manager", None)
            if ctx:
                object.__setattr__(self, "context_manager", ctx)

    def _maybe_apply_tool_call_compat(self) -> None:
        if not self.tool_call_compat:
            return
        if self.call_list:
            return
        if not self.message:
            return

        parsed_message, parsed_calls = parse_tool_call_compat_response(self.message)
        self.message = parsed_message
        self.call_list = [
            ToolCall(id=call.get("id"), name=call.get("name", ""), args=call.get("args", {}))
            for call in parsed_calls
        ]

    def __await__(self):
        return self._collect_full_response().__await__()

    async def __aiter__(self):
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            content = self.message or ""
            if content:
                yield content
            return

        full_content: list[str] = []
        tool_acc = _ToolCallAccumulator()
        stream_error: Exception | None = None
        try:
            async for event in self._stream:
                if event.text_delta:
                    full_content.append(event.text_delta)
                    yield event.text_delta
                if event.tool_name or event.tool_args_delta or event.tool_call_id:
                    tool_acc.apply(event)
        except Exception as e:
            # 部分 provider/SDK 会在流尾抛出"连接关闭"等异常。
            # 先记录异常，确保已收集的内容能正确落库，再重新抛出。
            stream_error = e

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()

        if stream_error is not None:
            raise stream_error

    async def _collect_full_response(self) -> str:
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            self._maybe_append_response_to_context()
            return self.message or ""

        full_content: list[str] = []
        tool_acc = _ToolCallAccumulator()
        stream_error: Exception | None = None
        try:
            async for event in self._stream:
                if event.text_delta:
                    full_content.append(event.text_delta)
                if event.tool_name or event.tool_args_delta or event.tool_call_id:
                    tool_acc.apply(event)
        except Exception as e:
            # 部分 provider/SDK 会在流尾抛出"连接关闭"等异常。
            # 先记录异常，确保已收集的内容能正确落库，再重新抛出。
            stream_error = e

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()

        if stream_error is not None:
            raise stream_error

        return self.message


    def _maybe_append_response_to_context(self) -> None:
        if not self._auto_append_response:
            return

        content_parts: list[object] = []
        if self.message:
            content_parts.append(Text(self.message))
        if self.call_list:
            content_parts.extend(self.call_list)

        if not content_parts:
            return

        # 将 assistant 回复写回 payloads
        self.payloads.append(LLMPayload(ROLE.ASSISTANT, content_parts))  # type: ignore[arg-type]
        self._maybe_apply_context_manager()

    def _maybe_apply_context_manager(self) -> None:
        if not self.context_manager:
            return
        self.payloads = self.context_manager.maybe_trim(self.payloads)

    def to_payload(self) -> LLMPayload:
        content_parts: list[object] = []
        if self.message:
            content_parts.append(Text(self.message))
        if self.call_list:
            content_parts.extend(self.call_list)
        if not content_parts:
            content_parts.append(Text(""))
        return LLMPayload(ROLE.ASSISTANT, content_parts)  # type: ignore[arg-type]

    def add_payload(self, payload: "LLMPayload | LLMResponse", position=None) -> Self:
        if isinstance(payload, LLMResponse):
            payload = payload.to_payload()

        if position is not None:
            self.payloads.insert(int(position), payload)
        else:
            self.payloads.append(payload)
        self._maybe_apply_context_manager()
        return self

    def add_call_reflex(self, results: list[LLMPayload]) -> Self:
        for payload in results:
            self.payloads.append(payload)
        self._maybe_apply_context_manager()
        return self

    async def send(self, auto_append_response: bool = True, *, stream: bool = True) -> "LLMResponse":
        # 延迟导入，避免循环依赖
        from .request import LLMRequest

        req = LLMRequest(
            self.model_set,
            request_name=getattr(self._upper, "request_name", ""),
            context_manager=self.context_manager,
        )
        req.payloads = list(self.payloads)
        return await req.send(auto_append_response=auto_append_response, stream=stream)

    async def stream_with_callback(self, on_chunk: Callable[[str], Awaitable[None]]) -> str:
        """流式响应 + 实时回调。

        适用场景：需要在接收到每个 chunk 时立即执行某些操作（如 UI 更新）。

        Args:
            on_chunk: 异步回调函数，接收每个文本 chunk。

        Returns:
            完整的响应文本。

        Raises:
            LLMResponseConsumedError: 如果响应已被消费。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            content = self.message or ""
            if content:
                await on_chunk(content)
            self._maybe_append_response_to_context()
            return content

        full_content: list[str] = []
        tool_acc = _ToolCallAccumulator()
        async for event in self._stream:
            if event.text_delta:
                full_content.append(event.text_delta)
                await on_chunk(event.text_delta)
            if event.tool_name or event.tool_args_delta or event.tool_call_id:
                tool_acc.apply(event)

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()
        return self.message

    async def stream_with_buffer(self, buffer_size: int = 10) -> AsyncIterator[str]:
        """带缓冲的流式响应。

        适用场景：减少回调次数，累积多个 chunk 后再 yield。

        Args:
            buffer_size: 缓冲区大小（字符数），达到此大小后才 yield。

        Yields:
            缓冲后的文本块。

        Raises:
            LLMResponseConsumedError: 如果响应已被消费。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            content = self.message or ""
            if content:
                yield content
            self._maybe_append_response_to_context()
            return

        full_content: list[str] = []
        buffer: list[str] = []
        buffer_len = 0
        tool_acc = _ToolCallAccumulator()

        stream_error: Exception | None = None
        try:
            async for event in self._stream:
                if event.text_delta:
                    full_content.append(event.text_delta)
                    buffer.append(event.text_delta)
                    buffer_len += len(event.text_delta)

                    if buffer_len >= buffer_size:
                        buffered = "".join(buffer)
                        yield buffered
                        buffer.clear()
                        buffer_len = 0

                if event.tool_name or event.tool_args_delta or event.tool_call_id:
                    tool_acc.apply(event)
        except Exception as e:
            # 有些 provider/SDK 会在流尾抛出“连接关闭”等异常。
            # 对于带 buffer 的消费方式，这会导致最后未 flush 的片段丢失。
            # 这里先记录异常，确保尾段 flush，再把异常抛出。
            stream_error = e

        # 剩余内容（无论正常结束还是异常结束，都尽量 flush）
        if buffer:
            yield "".join(buffer)

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()

        if stream_error is not None:
            raise stream_error


class _ToolCallAccumulator:
    """把 OpenAI 风格的 tool_call 增量拼成最终 ToolCall 列表。

    OpenAI 流式协议中，工具调用分多个 chunk 传输：
    - 首个 chunk：携带 tool_call_id + tool_name（以及可能的首段 args）
    - 后续 chunk：tool_call_id 可能为 None，仅携带 tool_args_delta

    因此需要追踪"当前活跃 id"，将无 id 的增量归属到最近一次出现的工具调用。
    """

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._current_id: str | None = None  # 追踪最近一次有效的 tool_call_id

    def apply(self, event: StreamEvent) -> None:
        # 优先使用事件携带的 id；若无则沿用上一次的 id（OpenAI 后续 chunk 不重复发送 id）
        effective_id = event.tool_call_id or self._current_id
        if not effective_id:
            # 既无新 id 又无历史 id，无法归属，丢弃
            return

        if effective_id not in self._by_id:
            self._by_id[effective_id] = {"id": effective_id, "name": None, "args": ""}
            self._order.append(effective_id)

        # 更新当前活跃 id
        if event.tool_call_id:
            self._current_id = event.tool_call_id

        rec = self._by_id[effective_id]
        if event.tool_name:
            rec["name"] = event.tool_name
        if event.tool_args_delta:
            rec["args"] = (rec.get("args") or "") + event.tool_args_delta

    def finalize(self) -> list[ToolCall]:
        out: list[ToolCall] = []
        for tool_call_id in self._order:
            rec = self._by_id[tool_call_id]
            name = rec.get("name") or ""
            args_raw = rec.get("args") or ""
            args: dict[str, Any] | str
            if not args_raw:
                args = {}
            else:
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = args_raw

            out.append(ToolCall(id=tool_call_id, name=name, args=args))
        return out
