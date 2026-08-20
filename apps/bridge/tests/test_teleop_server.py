"""Tests for the teleop session: dead-man behaviour and locomotion dispatch.

The arm and hand drivers are stubbed out -- they have their own tests -- so
these focus on the part that decides whether the robot is allowed to move at
all, and on the guarantee that a session which ends for any reason leaves the
robot stopped.
"""

from __future__ import annotations

import asyncio
import json
import math

import pytest

from bridge.teleop import server as srv
from bridge.teleop.protocol import PROTOCOL_VERSION, parse_frame
from bridge.teleop.server import TeleopSession, yaw_to_vyaw


def frame(**overrides) -> str:
    base = {
        "v": PROTOCOL_VERSION,
        "seq": 1,
        "t": 0.0,
        "enabled": True,
        "walk": 0.0,
        "arms": False,
        "head": {"yaw": 0.0, "pos": [0.0, 1.6, 0.0]},
        "hands": {},
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture
def sent(monkeypatch):
    """Record every velocity command instead of touching DDS."""
    commands: list[tuple[float, float, float, float]] = []

    async def fake_send(vx, vy, vyaw, height):
        commands.append((vx, vy, vyaw, height))

    monkeypatch.setattr("bridge.skills._locomotion.send_velocity_async", fake_send)
    return commands


@pytest.fixture
def session(monkeypatch):
    s = TeleopSession()
    # The default hand driver is already a no-op, but pin it so a stray env
    # var in the developer's shell cannot make this test publish.
    from bridge.teleop.hands import NullHandDriver

    s.hands = NullHandDriver("test")
    yield s
    # A session registers a `teleop_session` task for its lifetime, and the
    # registry is a process-wide singleton. Without this, every test here
    # leaves one "running" and the suites that assert `len(list_active()) == 1`
    # start failing somewhere else entirely.
    s.close()


# -- yaw mapping ------------------------------------------------------------


def test_yaw_inside_the_deadzone_commands_nothing():
    # A worn headset is never still. Without a deadzone the robot would creep
    # continuously in whichever direction the operator's neck is relaxed.
    assert yaw_to_vyaw(0.0) == 0.0
    assert yaw_to_vyaw(srv.YAW_DEADZONE_RAD * 0.99) == 0.0
    assert yaw_to_vyaw(-srv.YAW_DEADZONE_RAD * 0.99) == 0.0


def test_yaw_past_the_deadzone_is_signed_and_capped():
    small = yaw_to_vyaw(srv.YAW_DEADZONE_RAD + 0.01)
    assert 0 < small < srv.YAW_MAX_RAD_S

    assert yaw_to_vyaw(srv.YAW_FULL_SCALE_RAD) == pytest.approx(srv.YAW_MAX_RAD_S)
    assert yaw_to_vyaw(math.pi) == pytest.approx(srv.YAW_MAX_RAD_S)
    assert yaw_to_vyaw(-math.pi) == pytest.approx(-srv.YAW_MAX_RAD_S)


def test_yaw_mapping_is_symmetric():
    for value in (0.2, 0.5, 1.0, 3.0):
        assert yaw_to_vyaw(-value) == pytest.approx(-yaw_to_vyaw(value))


# -- dispatch ---------------------------------------------------------------


@pytest.mark.parametrize("yaw", [srv.YAW_FULL_SCALE_RAD, -srv.YAW_FULL_SCALE_RAD])
async def test_turning_the_head_turns_the_robot(session, sent, yaw):
    session.ingest(parse_frame(frame(head={"yaw": yaw, "pos": [0, 1.6, 0]})))

    await srv._dispatch_once(session, session.last_frame_at)

    assert len(sent) == 1
    vx, _vy, vyaw, _h = sent[0]
    # Head yaw alone rotates in place: no forward component comes with it.
    assert vx == 0.0
    assert vyaw == pytest.approx(math.copysign(srv.YAW_MAX_RAD_S, yaw))


async def test_a_half_turn_of_the_head_gives_a_proportional_rate(session, sent):
    half = srv.YAW_DEADZONE_RAD + (srv.YAW_FULL_SCALE_RAD - srv.YAW_DEADZONE_RAD) / 2
    session.ingest(parse_frame(frame(head={"yaw": half, "pos": [0, 1.6, 0]})))

    await srv._dispatch_once(session, session.last_frame_at)

    assert sent[0][2] == pytest.approx(srv.YAW_MAX_RAD_S / 2)


async def test_walk_axis_drives_forward_and_back(session, sent):
    session.ingest(parse_frame(frame(seq=1, walk=1.0)))
    await srv._dispatch_once(session, session.last_frame_at)
    session.ingest(parse_frame(frame(seq=2, walk=-1.0)))
    await srv._dispatch_once(session, session.last_frame_at)

    assert sent[0][0] == pytest.approx(srv.WALK_MAX_VEL)
    assert sent[1][0] == pytest.approx(-srv.WALK_MAX_VEL)


async def test_releasing_the_deadman_stops_the_robot(session, sent):
    session.ingest(parse_frame(frame(seq=1, walk=1.0)))
    await srv._dispatch_once(session, session.last_frame_at)
    assert session.moving is True

    session.ingest(parse_frame(frame(seq=2, walk=1.0, enabled=False)))
    await srv._dispatch_once(session, session.last_frame_at)

    assert sent[-1] == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)
    assert session.moving is False


async def test_stale_frames_stop_the_robot(session, sent):
    session.ingest(parse_frame(frame(walk=1.0)))
    await srv._dispatch_once(session, session.last_frame_at)
    assert session.moving is True

    # No new frame; the client is gone. Only the absence of frames can tell us.
    await srv._dispatch_once(session, session.last_frame_at + srv.STALE_FRAME_S + 0.1)

    assert sent[-1][0] == 0.0
    assert session.moving is False


async def test_zero_velocity_is_not_re_issued_forever(session, sent):
    # Re-issuing zero at the dispatch rate would keep the firmware's own
    # `duration` deadman permanently refreshed -- the one thing it exists to
    # prevent. One stop command, then silence.
    session.ingest(parse_frame(frame(walk=1.0)))
    await srv._dispatch_once(session, session.last_frame_at)
    session.ingest(parse_frame(frame(seq=2, walk=0.0)))
    for _ in range(5):
        await srv._dispatch_once(session, session.last_frame_at)

    assert len([c for c in sent if c == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)]) == 1


async def test_holding_motion_too_long_trips_the_latch(session, sent, monkeypatch):
    monkeypatch.setattr(srv, "MAX_CONTINUOUS_MOTION_S", 0.05)
    start = 1000.0
    session.ingest(parse_frame(frame(walk=1.0)))
    session.last_frame_at = start
    await srv._dispatch_once(session, start)
    assert session.moving is True

    session.last_frame_at = start + 0.2
    await srv._dispatch_once(session, start + 0.2)

    assert session.deadman_tripped is True
    assert sent[-1][0] == 0.0


async def test_the_latch_re_arms_when_the_operator_lets_go(session, sent, monkeypatch):
    monkeypatch.setattr(srv, "MAX_CONTINUOUS_MOTION_S", 0.05)
    start = 1000.0
    session.ingest(parse_frame(frame(seq=1, walk=1.0)))
    session.last_frame_at = start
    await srv._dispatch_once(session, start)
    session.last_frame_at = start + 0.2
    await srv._dispatch_once(session, start + 0.2)
    assert session.deadman_tripped is True

    # Operator releases: the latch re-arms, so pressing again works.
    session.ingest(parse_frame(frame(seq=2, walk=0.0, enabled=False)))
    session.last_frame_at = start + 0.3
    await srv._dispatch_once(session, start + 0.3)
    assert session.deadman_tripped is False

    session.ingest(parse_frame(frame(seq=3, walk=1.0)))
    session.last_frame_at = start + 0.4
    await srv._dispatch_once(session, start + 0.4)
    assert sent[-1][0] == pytest.approx(srv.WALK_MAX_VEL)


# -- ingest ordering --------------------------------------------------------


def test_out_of_order_frames_are_dropped(session):
    session.ingest(parse_frame(frame(seq=10)))
    session.ingest(parse_frame(frame(seq=5)))

    assert session.last_seq == 10
    assert session.frames_rejected == 1


def test_a_reconnecting_client_resets_the_counter(session):
    session.ingest(parse_frame(frame(seq=10)))
    session.ingest(parse_frame(frame(seq=0)))
    session.ingest(parse_frame(frame(seq=1)))

    assert session.last_seq == 1
    assert session.frames_received == 3


# -- calibration ------------------------------------------------------------


def test_calibration_needs_an_extended_arm(session):
    bent = {"tracked": True, "pos": [0.18, 1.30, 0.0], "quat": [0, 0, 0, 1]}
    session.ingest(parse_frame(frame(hands={"right": bent})))
    assert session.calibrated is False

    extended = {"tracked": True, "pos": [0.18, 1.38 - 0.70, 0.0], "quat": [0, 0, 0, 1]}
    session.ingest(parse_frame(frame(seq=2, hands={"right": extended})))
    assert session.calibrated is True
    assert session.arm_length_m == pytest.approx(0.70, abs=0.02)


# -- shutdown ---------------------------------------------------------------


async def test_safe_stop_always_commands_zero(session, sent):
    await srv._safe_stop(session)

    assert sent[-1] == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)
    assert session.moving is False


async def test_a_cancelled_dispatch_loop_still_stops_the_robot(session, sent):
    class _FakeWs:
        async def send(self, _message):
            return None

    loop = asyncio.create_task(srv._dispatch_loop(session, _FakeWs()))
    session.ingest(parse_frame(frame(walk=1.0)))
    await asyncio.sleep(0.1)
    assert any(c[0] != 0.0 for c in sent)

    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop

    # A client that disconnects mid-turn must not leave the robot rotating
    # until the firmware's own 1 s duration expires.
    assert sent[-1] == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)


# -- arm edge handling ------------------------------------------------------


class _RecordingHands:
    """A hand driver that counts how often it is told to open."""

    name = "recording"
    sides = ("right",)

    def __init__(self) -> None:
        self.relax_calls = 0
        self.sent: list[tuple[str, float]] = []

    def send(self, side, grip):
        self.sent.append((side, grip))

    def relax(self):
        self.relax_calls += 1


async def test_hands_are_relaxed_once_on_the_falling_edge(session, sent):
    # Relaxing every tick would publish hand commands at the dispatch rate for
    # the whole session, to a hand that is already open.
    hands = _RecordingHands()
    session.hands = hands
    session.arms_active = True

    for _ in range(5):
        session.ingest(parse_frame(frame(seq=session.last_seq + 1, arms=False)))
        await srv._dispatch_once(session, session.last_frame_at)

    assert hands.relax_calls == 1
    assert session.arms_active is False


async def test_lowering_the_arms_does_not_stall_locomotion(session, sent, monkeypatch):
    """The release must be requested, not awaited, from the dispatch loop."""
    released = {"awaited": False, "requested": False}

    class _FakeDriver:
        engaged = True

        def request_release(self):
            released["requested"] = True

        async def release(self):
            released["awaited"] = True
            await asyncio.sleep(2.0)

        def status(self):
            return {"engaged": True, "weight": 1.0, "enabled_by_env": True, "sim_mode": "real"}

    monkeypatch.setattr(srv, "get_driver", lambda: _FakeDriver())
    session.arms_active = True
    session.ingest(parse_frame(frame(arms=False, head={"yaw": srv.YAW_FULL_SCALE_RAD, "pos": [0, 1.6, 0]})))

    await asyncio.wait_for(srv._dispatch_once(session, session.last_frame_at), timeout=0.5)

    assert released["requested"] is True
    assert released["awaited"] is False
    # And the turn the operator is still asking for went out on the same tick.
    assert sent[-1][2] == pytest.approx(srv.YAW_MAX_RAD_S)
