"""The e-stop must stop teleoperation. It is the one thing that has to work.

`stop_everything` does three things: set `cancel_event` on every task in the
TaskRegistry, send a zero-velocity burst, and (on real) Damp. A teleop session
is not a skill invocation, so until it registers itself it appears in none of
that — and the burst is useless against it, because the dispatch loop re-issues
velocity 50 ms later.

These tests are written from the operator's point of view: someone is wearing a
headset, someone else presses PARAR.
"""

from __future__ import annotations

import itertools
import json

import time

import pytest

from bridge.skills import stop_everything
from bridge.skills.task_runtime import get_registry
from bridge.teleop import server as srv
from bridge.teleop.protocol import PROTOCOL_VERSION, parse_frame
from bridge.teleop.server import TeleopSession


#: The session rejects out-of-order frames, so tests that interleave helper
#: traffic with their own must share one monotonically increasing sequence.
#: Getting this wrong is silent: the frame is counted as rejected and the
#: session simply keeps the previous one, so the test asserts against a state
#: it never actually reached.
_seq = itertools.count(1)


def next_seq() -> int:
    return next(_seq)


def frame(**overrides) -> str:
    base = {
        "v": PROTOCOL_VERSION,
        "seq": 1,
        "t": 0.0,
        "enabled": True,
        "walk": 1.0,
        "arms": False,
        "head": {"yaw": 0.0, "pos": [0.0, 1.6, 0.0]},
        "hands": {},
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture
def sent(monkeypatch):
    commands: list[tuple[float, float, float, float]] = []

    async def fake_send(vx, vy, vyaw, height):
        commands.append((vx, vy, vyaw, height))

    monkeypatch.setattr("bridge.skills._locomotion.send_velocity_async", fake_send)
    # `stop_everything` imports this into its own namespace, so patching it on
    # `_locomotion` is a no-op there -- it would reach real DDS.
    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: None)
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    return commands


def _make_session() -> TeleopSession:
    """A session with the hand driver stubbed out — the shape the fixture makes.

    Factored out because several tests need a SECOND session: reconnecting is
    the thing an operator does when the robot stops responding, so what a fresh
    session inherits is exactly what needs testing.
    """
    from bridge.teleop.hands import NullHandDriver

    s = TeleopSession()
    s.hands = NullHandDriver("test")
    return s


@pytest.fixture
def session(monkeypatch):
    s = _make_session()
    yield s
    s.close()


async def _release_for(session: TeleopSession, t0: float, duration_s: float) -> None:
    """Hold the dead-man released from `t0` for `duration_s`, at the real frame rate.

    Frames do not stop when the operator lets go — the sender is a fixed-rate
    loop that keeps transmitting `enabled: false` (see `stream.ts`). That
    matters here: the dwell is longer than STALE_FRAME_S, so a test that
    advanced the clock without delivering frames would be asserting against a
    dead link rather than a released dead-man, and could never clear.
    """
    step = 1.0 / 30.0
    elapsed = 0.0
    while elapsed <= duration_s:
        now = t0 + elapsed
        session.ingest(parse_frame(frame(seq=next_seq(), enabled=False, walk=0.0)))
        session.last_frame_at = now  # ingest stamps wall-clock; the test drives it
        await srv._dispatch_once(session, now)
        elapsed += step


async def test_a_live_session_is_visible_to_stop_everything(session, sent):
    # If it is not in the registry, the e-stop cannot see it and neither can
    # `list_active_tasks` — an operator asking "what is driving the robot?"
    # would be told "nothing" while someone drives it from a headset.
    active = [t for t in get_registry().list_active() if t.skill_name == "teleop_session"]
    assert len(active) == 1


async def test_pressing_stop_halts_the_stream(session, sent):
    session.ingest(parse_frame(frame()))
    await srv._dispatch_once(session, session.last_frame_at)
    assert sent[-1][0] != 0.0, "precondition: the session is commanding motion"

    await stop_everything.run()

    # The operator is still holding the control and the frames keep coming --
    # which is exactly the situation. The stream must refuse anyway.
    session.ingest(parse_frame(frame(seq=2)))
    await srv._dispatch_once(session, session.last_frame_at)

    assert sent[-1] == (0.0, 0.0, 0.0, srv.STAND_HEIGHT)
    assert session.stopped is True


async def test_the_stream_stays_stopped_while_the_control_is_held(session, sent):
    await stop_everything.run()

    for seq in range(2, 12):
        session.ingest(parse_frame(frame(seq=seq)))
        await srv._dispatch_once(session, session.last_frame_at)

    # Not one non-zero command after the stop, no matter how many frames arrive.
    assert all(c[0] == 0.0 and c[2] == 0.0 for c in sent)


async def test_recovery_needs_a_deliberate_release(session, sent):
    await stop_everything.run()
    session.ingest(parse_frame(frame(seq=2)))
    await srv._dispatch_once(session, session.last_frame_at)
    assert session.stopped is True

    # Releasing the dead-man is the gesture that clears it, but it has to be
    # HELD released. One frame is not enough: releasing is the most likely
    # thing to happen in the second after an emergency stop, so an instant
    # clear let a reflex undo the stop.
    t0 = session.last_frame_at
    await _release_for(session, t0, 0.0)
    assert session.stopped is True, "one released frame must not clear a stop"

    await _release_for(session, t0, srv.ESTOP_RELEASE_DWELL_S / 2)
    assert session.stopped is True, "half the dwell must not clear a stop"

    await _release_for(session, t0, srv.ESTOP_RELEASE_DWELL_S + 0.05)
    assert session.stopped is False

    session.ingest(parse_frame(frame(seq=next_seq())))
    await srv._dispatch_once(session, session.last_frame_at)
    assert sent[-1][0] == pytest.approx(srv.WALK_MAX_VEL)


async def test_re_gripping_during_the_dwell_restarts_it(session, sent):
    """The release has to be continuous, not cumulative.

    Otherwise the operator can tap their way out of a stop: release, grip,
    release, grip — never held for the dwell, but clearing anyway.
    """
    await stop_everything.run()
    session.ingest(parse_frame(frame(seq=2)))
    await srv._dispatch_once(session, session.last_frame_at)
    t0 = session.last_frame_at

    await _release_for(session, t0, srv.ESTOP_RELEASE_DWELL_S / 2)
    # Grips again half way through...
    session.last_frame_at = t0 + srv.ESTOP_RELEASE_DWELL_S / 2
    session.ingest(parse_frame(frame(seq=next_seq())))
    await srv._dispatch_once(session, t0 + srv.ESTOP_RELEASE_DWELL_S / 2)
    # ...then releases again. The clock restarts here.
    t1 = t0 + srv.ESTOP_RELEASE_DWELL_S / 2
    await _release_for(session, t1, srv.ESTOP_RELEASE_DWELL_S * 0.6)

    # Past the ORIGINAL dwell, but not past the restarted one.
    assert session.stopped is True


async def test_a_dead_link_can_never_clear_a_stop(session, sent):
    """The most dangerous of the three: a stop pressed after the link died.

    The session holds the last frame it received forever. If that frame has
    the dead-man false — which it does whenever the operator was not actively
    commanding — the old code cleared the latch against a frame from BEFORE
    the stop existed, on a link with nobody on the other end.
    """
    session.ingest(parse_frame(frame(seq=1, enabled=False, walk=0.0)))
    quiet_at = session.last_frame_at

    await stop_everything.run()
    # Time moves well past the staleness threshold; no new frames arrive.
    later = quiet_at + srv.STALE_FRAME_S + srv.ESTOP_RELEASE_DWELL_S + 5.0
    await srv._dispatch_once(session, later)
    assert session.stopped is True

    await srv._dispatch_once(session, later + 60.0)
    assert session.stopped is True, "a stop must not clear itself on a dead link"


async def test_reconnecting_does_not_clear_a_stop(session, sent):
    """Reconnecting is what an operator does reflexively when the robot stops
    responding — so it must not be a way to undo the stop that caused it.

    The old session recorded the existing stop as "already seen" at
    construction and started unlatched.
    """
    await stop_everything.run()
    fresh = _make_session()
    try:
        assert fresh.stopped is True
        assert fresh.check_estop(time.time()) is True
    finally:
        fresh.close()


async def test_a_cleared_stop_is_not_inherited_by_the_next_session(session, sent):
    """The other half: once an operator clears a stop, it is over.

    Without the acknowledgement record, every session after a stop would
    inherit it forever — safe, but it would read as a robot that is simply
    broken.
    """
    await stop_everything.run()
    session.ingest(parse_frame(frame(seq=2, enabled=False, walk=0.0)))
    t0 = session.last_frame_at
    await _release_for(session, t0, srv.ESTOP_RELEASE_DWELL_S + 0.05)
    assert session.stopped is False

    fresh = _make_session()
    try:
        assert fresh.stopped is False
    finally:
        fresh.close()


async def test_stop_releases_the_arms_too(session, sent, monkeypatch):
    released = {"called": False}

    class _FakeDriver:
        engaged = True

        def request_release(self):
            released["called"] = True

        def clear_failure(self):
            released["cleared"] = True

        async def release(self):
            released["called"] = True

        def status(self):
            return {"engaged": True, "weight": 1.0, "enabled_by_env": True, "sim_mode": "real"}

    monkeypatch.setattr(srv, "get_driver", lambda: _FakeDriver())
    session.arms_active = True

    await stop_everything.run()
    session.ingest(parse_frame(frame(seq=2, arms=True)))
    await srv._dispatch_once(session, session.last_frame_at)

    assert released["called"] is True


async def test_the_session_leaves_the_registry_when_it_ends(sent):
    s = TeleopSession()
    from bridge.teleop.hands import NullHandDriver

    s.hands = NullHandDriver("test")
    assert any(t.skill_name == "teleop_session" for t in get_registry().list_active())

    s.close()

    # A session that never leaves would make every later stop_everything report
    # a cancelled task that no longer exists, and would keep the watchdog
    # believing motion is in flight.
    assert not any(t.skill_name == "teleop_session" for t in get_registry().list_active())


async def test_stop_everything_reports_the_session_it_cancelled(session, sent):
    result = await stop_everything.run()
    cancelled = result.get("cancelled_task_ids") or result.get("cancelled") or []
    assert len(cancelled) >= 1
