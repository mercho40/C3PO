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
    for i in range(srv.CALIBRATION_SAMPLES):
        session.ingest(parse_frame(frame(seq=2 + i, hands={"right": extended})))
    assert session.calibrated is True
    assert session.arm_length_m == pytest.approx(0.70, abs=0.02)


def test_one_wild_frame_cannot_set_the_operators_reach(session):
    """The failure this guard exists for.

    Quest hand tracking emits wild positions for a frame or two as a hand
    enters or leaves the tracking volume. The protocol rejects those beyond
    1.5 m — which means anything up to 1.5 m gets through and used to be
    measured as if it were an arm, latched, and held for the whole session.

    `_elbow_angle` is `2 acos(reach / arm_length)`, so an inflated denominator
    makes every real hand position read as barely extended and the elbow stays
    almost straight. No error, no log line, just an arm that will not bend.
    """
    # Inside the protocol's 1.5 m sanity limit — which is the point. Anything
    # it does NOT reject used to be trusted outright.
    artefact = {"tracked": True, "pos": [0.18, 0.35, 0.0], "quat": [0, 0, 0, 1]}
    session.ingest(parse_frame(frame(hands={"right": artefact})))
    assert session.calibrated is False

    real = {"tracked": True, "pos": [0.18, 0.75, 0.0], "quat": [0, 0, 0, 1]}
    for i in range(srv.CALIBRATION_SAMPLES + 2):
        session.ingest(parse_frame(frame(seq=10 + i, hands={"right": real})))

    assert session.calibrated is True
    assert session.arm_length_m == pytest.approx(0.63, abs=0.02), (
        "one artefact among real samples must not survive into the reach"
    )


def test_samples_that_never_agree_never_calibrate(session):
    """A hand jittering wildly is not a measurement, however long it goes on.

    Falling back to the default reach is right here: it is somebody's arm
    length, which a number derived from noise is not.
    """
    for i in range(40):
        # Alternating between two reaches that BOTH clear the minimum
        # (~0.53 and ~0.78 from the estimated shoulder, against a 0.53
        # threshold) but disagree by far more than an arm changes length.
        y = 0.85 if i % 2 else 0.60
        hand = {"tracked": True, "pos": [0.18, y, 0.0], "quat": [0, 0, 0, 1]}
        session.ingest(parse_frame(frame(seq=100 + i, hands={"right": hand})))

    assert session.calibrated is False
    assert session.arm_length_m == pytest.approx(srv.DEFAULT_ARM_LENGTH_M)


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

        def clear_failure(self):
            released["cleared"] = True

        async def release(self):
            released["awaited"] = True
            await asyncio.sleep(2.0)

        def status(self):
            return {"engaged": True, "weight": 1.0, "enabled_by_env": True, "sim_mode": "real"}

    monkeypatch.setattr(srv, "get_driver", lambda: _FakeDriver())
    session.arms_active = True
    session.ingest(
        parse_frame(frame(arms=False, head={"yaw": srv.YAW_FULL_SCALE_RAD, "pos": [0, 1.6, 0]}))
    )

    await asyncio.wait_for(srv._dispatch_once(session, session.last_frame_at), timeout=0.5)

    assert released["requested"] is True
    assert released["awaited"] is False
    # And the turn the operator is still asking for went out on the same tick.
    assert sent[-1][2] == pytest.approx(srv.YAW_MAX_RAD_S)


# -- loop resilience --------------------------------------------------------


async def test_a_failing_dispatch_ends_the_session_instead_of_dying_quietly(
    session, sent, monkeypatch
):
    """A control loop that cannot control must hand back, loudly.

    Only `CancelledError` used to be caught, so anything else — a DDS publisher
    that fails, a hand driver that raises, an unexpected error from `engage()`
    — killed the loop while the WebSocket kept ingesting frames. The session
    looked alive from the page, status stopped updating, and nothing dispatched
    or stopped until the operator happened to disconnect.
    """
    closed: list[tuple[int, str]] = []

    class _FakeWs:
        async def send(self, _message):
            return None

        async def close(self, code=1000, reason=""):
            closed.append((code, reason))

    calls = {"n": 0}

    async def exploding_dispatch(_session, _now):
        calls["n"] += 1
        raise RuntimeError("DDS publisher went away")

    monkeypatch.setattr(srv, "_dispatch_once", exploding_dispatch)

    await asyncio.wait_for(srv._dispatch_loop(session, _FakeWs()), timeout=2.0)

    # It gave up rather than spinning on the failure at the dispatch rate.
    assert calls["n"] == 1
    # And it told the client, so the page can say the link is gone.
    assert closed and closed[0][0] == 1011


async def test_a_failing_dispatch_still_stops_the_robot(session, sent, monkeypatch):
    class _FakeWs:
        async def send(self, _message):
            return None

        async def close(self, code=1000, reason=""):
            return None

    session.ingest(parse_frame(frame(walk=1.0)))
    await srv._dispatch_once(session, session.last_frame_at)
    assert session.moving is True

    async def exploding_dispatch(_session, _now):
        raise RuntimeError("boom")

    monkeypatch.setattr(srv, "_dispatch_once", exploding_dispatch)
    await asyncio.wait_for(srv._dispatch_loop(session, _FakeWs()), timeout=2.0)

    assert sent[-1] == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)
