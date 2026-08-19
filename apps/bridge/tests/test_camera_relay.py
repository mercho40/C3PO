"""Tests for the teleimager ZeroMQ -> WebSocket camera relay.

`_broadcast` is tested against fake client objects (no real sockets).
`_pump_frames` is tested against a real `zmq.asyncio` PUB socket on loopback
(no mocking of ZMQ itself) since that wiring -- connecting SUB, subscribing
to everything, receiving frames -- is exactly the part worth catching a
regression in; `_broadcast` itself is monkeypatched out in that test so it
doesn't also need a real WebSocket server.
"""

from __future__ import annotations

import asyncio

import pytest
import zmq
import zmq.asyncio

from bridge import camera_relay


@pytest.fixture(autouse=True)
def clean_clients():
    camera_relay._clients.clear()
    yield
    camera_relay._clients.clear()


class FakeConnection:
    def __init__(self, remote_address=("127.0.0.1", 0), fail: bool = False):
        self.remote_address = remote_address
        self.sent: list[bytes] = []
        self.fail = fail
        self.closed = False

    async def send(self, data: bytes) -> None:
        if self.fail:
            raise ConnectionError("boom")
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_connected_clients():
    a, b = FakeConnection(), FakeConnection()
    camera_relay._clients.update({a, b})

    await camera_relay._broadcast(b"jpeg-bytes")

    assert a.sent == [b"jpeg-bytes"]
    assert b.sent == [b"jpeg-bytes"]


@pytest.mark.asyncio
async def test_broadcast_is_a_noop_with_no_clients():
    # Should not raise / hang with nothing to send to.
    await camera_relay._broadcast(b"jpeg-bytes")


@pytest.mark.asyncio
async def test_broadcast_drops_client_that_fails_to_send():
    good, bad = FakeConnection(), FakeConnection(fail=True)
    camera_relay._clients.update({good, bad})

    await camera_relay._broadcast(b"jpeg-bytes")

    assert good.sent == [b"jpeg-bytes"]
    assert bad not in camera_relay._clients
    assert good in camera_relay._clients
    # Dropped clients must be closed, not merely forgotten -- otherwise the
    # socket leaks and a half-written frame leaves the connection corrupt.
    assert bad.closed
    assert not good.closed


@pytest.mark.asyncio
async def test_pump_frames_relays_real_zmq_messages(monkeypatch):
    # Real PUB/SUB over loopback -- exercises the actual subscribe/connect
    # path, not a mock of it. Port 0 lets the OS pick a free one; ZMQ's
    # ROUTER-style `bind` reports it back via last_endpoint.
    ctx = zmq.asyncio.Context.instance()
    pub = ctx.socket(zmq.PUB)
    port = pub.bind_to_random_port("tcp://127.0.0.1")

    monkeypatch.setattr(camera_relay, "TELEIMAGER_HOST", "127.0.0.1")
    monkeypatch.setattr(camera_relay, "TELEIMAGER_PORT", port)

    received: list[bytes] = []

    async def fake_broadcast(frame: bytes) -> None:
        received.append(frame)

    monkeypatch.setattr(camera_relay, "_broadcast", fake_broadcast)

    task = asyncio.ensure_future(camera_relay._pump_frames())
    try:
        # ZMQ's classic "slow joiner" problem -- the SUB may not finish
        # connecting before the first publish, so keep publishing until it's
        # observed, rather than guessing a fixed sleep.
        deadline = asyncio.get_event_loop().time() + 3.0
        while not received and asyncio.get_event_loop().time() < deadline:
            await pub.send(b"fake-jpeg-frame")
            await asyncio.sleep(0.02)

        assert received, "camera_relay never relayed the published frame"
        assert received[0] == b"fake-jpeg-frame"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pub.close(linger=0)
