"""LLM context management utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .payload import LLMPayload
from .roles import ROLE

CompressionHook = Callable[[list[list[LLMPayload]], list[LLMPayload]], list[LLMPayload]]


@dataclass(slots=True)
class LLMContextManager:
    """上下文管理器，负责根据 max_payloads 限制对上下文进行裁剪。
    
    重载 maybe_trim 方法实现裁剪逻辑，默认按照“保留开头的系统/工具消息 + 最近的用户/助手消息”的策略进行裁剪。
    """

    max_payloads: int | None = None
    compression_hook: CompressionHook | None = None

    def maybe_trim(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
        if self.max_payloads is None or self.max_payloads <= 0:
            return payloads
        if len(payloads) <= self.max_payloads:
            return payloads
        return self._trim(payloads, self.max_payloads)

    def _trim(self, payloads: list[LLMPayload], max_payloads: int) -> list[LLMPayload]:
        pinned, tail = self._split_pinned_prefix(payloads)
        groups = self._build_qa_groups(tail)
        if not groups:
            return payloads

        kept_groups = list(groups)
        dropped_groups: list[list[LLMPayload]] = []

        while len(kept_groups) > 1 and self._payload_len(pinned, kept_groups) > max_payloads:
            dropped_groups.append(kept_groups.pop(0))

        remaining_payloads = self._flatten_groups(kept_groups)
        hook_payloads = self._apply_compression_hook(dropped_groups, remaining_payloads)

        if hook_payloads:
            remaining_payloads = self._flatten_groups(kept_groups)

        while len(kept_groups) > 1 and (
            len(pinned) + len(hook_payloads) + len(remaining_payloads) > max_payloads
        ):
            kept_groups.pop(0)
            remaining_payloads = self._flatten_groups(kept_groups)

        return pinned + hook_payloads + remaining_payloads

    def _split_pinned_prefix(self, payloads: list[LLMPayload]) -> tuple[list[LLMPayload], list[LLMPayload]]:
        pinned_roles = {ROLE.SYSTEM, ROLE.TOOL}
        idx = 0
        for payload in payloads:
            if payload.role in pinned_roles:
                idx += 1
                continue
            break
        return payloads[:idx], payloads[idx:]

    def _build_qa_groups(self, payloads: list[LLMPayload]) -> list[list[LLMPayload]]:
        groups: list[list[LLMPayload]] = []
        current: list[LLMPayload] = []
        prelude: list[LLMPayload] = []

        for payload in payloads:
            if payload.role == ROLE.USER:
                if current:
                    groups.append(current)
                if prelude:
                    current = prelude + [payload]
                    prelude = []
                else:
                    current = [payload]
                continue

            if not current:
                prelude.append(payload)
                continue

            current.append(payload)

        if current:
            groups.append(current)
        elif prelude:
            groups.append(prelude)

        return groups

    def _apply_compression_hook(
        self,
        dropped_groups: list[list[LLMPayload]],
        remaining_payloads: list[LLMPayload],
    ) -> list[LLMPayload]:
        if not self.compression_hook or not dropped_groups:
            return []
        return self.compression_hook(dropped_groups, remaining_payloads)

    def _flatten_groups(self, groups: list[list[LLMPayload]]) -> list[LLMPayload]:
        return [payload for group in groups for payload in group]

    def _payload_len(self, pinned: list[LLMPayload], groups: list[list[LLMPayload]]) -> int:
        return len(pinned) + sum(len(group) for group in groups)
