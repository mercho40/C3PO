"""End-to-end over a real WebSocket: browser frame in, velocity command out.

The unit tests either side of this one exercise the parser and the session in
isolation. This one runs the actual `serve()` handler on an ephemeral port and
drives it with a real client, so it covers the seams those miss: JSON on the
wire, the single-session refusal, the status messages the UI reads, and
whether a disconnect actually stops the robot.

Only DDS is faked. Everything above it is the real code path.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from bridge.teleop import server as srv
from bridge.teleop.protocol import PROTOCOL_VERSION


@pytest.fixture
def sent(monkeypatch):
    commands: list[tuple[float, float, float, float]] = []

    async def fake_send(vx, vy, vyaw, height):
        commands.append((vx, vy, vyaw, height))

    monkeypatch.setattr("bridge.skills._locomotion.send_velocity_async", fake_send)
    return commands


@pytest.fixture(autouse=True)
def _no_stale_session():
    """Nothing may leak the module-level session between tests."""
    srv._active = None
    yield
    srv._active = None


async def _serve():
    server = await serve(srv.handle_client, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


def _frame(seq: int, **overrides) -> str:
    payload = {
        "v": PROTOCOL_VERSION,
        "seq": seq,
        "t": float(seq),
        "enabled": True,
        "walk": 0.0,
        "arms": False,
        "head": {"yaw": 0.0, "pos": [0.0, 1.6, 0.0]},
        "hands": {},
    }
    payload.update(overrides)
    return json.dumps(payload)


async def test_a_walk_frame_reaches_the_velocity_channel(sent):
    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send(_frame(1, walk=1.0))
            for _ in range(50):
                if any(c[0] != 0.0 for c in sent):
                    break
                await asyncio.sleep(0.02)

    assert any(c[0] == pytest.approx(srv.WALK_MAX_VEL) for c in sent)


async def test_head_yaw_reaches_the_velocity_channel(sent):
    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send(_frame(1, head={"yaw": srv.YAW_FULL_SCALE_RAD, "pos": [0, 1.6, 0]}))
            for _ in range(50):
                if any(c[2] != 0.0 for c in sent):
                    break
                await asyncio.sleep(0.02)

    assert any(c[2] == pytest.approx(srv.YAW_MAX_RAD_S) for c in sent)


async def test_disconnecting_stops_the_robot(sent):
    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send(_frame(1, walk=1.0))
            for _ in range(50):
                if any(c[0] != 0.0 for c in sent):
                    break
                await asyncio.sleep(0.02)
        # Socket closed. The stop must not wait for the firmware's own 1 s
        # duration to expire.
        await asyncio.sleep(0.2)

    assert sent[-1] == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)


async def test_the_client_receives_status(sent):
    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send(_frame(1))
            raw = await asyncio.wait_for(ws.recv(), timeout=3.0)

    status = json.loads(raw)
    assert status["type"] == "status"
    # The UI reads these to explain why the arms are not moving.
    assert status["hands"] == "none"
    assert status["arm"]["engaged"] is False


async def test_a_second_operator_is_refused_not_interleaved(sent):
    # Two headsets driving one robot is not a mode this system has. Letting
    # the second connect and silently interleave setpoints is the worst
    # available answer.
    server, url = await _serve()
    async with server:
        async with connect(url) as first:
            await first.send(_frame(1))
            await asyncio.sleep(0.1)

            with pytest.raises(Exception) as excinfo:
                async with connect(url) as second:
                    await second.recv()
            assert "1013" in str(excinfo.value) or "already active" in str(excinfo.value)


async def test_the_slot_is_freed_after_the_first_operator_leaves(sent):
    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send(_frame(1))
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.2)

        async with connect(url) as second:
            await second.send(_frame(1))
            raw = await asyncio.wait_for(second.recv(), timeout=3.0)
            assert json.loads(raw)["type"] == "status"


async def test_malformed_frames_do_not_kill_the_session(sent):
    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send("{not json")
            await ws.send(json.dumps({"v": 99, "seq": 1}))
            await ws.send(_frame(1, walk=1.0))
            for _ in range(50):
                if any(c[0] != 0.0 for c in sent):
                    break
                await asyncio.sleep(0.02)

    # The good frame still got through, after two bad ones.
    assert any(c[0] == pytest.approx(srv.WALK_MAX_VEL) for c in sent)


async def test_the_exact_payload_the_browser_client_builds(sent):
    """Guard against the web and bridge halves drifting apart.

    This is a transcription of what `apps/web/src/lib/teleop/stream.ts` puts on
    the wire — including `handPayload`'s `{tracked: false}` for an untracked
    hand, which is a different shape from omitting the key. Nothing else in
    this repo type-checks across that boundary: the stream deliberately does
    not go through Eden, so there is no shared type to lean on.
    """
    server, url = await _serve()
    browser_frame = {
        "v": 1,
        "seq": 0,
        "t": 6042,
        "enabled": True,
        "walk": 1,
        "arms": True,
        "head": {"yaw": 0.21, "pos": [0.02, 1.62, -0.05]},
        "hands": {
            "left": {"tracked": False},
            # Arm held out in front, roughly 0.6 m from the estimated
            # shoulder -- extended enough that the session calibrates reach
            # from it (see TeleopSession._maybe_calibrate).
            "right": {
                "tracked": True,
                "pos": [0.071, 1.40, -0.675],
                "quat": [0.0, 0.0, 0.0, 1.0],
                "grip": 0.37,
            },
        },
    }

    async with server:
        async with connect(url) as ws:
            await ws.send(json.dumps(browser_frame))
            # The dispatch loop emits its first status immediately, possibly
            # before the frame is ingested -- read until one reflects it.
            status = {"frames_received": 0}
            for _ in range(20):
                status = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                if status.get("frames_received"):
                    break

    assert status["frames_received"] == 1
    assert status["frames_rejected"] == 0
    # The extended right arm is what calibration measures reach from.
    assert status["calibrated"] is True
    assert any(c[0] == pytest.approx(srv.WALK_MAX_VEL) for c in sent)


async def test_a_reconnect_during_teardown_is_not_refused(sent, monkeypatch):
    """Found on the robot: the session slot outlived the socket.

    Teardown sends a zero-velocity command, which on real hardware is a DDS RPC
    that waits for an ack — not instant. The single-session slot was held for
    that whole window, so a client that dropped and immediately reconnected was
    refused by its own previous self. On a headset that is a Wi-Fi blip ending
    the session outright.

    Stub-mode teardown is instantaneous, which is exactly why every existing
    test passed. So this one makes teardown slow on purpose.
    """
    real_stop = srv._safe_stop

    async def slow_stop(session):
        await asyncio.sleep(0.4)
        await real_stop(session)

    monkeypatch.setattr(srv, "_safe_stop", slow_stop)

    server, url = await _serve()
    async with server:
        async with connect(url) as first:
            await first.send(_frame(1))
            await asyncio.sleep(0.1)
        # No pause: reconnect while the previous teardown is still running.
        async with connect(url) as second:
            await second.send(_frame(1))
            raw = await asyncio.wait_for(second.recv(), timeout=5.0)
            assert json.loads(raw)["type"] == "status"


async def test_a_genuine_second_operator_is_still_refused(sent, monkeypatch):
    # The grace period must not become a queue. Someone actually holding the
    # session still turns a second connection away, and promptly.
    monkeypatch.setattr(srv, "RECONNECT_GRACE_S", 0.3)

    server, url = await _serve()
    async with server:
        async with connect(url) as first:
            await first.send(_frame(1))
            await asyncio.sleep(0.1)

            started = asyncio.get_running_loop().time()
            with pytest.raises(Exception) as excinfo:
                async with connect(url) as second:
                    await second.recv()
            waited = asyncio.get_running_loop().time() - started

            assert "1013" in str(excinfo.value) or "already active" in str(excinfo.value)
            assert waited < 2.0, "refusal should be prompt, not a long block"


async def test_teardown_does_not_wait_out_an_unanswered_stop(sent, monkeypatch):
    """A stop the robot never acks must not hold the session slot.

    `send_velocity_async` is a DDS RPC with 10 s of headroom. On the robot with
    no motion controller loaded it simply never answers — so teardown took the
    full timeout, the single-session slot was held throughout, and a client
    reconnecting was refused by its own previous session, past even the grace
    period.

    Teardown is best-effort: underneath it the firmware's own `duration`
    deadman stops the robot within a second of the last setpoint regardless.
    Waiting for the ack makes nothing safer.
    """
    started = asyncio.Event()

    async def never_acks(vx, vy, vyaw, height):
        started.set()
        await asyncio.sleep(30)  # the ack that never comes

    monkeypatch.setattr("bridge.skills._locomotion.send_velocity_async", never_acks)

    server, url = await _serve()
    async with server:
        async with connect(url) as ws:
            await ws.send(_frame(1, walk=1.0))
            await asyncio.wait_for(started.wait(), timeout=3.0)

        # Reconnect immediately. Without the budget this blocks for the full
        # RPC timeout and then refuses.
        began = asyncio.get_running_loop().time()
        async with connect(url, open_timeout=8) as second:
            await second.send(_frame(1))
            raw = await asyncio.wait_for(second.recv(), timeout=8.0)
            assert json.loads(raw)["type"] == "status"
        waited = asyncio.get_running_loop().time() - began

    assert waited < srv.RECONNECT_GRACE_S + 2.0, (
        f"reconnect took {waited:.1f}s — teardown is still waiting on the ack"
    )
