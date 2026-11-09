"""Tests for walk_to -- the continuous velocity-loop skill that drives the
G1 toward a world-frame XY target. Had zero coverage despite being
higher-risk motion code than walk_velocity (open-loop, single command):
this one loops, integrates live pose feedback, and must cancel promptly.

No real DDS or physics: `sampler.get_state()` is faked with a scripted
sequence of poses simulating approach to the target, and `send_velocity`/
`stop_motion` (imported directly into walk_to's own namespace, so patched
there rather than on `_locomotion`) are stubbed to avoid touching DDS.
"""

from __future__ import annotations

import asyncio

import pytest

from bridge.skills import walk_to
from bridge.skills.task_runtime import get_registry


class _FakeSampler:
    """Returns poses from a scripted sequence, repeating the last one."""

    def __init__(self, poses: list[dict]) -> None:
        self._poses = poses
        self._i = 0
        self.pose_age = 0.0  # what latest_pose() reports: odometry is live unless a test says not

    def get_state(self) -> dict:
        i = min(self._i, len(self._poses) - 1)
        self._i += 1
        return {"pose": self._poses[i]}

    def latest_pose(self) -> tuple[float, float, float]:
        return 0.0, 0.0, self.pose_age


class _NoPoseSampler:
    def get_state(self) -> dict:
        return {"pose": None}

    def latest_pose(self) -> None:
        return None


def _pose(x: float, y: float, yaw: float = 0.0) -> dict:
    return {"x_meters_world": x, "y_meters_world": y, "yaw_radians_world": yaw}


@pytest.fixture(autouse=True)
def _stub_motion(monkeypatch):
    async def fake_send_velocity_async(*a, **kw):
        return None

    monkeypatch.setattr(walk_to, "send_velocity_async", fake_send_velocity_async)

    async def fake_stop_motion(height=0.78, duration_s=0.4):
        return None

    monkeypatch.setattr(walk_to, "stop_motion", fake_stop_motion)


@pytest.mark.asyncio
async def test_walk_to_no_pose_fails_fast(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _NoPoseSampler())

    result = await walk_to.run(target_x=1.0, target_y=1.0)

    assert result["status"] == "failed"
    assert result["phase"] == "no_pose"


@pytest.mark.asyncio
async def test_walk_to_already_within_stop_distance_completes_immediately(monkeypatch):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _FakeSampler([_pose(0.0, 0.0)]))

    result = await walk_to.run(target_x=0.3, target_y=0.0, stop_distance_m=1.0)

    assert result["status"] == "completed"
    assert result["phase"] == "already_within_stop_distance"
    assert result["result"]["arrived"] is True
    assert result["result"]["displacement_m"] == 0.0


@pytest.mark.asyncio
async def test_walk_to_arrives_as_pose_approaches_target(monkeypatch):
    # target (5, 0), stop_distance 1.0 -- sequence walks the reported
    # distance down from 5.0 to 0.5 (within stop distance) over a few loop
    # iterations.
    sampler = _FakeSampler([_pose(0.0, 0.0), _pose(0.0, 0.0), _pose(3.0, 0.0), _pose(4.5, 0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await walk_to.run(target_x=5.0, target_y=0.0, stop_distance_m=1.0, timeout_s=5.0)

    assert result["status"] == "completed"
    assert result["phase"] == "arrived"
    assert result["result"]["arrived"] is True
    assert result["result"]["final_distance_m"] <= 1.0


@pytest.mark.asyncio
async def test_walk_to_timeout_when_target_never_reached(monkeypatch):
    sampler = _FakeSampler([_pose(0.0, 0.0)] * 500)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await walk_to.run(target_x=10.0, target_y=0.0, stop_distance_m=0.1, timeout_s=0.05)

    assert result["status"] == "completed"
    assert result["phase"] == "timeout"
    assert result["result"]["arrived"] is False


@pytest.mark.asyncio
async def test_walk_to_cancellation_stops_promptly(monkeypatch):
    sampler = _FakeSampler([_pose(0.0, 0.0)] * 1000)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    task_future = asyncio.ensure_future(
        walk_to.run(target_x=10.0, target_y=0.0, stop_distance_m=0.1, timeout_s=30.0)
    )
    await asyncio.sleep(0.05)  # let it register and start looping

    active = get_registry().list_active()
    assert len(active) == 1
    assert get_registry().cancel(active[0].task_id) is True

    result = await asyncio.wait_for(task_future, timeout=2.0)

    assert result["status"] == "cancelled"
    assert result["phase"] == "cancelled"


# --- retarget: the hook go_to_object re-aims through ------------------------


@pytest.fixture
def fast_retarget(monkeypatch):
    monkeypatch.setattr(walk_to, "LOOP_PERIOD_S", 0.005)
    monkeypatch.setattr(walk_to, "RETARGET_PERIOD_S", 0.05)
    stops: list[float] = []

    async def counting_stop(height=0.78, duration_s=0.4):
        stops.append(height)

    monkeypatch.setattr(walk_to, "stop_motion", counting_stop)
    return stops


@pytest.mark.asyncio
async def test_retarget_is_called_once_per_period_not_every_loop(monkeypatch, fast_retarget):
    sampler = _FakeSampler([_pose(0.0, 0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)
    calls: list[int] = []

    def retarget():
        calls.append(sampler._i)
        return (10.0, 0.0)

    result = await walk_to.run(
        target_x=10.0, target_y=0.0, stop_distance_m=0.1, timeout_s=0.3, retarget=retarget
    )

    assert result["phase"] == "timeout"
    assert 3 <= len(calls) <= 7  # ~0.3 s / 0.05 s
    assert sampler._i > 3 * len(calls)  # the loop ran many more times than it re-aimed


@pytest.mark.asyncio
async def test_a_moved_target_is_followed(monkeypatch, fast_retarget):
    # Standing at the origin, 5 m short of the first target; the object turns out to be 0.5 m away.
    sampler = _FakeSampler([_pose(0.0, 0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await walk_to.run(
        target_x=5.0, target_y=0.0, stop_distance_m=1.0, timeout_s=2.0, retarget=lambda: (0.0, 0.5)
    )

    assert result["phase"] == "arrived"
    assert result["result"]["final_distance_m"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_a_lost_target_ends_the_walk_through_stop_motion(monkeypatch, fast_retarget):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _FakeSampler([_pose(0.0, 0.0)]))

    result = await walk_to.run(
        target_x=5.0, target_y=0.0, stop_distance_m=1.0, timeout_s=2.0, retarget=lambda: None
    )

    assert (result["status"], result["phase"]) == ("failed", "target_lost")
    assert len(fast_retarget) == 1


@pytest.mark.asyncio
async def test_a_raising_retarget_still_stops(monkeypatch, fast_retarget):
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _FakeSampler([_pose(0.0, 0.0)]))

    def broken():
        raise RuntimeError("perception exploded")

    result = await walk_to.run(
        target_x=5.0, target_y=0.0, stop_distance_m=1.0, timeout_s=2.0, retarget=broken
    )

    assert (result["status"], result["phase"]) == ("failed", "exception")
    assert len(fast_retarget) == 1


@pytest.mark.asyncio
async def test_a_second_walk_is_refused_while_one_runs(monkeypatch):
    # The no_active_walk_task precondition: two loops writing velocity would fight.
    sampler = _FakeSampler([_pose(0.0, 0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)
    walking = get_registry().create("walk_to")
    try:
        result = await walk_to.run(target_x=5.0, target_y=0.0, timeout_s=0.05)
    finally:
        walking.status = "completed"  # no skill is looping on it to end it

    assert result["status"] == "failed"
    assert result["phase"] == "walk_task_active"
    assert walking.task_id in result["error"]
    assert sampler._i == 0  # refused before reading a pose, let alone moving


# --- frozen odometry (get_state keeps returning the last pose after it stops) ---


@pytest.mark.asyncio
async def test_frozen_odometry_refuses_before_moving(monkeypatch):
    sent: list[tuple] = []

    async def recording_send(*a, **kw):
        sent.append(a)

    monkeypatch.setattr(walk_to, "send_velocity_async", recording_send)
    sampler = _FakeSampler([_pose(0.0, 0.0)])
    sampler.pose_age = 5.0
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await walk_to.run(target_x=5.0, target_y=0.0, timeout_s=2.0)

    assert (result["status"], result["phase"]) == ("failed", "pose_stale")
    assert sent == []


@pytest.mark.asyncio
async def test_odometry_freezing_mid_walk_stops_the_walk(monkeypatch, fast_retarget):
    sampler = _FakeSampler([_pose(0.0, 0.0)])
    sent: list[tuple] = []

    async def send(*a, **kw):
        sent.append(a)
        if len(sent) == 3:
            sampler.pose_age = 5.0

    monkeypatch.setattr(walk_to, "send_velocity_async", send)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await walk_to.run(target_x=5.0, target_y=0.0, timeout_s=2.0)

    assert (result["status"], result["phase"]) == ("failed", "pose_stale")
    assert len(sent) == 3  # no setpoint steered on the frozen pose...
    assert len(fast_retarget) == 1  # ...and the stop still went out
