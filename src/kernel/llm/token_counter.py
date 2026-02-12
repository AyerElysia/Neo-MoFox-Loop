from __future__ import annotations

import json

from .payload import LLMPayload, Text, ToolCall, ToolResult


def _get_tiktoken_encoding(model_identifier: str):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model_identifier)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def _serialize_payload(payload: LLMPayload) -> str:
    chunks: list[str] = [f"role:{payload.role.value}"]

    for part in payload.content:
        if isinstance(part, Text):
            chunks.append(part.text)
            continue

        if isinstance(part, ToolResult):
            chunks.append(part.to_text())
            continue

        if isinstance(part, ToolCall):
            chunks.append(part.name)
            if isinstance(part.args, dict):
                chunks.append(json.dumps(part.args, ensure_ascii=False, sort_keys=True))
            else:
                chunks.append(str(part.args))
            continue

        to_schema = getattr(part, "to_schema", None)
        if callable(to_schema):
            try:
                chunks.append(json.dumps(to_schema(), ensure_ascii=False, sort_keys=True))
                continue
            except Exception:
                chunks.append(str(part))
                continue

        text = getattr(part, "text", None)
        if isinstance(text, str):
            chunks.append(text)
            continue

        value = getattr(part, "value", None)
        if isinstance(value, str):
            chunks.append(value)
            continue

        chunks.append(str(part))

    return "\n".join(chunks)


def count_payload_tokens(payloads: list[LLMPayload], *, model_identifier: str) -> int:
    encoding = _get_tiktoken_encoding(model_identifier)
    total = 0
    for payload in payloads:
        serialized = _serialize_payload(payload)
        total += len(encoding.encode(serialized))
    return total


def count_text_tokens(text: str, *, model_identifier: str) -> int:
    encoding = _get_tiktoken_encoding(model_identifier)
    return len(encoding.encode(text))
