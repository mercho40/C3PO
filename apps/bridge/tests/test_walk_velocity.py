"""Tests for the open-loop velocity skill (`bridge.skills.walk_velocity`).

All DDS calls are mocked via `bridge.sdk.g1_rpc.call_set_velocity`, reached
through `_locomotion.send_velocity` (so the safety governor is in the path, as
on the robot) — these test the skill's clamping/task-lifecycle logic, not the
RPC dispatch itself.
"""

from __future__ import annotations

import asyncio

import pytest

from bridge.sdk import g1_rpc
from bridge.skills import _locomotion, walk_velocity


@pytest.fixture(autouse=True)
def real_dispatch(monkeypatch):
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")


def _record(monkeypatch, code=0):
    calls: list[tuple[float, float, float, float]] = []
    monkeypatch.setattr(
        g1_rpc,
        "call_set_velocity",
        lambda vx, vy, vyaw, duration: calls.append((vx, vy, vyaw, duration)) or (code, ""),
    )
    return calls


@pytest.mark.asyncio
async def test_velocity_within_limits_passed_through_unclamped(monkeypatch):
    calls = _record(monkeypatch)

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.1)

    assert calls[0][0] == 0.1
    assert result["status"] == "completed"
    assert result["phase"] == "duration_elapsed"


@pytest.mark.asyncio
async def test_velocity_over_limit_gets_clamped(monkeypatch):
    calls = _record(monkeypatch)

    result = await walk_velocity.run(vx=5.0, vy=-5.0, vyaw=10.0, duration_s=0.05)

    # The legs get the diagonal capped as a vector by safety.govern: cap/sqrt(2) per axis.
    edge = walk_velocity.MAX_LINEAR_VEL / 2**0.5
    assert calls[0][:3] == pytest.approx((edge, -edge, walk_velocity.MAX_YAW_VEL))
    # And the result reports that vector, not the per-axis clamp (0.3, -0.3) it never got.
    clamped = result["result"]["clamped"]
    assert (clamped["vx"], clamped["vy"], clamped["vyaw"]) == pytest.approx(calls[0][:3])
    assert result["result"]["requested"]["vx"] == 5.0  # original request preserved for visibility


@pytest.mark.asyncio
async def test_duration_over_ceiling_gets_clamped(monkeypatch):
    _record(monkeypatch)
    monkeypatch.setattr(walk_velocity, "MAX_DURATION_S", 0.1)  # keep the test fast

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=999.0)

    assert result["result"]["clamped"]["duration_s"] == 0.1
    assert result["duration_s"] < 0.5


@pytest.mark.asyncio
async def test_no_setpoint_outlives_the_firmware_deadman(monkeypatch):
    """A 3 s walk used to be ONE setpoint with duration=3: a bridge that died
    mid-walk left the robot walking for three seconds. Each setpoint now
    carries the 1 s deadman, re-issued, and the walk ends on a zero."""
    calls = _record(monkeypatch)

    await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.3)

    assert len(calls) > 2
    assert all(d <= _locomotion.VELOCITY_DURATION_S <= 1.0 for *_, d in calls)
    assert calls[-1][:3] == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_rpc_error_marks_task_failed_and_does_not_wait(monkeypatch):
    _record(monkeypatch, code=3104)

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=10.0)

    assert result["status"] == "failed"
    assert result["phase"] == "rpc_error"
    assert result["error"] == "rpc_error_code_3104"
    # Should fail fast on the RPC error, not sleep out the (would-be-clamped) duration.
    assert result["duration_s"] < 1.0


@pytest.mark.asyncio
async def test_cancel_stops_early_and_sends_zero_velocity(monkeypatch):
    calls = _record(monkeypatch)

    task = asyncio.ensure_future(walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=2.0))
    # Let it start and register in the task registry, then cancel almost immediately.
    await asyncio.sleep(0.05)
    from bridge.skills.task_runtime import get_registry

    active = get_registry().list_active()
    assert len(active) == 1
    active[0].cancel_event.set()
    result = await task

    assert result["status"] == "cancelled"
    assert result["duration_s"] < 1.0
    assert calls[0][0] == 0.1
    assert calls[-1][:3] == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_refused_while_the_arms_are_held(monkeypatch):
    from types import SimpleNamespace

    from bridge.teleop import arm_sdk

    calls = _record(monkeypatch)
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: SimpleNamespace(engaged=True))

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.2)

    assert (result["status"], result["phase"]) == ("failed", "arm_held")
    assert calls == []


# --- decision 11.6b: deliberate operator input preempts an agent walk -------


def _agent_walk(skill: str, events: list[str]):
    """A registry task like walk_to's, whose loop ends (after its stop burst) when cancelled."""
    from bridge.skills.task_runtime import get_registry

    task = get_registry().create(skill)

    async def loop():
        await task.cancel_event.wait()
        await asyncio.sleep(0.05)  # the walk's own stop_motion
        task.status = "cancelled"
        events.append(f"{skill}_ended")

    return task, asyncio.ensure_future(loop())


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", ["walk_to", "turn", "navigate_to", "look_around"])
async def test_operator_velocity_cancels_an_agent_walk_and_waits_for_it(monkeypatch, skill):
    events: list[str] = []
    monkeypatch.setattr(
        g1_rpc, "call_set_velocity", lambda *a: events.append("setpoint") or (0, "")
    )
    task, loop = _agent_walk(skill, events)
    try:
        result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.1)
        await asyncio.wait_for(loop, 1.0)
    finally:
        if task.status == "running":
            task.status = "failed"  # never cancelled: do not leak a running walk into later tests

    assert task.status == "cancelled"
    assert result["status"] == "completed"
    assert events[0] == f"{skill}_ended"  # no setpoint of ours while the walk still steered


@pytest.mark.asyncio
async def test_operator_velocity_never_cancels_another_walk_velocity(monkeypatch):
    """The headset renews its walk by overlapping 1 s calls; cancelling would stutter it."""
    from bridge.skills.task_runtime import get_registry

    _record(monkeypatch)
    renewing = get_registry().create("walk_velocity")
    try:
        result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.1)
    finally:
        renewing.status = "completed"  # no loop runs it here
    assert result["status"] == "completed"
    assert not renewing.cancel_event.is_set()


@pytest.mark.asyncio
async def test_a_running_walk_velocity_yields_to_the_headset_and_still_stops(monkeypatch):
    from bridge import estop

    calls: list[tuple] = []

    def record(vx, vy, vyaw, d):
        calls.append((vx, vy, vyaw))
        if len(calls) == 3:
            estop.renew_teleop_lease()
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_set_velocity", record)

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=2.0)

    assert (result["status"], result["phase"]) == ("failed", "teleop_takeover")
    assert len(calls) == 4 and calls[-1] == (0.0, 0.0, 0.0)  # three setpoints, then only zero
