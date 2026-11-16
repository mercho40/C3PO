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


# --- frozen odometry (get_state keeps returning the last pose after it stops) ---


@pytest.mark.asyncio
async def test_frozen_odometry_refuses_before_turning(monkeypatch):
    sent: list[tuple] = []

    async def recording_send(*a, **kw):
        sent.append(a)

    monkeypatch.setattr(turn, "send_velocity_async", recording_send)
    sampler = _FakeSampler([_pose(0.0)])
    sampler.pose_age = 5.0
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=2.0)

    assert (result["status"], result["phase"]) == ("failed", "pose_stale")
    assert sent == []


@pytest.mark.asyncio
async def test_odometry_freezing_mid_turn_stops_the_turn(monkeypatch):
    sampler = _FakeSampler([_pose(0.0)])
    sent: list[tuple] = []
    stops: list[float] = []

    async def send(*a, **kw):
        sent.append(a)
        if len(sent) == 3:
            sampler.pose_age = 5.0

    async def counting_stop(height=0.78, duration_s=0.4):
        stops.append(height)

    monkeypatch.setattr(turn, "send_velocity_async", send)
    monkeypatch.setattr(turn, "stop_motion", counting_stop)
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=2.0)

    assert (result["status"], result["phase"]) == ("failed", "pose_stale")
    assert len(sent) == 3  # no setpoint steered on the frozen pose...
    assert stops  # ...and the stop still went out


@pytest.mark.asyncio
async def test_a_turn_is_refused_while_the_arms_are_held(monkeypatch):
    from types import SimpleNamespace

    from bridge.teleop import arm_sdk

    monkeypatch.setattr(arm_sdk, "get_driver", lambda: SimpleNamespace(engaged=True))
    sampler = _FakeSampler([_pose(0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=0.05)

    assert (result["status"], result["phase"]) == ("failed", "arm_held")
    assert sampler._i == 0


@pytest.mark.asyncio
async def test_a_turn_is_refused_while_another_walk_runs(monkeypatch):
    sampler = _FakeSampler([_pose(0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)
    walking = get_registry().create("walk_to")
    try:
        result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=0.05)
    finally:
        walking.status = "completed"  # no skill is looping on it to end it

    assert (result["status"], result["phase"]) == ("failed", "walk_task_active")
    assert walking.task_id in result["error"]
    assert sampler._i == 0


@pytest.mark.asyncio
async def test_look_around_may_turn_inside_its_own_sweep(monkeypatch):
    sampler = _FakeSampler([_pose(0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)
    sweep = get_registry().create("look_around")
    try:
        result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=0.05)
    finally:
        sweep.status = "completed"
    assert result["phase"] == "timeout"  # it ran


@pytest.mark.asyncio
async def test_a_turn_is_refused_in_a_fresh_non_walk_fsm(monkeypatch):
    """In FSM 802 SetVelocity answers 0 and nothing turns; the turn used to time out as completed."""
    from types import SimpleNamespace

    fsm = SimpleNamespace(get_arm_state=lambda: {"fsm_id": 802, "fsm_age_s": 0.5})
    monkeypatch.setattr("bridge.sdk.state.existing_sampler", lambda: fsm)
    sampler = _FakeSampler([_pose(0.0)])
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=0.05)

    assert (result["status"], result["phase"]) == ("failed", "fsm_not_walkable")
    assert sampler._i == 0


@pytest.mark.asyncio
async def test_a_wall_clock_step_does_not_end_the_turn(monkeypatch):
    """The robot has no RTC, so NTP steps its clock; the turn's deadline must not move with it."""
    import time

    real_time, step = time.time, [0.0]
    monkeypatch.setattr(time, "time", lambda: real_time() + step[0])

    async def send(*a, **kw):
        step[0] = 3600.0  # the first setpoint is sent inside the loop, after the deadline

    monkeypatch.setattr(turn, "send_velocity_async", send)
    sampler = _FakeSampler(
        [_pose(0.0), _pose(0.0), _pose(math.radians(45)), _pose(math.radians(89))]
    )
    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: sampler)

    result = await turn.run(delta_yaw_radians=math.radians(90), timeout_s=5.0)

    assert result["phase"] == "reached"
