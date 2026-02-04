from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mofox_wire import MessageEnvelope

from src.core.transport.ws import WSAdapterServer


class _ReceiverStub:
    def __init__(self) -> None:
        self.items: list[tuple[MessageEnvelope, str]] = []
        self._lock = asyncio.Lock()

    async def receive_envelope(self, envelope: MessageEnvelope, adapter_signature: str) -> None:
        async with self._lock:
            self.items.append((envelope, adapter_signature))


def _mk_envelope(platform: str = "ext") -> MessageEnvelope:
    return MessageEnvelope(
        direction="incoming",
        message_info={
            "platform": platform,
            "message_id": "m1",
            "user_info": {
                "platform": platform,
                "user_id": "u1",
            },
        },
        message_segment=[{"type": "text", "data": "hi"}],
        raw_message={"hello": "world"},
    )


def test_ws_adapter_server_accepts_envelope_by_path_signature() -> None:
    app = FastAPI()
    server = WSAdapterServer(path="/ws/adapter")
    receiver = _ReceiverStub()
    server.set_message_receiver(receiver)
    server.mount_to_app(app)

    sig = "external:adapter:demo"
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/adapter/{sig}") as ws:
            ws.send_json(_mk_envelope())

    assert len(receiver.items) == 1
    env, got_sig = receiver.items[0]
    assert got_sig == sig
    assert env["message_info"]["message_id"] == "m1"


def test_ws_adapter_server_rejects_missing_signature() -> None:
    app = FastAPI()
    server = WSAdapterServer(path="/ws/adapter")
    server.mount_to_app(app)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/adapter"):
                pass


@pytest.mark.asyncio
async def test_ws_adapter_server_push_outgoing_uses_registered_connection() -> None:
    server = WSAdapterServer()

    sent: dict[str, Any] = {}

    class _FakeWS:
        client_state = type("S", (), {"name": "CONNECTED"})()

        async def send_text(self, text: str) -> None:
            sent["text"] = text

        async def send_bytes(self, data: bytes) -> None:
            sent["bytes"] = data

    sig = "external:adapter:demo"

    # 手动注册 fake 连接（不走真实 ASGI）
    await server._register(sig, _FakeWS())  # type: ignore[attr-defined]

    envelope = _mk_envelope(platform="ext2")
    await server.push_outgoing(sig, envelope, use_binary=False)

    assert "text" in sent
    assert "ext2" in sent["text"]
