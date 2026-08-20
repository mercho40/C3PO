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
