"""Tests for stop_everything — had zero coverage despite being the
safety-critical e-stop. Two things matter most: (1) it actually interrupts
an in-flight walk_velocity task via the shared task registry, not just in
theory, and (2) the real-hardware Damp fallback only fires in real mode.
"""

from __future__ import annotations

import asyncio

import pytest

from bridge.sdk import g1_rpc
from bridge.skills import stop_everything, walk_velocity
from bridge.skills.task_runtime import get_registry


@pytest.fixture(autouse=True)
def _no_real_dds(monkeypatch):
    # stop_motion_sync publishes to rt/run_command/cmd via a real DDS
    # ChannelPublisher — fine on the actual robot/sim, but there's no DDS
    # participant in a test process. Every test in this file calls
    # stop_everything.run(), so stub this out once for all of them.
    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: None)


@pytest.mark.asyncio
async def test_stop_everything_cancels_in_flight_walk_velocity(monkeypatch):
    velocity_calls = []
    monkeypatch.setattr(
        g1_rpc,
        "call_velocity",
        lambda vx, vy, vyaw, duration: (velocity_calls.append((vx, vy, vyaw, duration)) or (0, "")),
    )
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")

    # Start a long-ish walk_velocity task in the background, same as a real
    # in-flight command would be.
    task_future = asyncio.ensure_future(walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=2.0))
    await asyncio.sleep(0.05)  # let it register in the task registry and issue its first call

    assert len(get_registry().list_active()) == 1

    result = stop_everything.run()

    assert result["cancelled_count"] == 1
    walk_result = await task_future
    assert walk_result["status"] == "cancelled"
    # First call was the real command; stop_everything's cancellation should
    # have made walk_velocity's own loop notice and send a zero-velocity stop
    # well before the original 2s duration elapsed.
    assert len(velocity_calls) == 2
    assert velocity_calls[-1][:3] == (0.0, 0.0, 0.0)


def test_stop_everything_dispatches_damp_in_real_mode(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: (damp_calls.append(mode) or (0, "")))

    result = stop_everything.run()

    assert len(damp_calls) == 1
    assert result["real_damp_fallback_rpc_code"] == 0
    assert result["real_damp_fallback_attempts"] == 1
    assert result["real_damp_fallback_succeeded"] is True


def test_stop_everything_skips_damp_outside_real_mode(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: (damp_calls.append(mode) or (0, "")))

    result = stop_everything.run()

    assert damp_calls == []
    assert result["real_damp_fallback_rpc_code"] is None
    assert result["real_damp_fallback_attempts"] == 0
    assert result["real_damp_fallback_succeeded"] is None


def test_stop_everything_retries_real_damp_until_success(monkeypatch):
    # A dropped packet on the first attempt(s) shouldn't be a silent e-stop
    # failure -- it should retry and the result should reflect what actually
    # happened (attempts taken, eventual success).
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "REAL_DAMP_RETRY_DELAY_S", 0.0)
    codes = iter([3104, 3104, 0])  # fails twice, succeeds on the third attempt
    damp_calls = []

    def fake_call_sport(mode):
        damp_calls.append(mode)
        return next(codes), ""

    monkeypatch.setattr(g1_rpc, "call_sport", fake_call_sport)

    result = stop_everything.run()

    assert len(damp_calls) == 3
    assert result["real_damp_fallback_rpc_code"] == 0
    assert result["real_damp_fallback_attempts"] == 3
    assert result["real_damp_fallback_succeeded"] is True


def test_stop_everything_reports_failure_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "REAL_DAMP_RETRY_DELAY_S", 0.0)
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: (damp_calls.append(mode) or (3104, None)))

    result = stop_everything.run()

    assert len(damp_calls) == stop_everything.REAL_DAMP_MAX_ATTEMPTS
    assert result["real_damp_fallback_rpc_code"] == 3104
    assert result["real_damp_fallback_attempts"] == stop_everything.REAL_DAMP_MAX_ATTEMPTS
    assert result["real_damp_fallback_succeeded"] is False
