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

    def get_state(self) -> dict:
        i = min(self._i, len(self._poses) - 1)
        self._i += 1
        return {"pose": self._poses[i]}


class _NoPoseSampler:
    def get_state(self) -> dict:
        return {"pose": None}


def _pose(x: float, y: float, yaw: float = 0.0) -> dict:
    return {"x_meters_world": x, "y_meters_world": y, "yaw_radians_world": yaw}


@pytest.fixture(autouse=True)
def _stub_motion(monkeypatch):
    monkeypatch.setattr(walk_to, "send_velocity", lambda *a, **kw: None)

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
    sampler = _FakeSampler(
        [_pose(0.0, 0.0), _pose(0.0, 0.0), _pose(3.0, 0.0), _pose(4.5, 0.0)]
    )
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
