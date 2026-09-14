"""Tests for turn -- the continuous velocity-loop skill that rotates the G1
in place by a yaw delta. Same task-lifecycle shape as walk_to; had zero
coverage before this. See test_walk_to.py for the fake-sampler pattern this
mirrors.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from bridge.skills import turn
from bridge.skills.task_runtime import get_registry


class _FakeSampler:
    def __init__(self, poses: list[dict]) -> None:
        self._poses = poses
        self._i = 0

    def get_state(self) -> dict:
        i = min(self._i, len(self._poses) - 1)
        self._i += 1
        return {"pose": self._poses[i]}


class _NoPoseSampler:
    def get_state(self) -> dict:
        return {"pose": None}


def _pose(yaw: float) -> dict:
    return {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": yaw}


@pytest.fixture(autouse=True)
def _stub_motion(monkeypatch):
    async def fake_send_velocity_async(*a, **kw):
        return None

    monkeypatch.setattr(turn, "send_velocity_async", fake_send_velocity_async)

    async def fake_stop_motion(height=0.78, duration_s=0.4):
        return None

    monkeypatch.setattr(turn, "stop_motion", fake_stop_motion)


@pytest.mark.asyncio
async def test_turn_no_pose_fails_fast(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _NoPoseSampler())

    result = await turn.run(delta_yaw_radians=math.radians(90))

    assert result["status"] == "failed"
    assert result["phase"] == "no_pose"


@pytest.mark.asyncio
async def test_turn_reaches_target_as_yaw_approaches(monkeypatch):
    # Start at 0 rad, target +90deg; sequence closes the gap to within the
    # default ~3deg tolerance over a few loop iterations.
    sampler = _FakeSampler(
        [_pose(0.0), _pose(0.0), _pose(math.radians(45)), _pose(math.radians(89))]
    )
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=5.0)

    assert result["status"] == "completed"
    assert result["phase"] == "reached"
    assert result["result"]["reached"] is True
    assert abs(result["result"]["final_yaw_error_radians"]) <= math.radians(3)


@pytest.mark.asyncio
async def test_turn_timeout_when_target_never_reached(monkeypatch):
    sampler = _FakeSampler([_pose(0.0)] * 500)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=0.05)

    assert result["status"] == "completed"
    assert result["phase"] == "timeout"
    assert result["result"]["reached"] is False


@pytest.mark.asyncio
async def test_turn_cancellation_stops_promptly(monkeypatch):
    sampler = _FakeSampler([_pose(0.0)] * 1000)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    task_future = asyncio.ensure_future(
        turn.run(delta_yaw_radians=math.radians(90), timeout_s=30.0)
    )
    await asyncio.sleep(0.05)  # let it register and start looping

    active = get_registry().list_active()
    assert len(active) == 1
    assert get_registry().cancel(active[0].task_id) is True

    result = await asyncio.wait_for(task_future, timeout=2.0)

    assert result["status"] == "cancelled"
    assert result["phase"] == "cancelled"


# --- the stopping condition -------------------------------------------------
#
# MEASURED ON THE ROBOT 2026-08-26. `turn` reported reached=true and the body
# settled 3.41 deg from target on a 3 deg tolerance. `reached` was decided from
# a SINGLE pose sample, and leg odometry is noisy — one reading dipping inside
# the band ended the loop while the robot was really outside it.
#
# This is the property works_real asserts for `turn`, so it is the property
# most worth pinning.


@pytest.mark.asyncio
async def test_one_noisy_sample_inside_the_band_does_not_stop_the_turn(monkeypatch):
    """A spike is not an arrival.

    Two samples far away, ONE reading that lands exactly on target, then far
    away forever. Before the confirm window this returned reached=true on the
    spike and stopped the robot 90 degrees short.
    """
    poses = [_pose(0.0), _pose(0.0), _pose(math.radians(90))] + [_pose(0.0)] * 400
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _FakeSampler(poses))

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=0.6)

    assert result["result"]["reached"] is False, (
        "a single in-tolerance sample was treated as arrival"
    )
    assert result["phase"] == "timeout"


@pytest.mark.asyncio
async def test_the_band_must_hold_for_the_confirm_window(monkeypatch):
    """Sustained agreement does stop it — the fix must not prevent stopping."""
    poses = [_pose(0.0), _pose(0.0)] + [_pose(math.radians(90))] * 6
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _FakeSampler(poses))

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=5.0)

    assert result["result"]["reached"] is True
    assert result["phase"] == "reached"


@pytest.mark.asyncio
async def test_leaving_the_band_resets_the_confirmation(monkeypatch):
    """Two inside, one outside, then sustained inside.

    The count must restart rather than accumulate across an excursion — two
    separate near-misses are not the same evidence as holding still.
    """
    poses = (
        [_pose(0.0)]
        + [_pose(math.radians(90))] * 2      # two inside
        + [_pose(0.0)]                        # excursion: resets
        + [_pose(math.radians(90))] * 6       # sustained inside
    )
    sampler = _FakeSampler(poses)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=5.0)

    assert result["result"]["reached"] is True
    # It cannot have stopped before the excursion; that would mean two samples
    # were enough and the reset never happened.
    assert sampler._i > 4


@pytest.mark.asyncio
async def test_the_confirm_window_commands_zero_yaw(monkeypatch):
    """While confirming, it must actively command a stop.

    Falling silent would be wrong in a way that is invisible here and obvious
    on hardware: the firmware holds the last setpoint for up to a second, so a
    silent confirm window lets the body coast through the band and out the
    other side while the loop congratulates itself.
    """
    sent: list[float] = []

    async def recording_send(vx, vy, vyaw, height=0.78):
        sent.append(vyaw)

    monkeypatch.setattr(turn, "send_velocity_async", recording_send)
    poses = [_pose(0.0)] + [_pose(math.radians(90))] * 6
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _FakeSampler(poses))

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=5.0)

    assert result["result"]["reached"] is True
    assert sent, "nothing was commanded at all"
    # The steering commands come first, then the confirm window's zeros.
    assert sent[-1] == 0.0, f"confirm window did not command a stop: {sent[-3:]}"
