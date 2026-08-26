"""Tests for stop_everything — had zero coverage despite being the
safety-critical e-stop. Two things matter most: (1) it actually interrupts
an in-flight walk_velocity task via the shared task registry, not just in
theory, and (2) the real-hardware Damp fallback only fires in real mode.
"""

from __future__ import annotations

import asyncio
import time

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
        "call_set_velocity",
        lambda vx, vy, vyaw, duration: velocity_calls.append((vx, vy, vyaw, duration)) or (0, ""),
    )
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")

    # Start a long-ish walk_velocity task in the background, same as a real
    # in-flight command would be.
    task_future = asyncio.ensure_future(walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=2.0))
    await asyncio.sleep(0.05)  # let it register in the task registry and issue its first call

    assert len(get_registry().list_active()) == 1

    result = await stop_everything.run()

    assert result["cancelled_count"] == 1
    walk_result = await task_future
    assert walk_result["status"] == "cancelled"
    # First call was the real command; stop_everything's cancellation should
    # have made walk_velocity's own loop notice and send a zero-velocity stop
    # well before the original 2s duration elapsed.
    assert len(velocity_calls) == 2
    assert velocity_calls[-1][:3] == (0.0, 0.0, 0.0)


async def test_stop_everything_dispatches_damp_in_real_mode(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    result = await stop_everything.run()

    assert len(damp_calls) == 1
    assert result["real_damp_fallback_rpc_code"] == 0
    assert result["real_damp_fallback_attempts"] == 1
    assert result["real_damp_fallback_succeeded"] is True


async def test_stop_everything_skips_damp_outside_real_mode(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    result = await stop_everything.run()

    assert damp_calls == []
    assert result["real_damp_fallback_rpc_code"] is None
    assert result["real_damp_fallback_attempts"] == 0
    assert result["real_damp_fallback_succeeded"] is None


async def test_stop_everything_retries_real_damp_until_success(monkeypatch):
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

    result = await stop_everything.run()

    assert len(damp_calls) == 3
    assert result["real_damp_fallback_rpc_code"] == 0
    assert result["real_damp_fallback_attempts"] == 3
    assert result["real_damp_fallback_succeeded"] is True


async def test_stop_everything_reports_failure_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "REAL_DAMP_RETRY_DELAY_S", 0.0)
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (3104, None))

    result = await stop_everything.run()

    assert len(damp_calls) == stop_everything.REAL_DAMP_MAX_ATTEMPTS
    assert result["real_damp_fallback_rpc_code"] == 3104
    assert result["real_damp_fallback_attempts"] == stop_everything.REAL_DAMP_MAX_ATTEMPTS
    assert result["real_damp_fallback_succeeded"] is False


async def test_real_damp_retries_stop_at_the_time_budget(monkeypatch):
    """A slow link must not let the e-stop retry loop run for ~30 s.

    Each Damp attempt can sit on the sport client's 10 s timeout, so bounding
    by attempt count alone gave a worst case of 3 x 10 s + sleeps. This is the
    degraded-link case the retry exists for, which made it precisely the case
    where the e-stop took longest to answer.
    """
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "REAL_DAMP_TOTAL_BUDGET_S", 0.05)

    attempts = 0

    def slow_failing_call_sport(mode):
        nonlocal attempts
        attempts += 1
        time.sleep(0.06)  # each attempt alone overruns the budget
        return 3104, None

    from bridge.sdk import g1_rpc

    monkeypatch.setattr(g1_rpc, "call_sport", slow_failing_call_sport)

    result = await stop_everything.run()

    # Stopped after the first over-budget attempt rather than using all three.
    assert attempts == 1, f"expected budget to halt retries, got {attempts} attempts"
    assert result["real_damp_fallback_succeeded"] is False
    assert result["real_damp_fallback_attempts"] == 1


async def test_cancellation_is_signalled_before_any_blocking_rpc(monkeypatch):
    """Cancel flags must be set even if the RPC path then blocks or fails.

    This is the ordering that makes the e-stop safe regardless of link health:
    the skills start stopping themselves off their own cancel_event, so a
    wedged DDS call cannot prevent the stop from beginning.
    """
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")

    from bridge.sdk import g1_rpc
    from bridge.skills.task_runtime import get_registry

    registry = get_registry()
    task = registry.create("fake_walk")

    def exploding_call_sport(mode):
        raise RuntimeError("link is down")

    monkeypatch.setattr(g1_rpc, "call_sport", exploding_call_sport)

    try:
        with pytest.raises(RuntimeError):
            await stop_everything.run()

        assert task.cancel_event.is_set(), "cancel must be signalled before the RPC path"
    finally:
        # The registry is a module-level singleton, so a task left "running"
        # leaks into every later test that counts active tasks. Nothing else
        # will finish this one -- it has no coroutine behind it.
        task.status = "cancelled"
        task.ended_at = time.time()


@pytest.mark.asyncio
async def test_stop_releases_the_arm_sdk_and_relaxes_the_hands(monkeypatch):
    """move_arm(hold=True) leaves a 50 Hz loop holding a pose with no task
    alive to cancel, and a BrainCo grip has no firmware dead-man. The e-stop
    is the release path of last resort for both — pin that it actually asks."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")

    from bridge.teleop import arm_sdk, hands

    class FakeArmDriver:
        engaged = True

        def __init__(self):
            self.release_requested = False

        def request_release(self):
            self.release_requested = True

    class FakeHandDriver(hands.HandDriver):
        name = "brainco"
        sides = ("right",)

        def __init__(self):
            self.sent = []

        def send(self, side, grip):
            self.sent.append((side, grip))

    arm_driver = FakeArmDriver()
    hand_driver = FakeHandDriver()
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: arm_driver)
    monkeypatch.setattr(hands, "get_driver", lambda: hand_driver)

    result = await stop_everything.run()

    assert arm_driver.release_requested
    assert result["arm_sdk_release_requested"] is True
    assert hand_driver.sent == [("right", 0.0)]
    assert result["hands_relaxed"] is True


@pytest.mark.asyncio
async def test_stop_survives_a_broken_teleop_stack(monkeypatch):
    """A failure in the arm/hand release must never break the e-stop itself."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")

    from bridge.teleop import arm_sdk, hands

    def explode():
        raise RuntimeError("teleop is broken")

    monkeypatch.setattr(arm_sdk, "get_driver", explode)
    monkeypatch.setattr(hands, "get_driver", explode)

    result = await stop_everything.run()

    assert result["arm_sdk_release_requested"] is False
    assert result["hands_relaxed"] is False
