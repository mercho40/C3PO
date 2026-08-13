"""Tests for the open-loop velocity skill (`bridge.skills.walk_velocity`).

All DDS calls are mocked via `bridge.sdk.g1_rpc.call_velocity` — these test
the skill's clamping/task-lifecycle logic, not the RPC dispatch itself
(covered by test_g1_request.py's coverage of the same underlying pattern).
"""

from __future__ import annotations

import asyncio

import pytest

from bridge.sdk import g1_rpc
from bridge.skills import walk_velocity


@pytest.mark.asyncio
async def test_velocity_within_limits_passed_through_unclamped(monkeypatch):
    seen = {}

    def fake_call_velocity(vx, vy, vyaw, duration):
        seen.update(vx=vx, vy=vy, vyaw=vyaw, duration=duration)
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_velocity", fake_call_velocity)

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.1)

    assert seen["vx"] == 0.1
    assert seen["duration"] == 0.1
    assert result["status"] == "completed"
    assert result["phase"] == "duration_elapsed"


@pytest.mark.asyncio
async def test_velocity_over_limit_gets_clamped(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        g1_rpc, "call_velocity", lambda vx, vy, vyaw, duration: (seen.update(vx=vx, vy=vy, vyaw=vyaw) or (0, ""))
    )

    result = await walk_velocity.run(vx=5.0, vy=-5.0, vyaw=10.0, duration_s=0.05)

    assert seen["vx"] == walk_velocity.MAX_LINEAR_VEL
    assert seen["vy"] == -walk_velocity.MAX_LINEAR_VEL
    assert seen["vyaw"] == walk_velocity.MAX_YAW_VEL
    assert result["result"]["clamped"]["vx"] == walk_velocity.MAX_LINEAR_VEL
    assert result["result"]["requested"]["vx"] == 5.0  # original request preserved for visibility


@pytest.mark.asyncio
async def test_duration_over_ceiling_gets_clamped(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        g1_rpc, "call_velocity", lambda vx, vy, vyaw, duration: (seen.update(duration=duration) or (0, ""))
    )

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=999.0)

    assert seen["duration"] == walk_velocity.MAX_DURATION_S
    assert result["result"]["clamped"]["duration_s"] == walk_velocity.MAX_DURATION_S


@pytest.mark.asyncio
async def test_rpc_error_marks_task_failed_and_does_not_wait(monkeypatch):
    monkeypatch.setattr(g1_rpc, "call_velocity", lambda vx, vy, vyaw, duration: (3104, None))

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=10.0)

    assert result["status"] == "failed"
    assert result["phase"] == "rpc_error"
    assert result["error"] == "rpc_error_code_3104"
    # Should fail fast on the RPC error, not sleep out the (would-be-clamped) duration.
    assert result["duration_s"] < 1.0


@pytest.mark.asyncio
async def test_cancel_stops_early_and_sends_zero_velocity(monkeypatch):
    calls = []
    monkeypatch.setattr(
        g1_rpc, "call_velocity", lambda vx, vy, vyaw, duration: (calls.append((vx, vy, vyaw, duration)) or (0, ""))
    )

    task_holder = {}
    orig_run = walk_velocity.run

    async def run_and_cancel():
        run_coro = orig_run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=2.0)
        task = asyncio.ensure_future(run_coro)
        # Let it start and register in the task registry, then cancel almost immediately.
        await asyncio.sleep(0.05)
        from bridge.skills.task_runtime import get_registry

        active = get_registry().list_active()
        assert len(active) == 1
        active[0].cancel_event.set()
        return await task

    result = await run_and_cancel()

    assert result["status"] == "cancelled"
    # First call is the real (clamped) command; second is the zero-velocity stop.
    assert len(calls) == 2
    assert calls[-1][:3] == (0.0, 0.0, 0.0)
