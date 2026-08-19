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

    task_future = asyncio.ensure_future(turn.run(delta_yaw_radians=math.radians(90), timeout_s=30.0))
    await asyncio.sleep(0.05)  # let it register and start looping

    active = get_registry().list_active()
    assert len(active) == 1
    assert get_registry().cancel(active[0].task_id) is True

    result = await asyncio.wait_for(task_future, timeout=2.0)

    assert result["status"] == "cancelled"
    assert result["phase"] == "cancelled"
