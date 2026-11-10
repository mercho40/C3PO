"""The teleop lease: two processes, one velocity topic, no arbitration.

`rt/run_command/cmd` has none. Two processes writing velocity at 20-50 Hz give
a robot that obeys whichever message landed last, alternating tens of times a
second, with neither writer able to tell. Both writers exist and are reachable
at once — the MCP server runs walk_to / turn / walk_velocity, the teleop stream
runs the headset, and they share nothing but the robot.

The collision is ordinary. The operator walks the robot across a room while the
agent, asked in the chat panel to "go to the door", starts a walk_to. Both
requests are legitimate. Only one can be obeyed, and the person in the room
wearing the headset is the one who can see what the robot is about to hit.
"""

from __future__ import annotations

import math
import time

import pytest

from bridge import estop
from bridge.skills import turn, walk_to
from bridge.skills._locomotion import teleop_conflict


@pytest.fixture(autouse=True)
def _stub_motion(monkeypatch):
    async def fake_send(*a, **kw):
        return None

    async def fake_stop(height=0.78, duration_s=0.4):
        return None

    for mod in (walk_to, turn):
        monkeypatch.setattr(mod, "send_velocity_async", fake_send)
        monkeypatch.setattr(mod, "stop_motion", fake_stop)


class _Sampler:
    def get_state(self):
        return {"pose": {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0}}

    def latest_pose(self):
        return 0.0, 0.0, 0.0


def test_no_lease_is_no_conflict():
    assert teleop_conflict() is None


def test_a_renewed_lease_is_a_conflict():
    estop.renew_teleop_lease()
    assert teleop_conflict() is not None


def test_an_expired_lease_frees_locomotion():
    """A lease, not a lock.

    A lock's failure mode here is a robot nobody can drive: hold one, crash,
    and locomotion is refused until somebody finds the stale file. This has to
    expire on its own.
    """
    estop.renew_teleop_lease()
    path = estop.DEFAULT_RUN_DIR / estop.LEASE_NAME
    stale = time.time() - estop.LEASE_TTL_S - 1.0
    import os

    os.utime(path, (stale, stale))

    assert teleop_conflict() is None


def test_releasing_frees_it_immediately():
    # The operator who just disconnected should not wait out the TTL before
    # the console works again.
    estop.renew_teleop_lease()
    estop.release_teleop_lease()
    assert teleop_conflict() is None


@pytest.mark.asyncio
async def test_walk_to_refuses_while_a_headset_is_driving(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _Sampler())
    estop.renew_teleop_lease()

    result = await walk_to.run(target_x=2.0, target_y=0.0)

    assert result["status"] == "failed"
    assert result["phase"] == "teleop_active"
    assert "teleoperation" in result["error"]


@pytest.mark.asyncio
async def test_turn_refuses_while_a_headset_is_driving(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _Sampler())
    estop.renew_teleop_lease()

    result = await turn.run(delta_yaw_radians=math.radians(90))

    assert result["status"] == "failed"
    assert result["phase"] == "teleop_active"


@pytest.mark.asyncio
async def test_walk_to_runs_normally_with_no_headset(monkeypatch):
    """The guard must not be in the way the rest of the time."""
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _Sampler())

    result = await walk_to.run(target_x=0.1, target_y=0.0, stop_distance_m=1.0)

    assert result["status"] == "completed"
    assert result["phase"] != "teleop_active"


# --- C7: the lease is held only while the headset is actually driving --------
#
# An idle connected headset used to renew it every tick, so a walk the
# headset's own voice asked for was refused as "teleop is driving".


@pytest.fixture
def tsession(monkeypatch):
    import json

    from bridge.teleop import server as srv
    from bridge.teleop.hands import NullHandDriver
    from bridge.teleop.protocol import PROTOCOL_VERSION, parse_frame

    async def fake_send(vx, vy, vyaw, height):
        return None

    monkeypatch.setattr("bridge.skills._locomotion.send_velocity_async", fake_send)
    s = srv.TeleopSession()
    s.hands = NullHandDriver("test")
    seq = iter(range(1, 10_000))

    async def tick(**overrides):
        base = {
            "v": PROTOCOL_VERSION,
            "seq": next(seq),
            "t": 0.0,
            "enabled": False,
            "walk": 0.0,
            "arms": False,
            "head": {"yaw": 0.0, "pos": [0.0, 1.6, 0.0]},
            "hands": {},
        }
        base.update(overrides)
        s.ingest(parse_frame(json.dumps(base)))
        await srv._dispatch_once(s, s.last_frame_at)

    yield tick
    s.close()


async def test_an_idle_connected_headset_does_not_hold_the_lease(tsession):
    await tsession()
    assert estop.teleop_is_driving() is False


async def test_walking_holds_it_and_letting_go_frees_it_that_tick(tsession):
    await tsession(enabled=True, walk=1.0)
    assert estop.teleop_is_driving() is True

    await tsession()
    assert estop.teleop_is_driving() is False


async def test_head_yaw_alone_holds_it(tsession):
    await tsession(enabled=True, head={"yaw": 0.5, "pos": [0.0, 1.6, 0.0]})
    assert estop.teleop_is_driving() is True


async def test_engaged_arms_hold_it_even_at_zero_velocity(tsession, monkeypatch):
    from bridge.teleop import server as srv

    class _EngagedDriver:
        engaged = True

        def request_release(self):
            pass

        def clear_failure(self):
            pass

        def status(self):
            return {"engaged": True}

    monkeypatch.setattr(srv, "get_driver", lambda: _EngagedDriver())
    await tsession()
    assert estop.teleop_is_driving() is True


def _takeover_on_third_setpoint(monkeypatch, mod):
    """A fake send that has the headset take over on the 3rd setpoint."""
    sends: list[tuple] = []
    stops: list[float] = []

    async def fake_send(*a, **kw):
        sends.append(a)
        if len(sends) == 3:
            estop.renew_teleop_lease()

    async def fake_stop(height=0.78, duration_s=0.4):
        stops.append(height)

    monkeypatch.setattr(mod, "send_velocity_async", fake_send)
    monkeypatch.setattr(mod, "stop_motion", fake_stop)
    return sends, stops


@pytest.mark.asyncio
async def test_a_running_walk_yields_to_the_headset_and_still_stops(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _Sampler())
    sends, stops = _takeover_on_third_setpoint(monkeypatch, walk_to)

    result = await walk_to.run(target_x=2.0, target_y=0.0, timeout_s=5.0)

    assert result["status"] == "failed"
    assert result["phase"] == "teleop_takeover"
    assert "took over" in result["error"]
    assert len(sends) == 3  # no setpoint after the takeover...
    assert stops  # ...but the stop still went out (arm-mirror takeovers send none)


@pytest.mark.asyncio
async def test_a_running_turn_yields_to_the_headset_and_still_stops(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _Sampler())
    sends, stops = _takeover_on_third_setpoint(monkeypatch, turn)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=5.0)

    assert result["status"] == "failed"
    assert result["phase"] == "teleop_takeover"
    assert len(sends) == 3
    assert stops
