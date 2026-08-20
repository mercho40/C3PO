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

import json

import pytest

from bridge.skills import stop_everything
from bridge.skills.task_runtime import get_registry
from bridge.teleop import server as srv
from bridge.teleop.protocol import PROTOCOL_VERSION, parse_frame
from bridge.teleop.server import TeleopSession


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


@pytest.fixture
def session(monkeypatch):
    from bridge.teleop.hands import NullHandDriver

    s = TeleopSession()
    s.hands = NullHandDriver("test")
    yield s
    s.close()


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

    # Releasing the dead-man is the deliberate act that clears it. Anything
    # less would let an e-stop be undone by simply continuing to hold.
    session.ingest(parse_frame(frame(seq=3, enabled=False, walk=0.0)))
    await srv._dispatch_once(session, session.last_frame_at)
    assert session.stopped is False

    session.ingest(parse_frame(frame(seq=4)))
    await srv._dispatch_once(session, session.last_frame_at)
    assert sent[-1][0] == pytest.approx(srv.WALK_MAX_VEL)


async def test_stop_releases_the_arms_too(session, sent, monkeypatch):
    released = {"called": False}

    class _FakeDriver:
        engaged = True

        def request_release(self):
            released["called"] = True

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
