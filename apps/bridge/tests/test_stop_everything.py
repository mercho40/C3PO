"""Tests for stop_everything — had zero coverage despite being the
safety-critical e-stop. Two things matter most: (1) it actually interrupts
an in-flight walk_velocity task via the shared task registry, not just in
theory, and (2) the real-hardware Damp fallback only fires in real mode,
and there only when the robot did not acknowledge the zero-velocity burst.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills import _locomotion, stop_everything, walk_velocity
from bridge.skills.task_runtime import get_registry


@pytest.fixture(autouse=True)
def _no_real_dds(monkeypatch):
    # stop_motion_sync publishes to rt/run_command/cmd via a real DDS
    # ChannelPublisher — fine on the actual robot/sim, but there's no DDS
    # participant in a test process. Every test in this file calls
    # stop_everything.run(), so stub this out once for all of them: a burst the
    # robot acknowledged, the normal case. Tests of the Damp fallback swap in
    # an unacknowledged one with `_burst_unacknowledged`.
    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: (20, 20))
    # Real-mode stops also silence our speaker (voice/1004), another DDS RPC.
    monkeypatch.setattr(g1_rpc, "stop_play", lambda: (0, None))


def _burst_unacknowledged(monkeypatch):
    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: (20, 0))


@pytest.mark.asyncio
async def test_stop_everything_cancels_in_flight_walk_velocity(monkeypatch):
    velocity_calls = []
    monkeypatch.setattr(
        g1_rpc,
        "call_set_velocity",
        lambda vx, vy, vyaw, duration: velocity_calls.append((vx, vy, vyaw, duration)) or (0, ""),
    )
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    # walk_velocity dispatches through _locomotion, which is real-only here.
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")

    # Start a long-ish walk_velocity task in the background, same as a real
    # in-flight command would be.
    task_future = asyncio.ensure_future(walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=2.0))
    await asyncio.sleep(0.05)  # let it register in the task registry and issue its first call

    assert len(get_registry().list_active()) == 1

    result = await stop_everything.run()

    assert result["cancelled_count"] == 1
    walk_result = await task_future
    assert walk_result["status"] == "cancelled"
    # The walk re-issues its setpoint until stop_everything's cancellation
    # makes its loop notice and send zero — well before the original 2 s.
    assert velocity_calls[0][0] == 0.1
    assert velocity_calls[-1][:3] == (0.0, 0.0, 0.0)
    assert walk_result["duration_s"] < 1.0


async def test_an_acknowledged_stop_does_not_damp(monkeypatch):
    """2026-10-15: PARAR damped a walking robot whose burst had landed.

    On the hanger that is a sag; on the floor it is a fall. A stop the robot
    heard leaves it standing at zero velocity, and says so.
    """
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "DAMP_POLICY", "on_failure")
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    result = await stop_everything.run()

    assert damp_calls == []
    assert result["stop_burst_acked"] == 20
    assert result["real_damp_fallback_attempts"] == 0
    assert result["real_damp_fallback_reason"] is None
    # None, not False: the console reads only False as a failed stop.
    assert result["real_damp_fallback_succeeded"] is None


async def test_stop_everything_dispatches_damp_when_the_stop_was_not_heard(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    _burst_unacknowledged(monkeypatch)
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    result = await stop_everything.run()

    assert len(damp_calls) == 1
    assert result["real_damp_fallback_reason"] == "burst_unacknowledged"
    assert result["real_damp_fallback_rpc_code"] == 0
    assert result["real_damp_fallback_attempts"] == 1
    assert result["real_damp_fallback_succeeded"] is True


async def test_a_burst_that_raises_still_gets_the_damp(monkeypatch):
    """It used to propagate, which skipped the Damp: the one case it is for."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")

    def exploding_burst(**kwargs):
        raise RuntimeError("dds down")

    monkeypatch.setattr(stop_everything, "stop_motion_sync", exploding_burst)
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    result = await stop_everything.run()

    assert len(damp_calls) == 1
    assert result["real_damp_fallback_reason"] == "burst_failed"
    assert "dds down" in result["stop_burst_error"]
    assert result["real_damp_fallback_succeeded"] is True


async def test_the_always_policy_damps_after_every_stop(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "DAMP_POLICY", "always")
    damp_calls = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    result = await stop_everything.run()

    assert len(damp_calls) == 1
    assert result["real_damp_fallback_reason"] == "policy_always"


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
    _burst_unacknowledged(monkeypatch)
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
    _burst_unacknowledged(monkeypatch)
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
    _burst_unacknowledged(monkeypatch)
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
    _burst_unacknowledged(monkeypatch)

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


async def test_parar_stops_our_speech_beside_the_burst(monkeypatch):
    """PARAR left the robot talking: stop_everything never called stop_play."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    calls: list[str] = []
    monkeypatch.setattr(g1_rpc, "stop_play", lambda: calls.append("stop_play") or (0, None))

    result = await stop_everything.run()

    assert calls == ["stop_play"]
    assert result["speech_stop_rpc_code"] == 0


async def test_a_slow_speaker_does_not_hold_the_stop_answer(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "SPEECH_STOP_WAIT_S", 0.05)
    monkeypatch.setattr(g1_rpc, "stop_play", lambda: time.sleep(3.0) or (0, None))

    started = time.monotonic()
    result = await stop_everything.run()

    # Wide margin: this must hold on a loaded CI box, and 3 s is what it guards.
    assert time.monotonic() - started < 2.0
    assert result["speech_stop_rpc_code"] is None


async def test_stop_play_ends_a_play_pcm_still_sending_chunks(monkeypatch):
    """Stopping what reached the speaker is no use if the next chunk restarts it."""
    sent: list[int] = []

    class FakeVoice:
        def _CallRequestWithParamAndBin(self, api_id, param, chunk):
            sent.append(len(chunk))
            if len(sent) == 2:
                g1_rpc.cancel_pcm_playback()  # PARAR lands mid-utterance
            return 0, None

    monkeypatch.setattr(g1_rpc, "_get_voice_client", lambda: FakeVoice())
    pcm = b"\x00" * (g1_rpc.PLAY_CHUNK_BYTES * 5)

    code, data = g1_rpc.play_pcm(pcm, "c3po-1")

    assert len(sent) == 2
    assert (code, data) == (0, "stopped")


async def test_outside_real_mode_the_stop_does_not_touch_the_speaker(monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    calls: list[str] = []
    monkeypatch.setattr(g1_rpc, "stop_play", lambda: calls.append("x") or (0, None))

    result = await stop_everything.run()

    assert calls == []
    assert result["speech_stop_rpc_code"] is None


async def test_a_wall_clock_step_does_not_cut_the_damp_retries_short(monkeypatch):
    """The robot has no RTC: NTP steps its wall clock forward after boot.

    A deadline on time.time() then read as long past after one failed attempt,
    and the burst's duration came out as hours.
    """
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    _burst_unacknowledged(monkeypatch)
    monkeypatch.setattr(stop_everything, "REAL_DAMP_RETRY_DELAY_S", 0.0)
    real_time = time.time
    step = iter(range(0, 10**6, 3600))
    monkeypatch.setattr(time, "time", lambda: real_time() + next(step))
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: (3104, None))

    result = await stop_everything.run()

    assert result["real_damp_fallback_attempts"] == stop_everything.REAL_DAMP_MAX_ATTEMPTS
    assert result["stop_burst_duration_s"] < 1.0


def _real_burst(monkeypatch, call_set_velocity):
    """The real stop_motion_sync (the autouse fixture fakes it) on a fake link."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "stop_motion_sync", _locomotion.stop_motion_sync)
    monkeypatch.setattr(g1_rpc, "call_set_velocity", call_set_velocity)


async def test_one_hung_velocity_reply_does_not_damp_a_robot_that_heard_the_rest(monkeypatch):
    """A1: the burst waited on each 10 s ack in turn, so one slow reply ate it,
    acked came back 0 and the fallback damped a robot standing on the floor."""
    hang = threading.Event()
    calls: list = []

    def call_set_velocity(*a):
        first = not calls
        calls.append(a)
        if first:
            hang.wait(5.0)
            return 3104, None
        return 0, ""

    _real_burst(monkeypatch, call_set_velocity)
    damp_calls: list = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    started = time.monotonic()
    try:
        result = await stop_everything.run()
    finally:
        hang.set()

    assert damp_calls == []
    assert result["stop_burst_acked"] > 0
    assert result["real_damp_fallback_reason"] is None
    assert time.monotonic() - started < 2.0


async def test_a_burst_nobody_answers_still_damps_within_the_budget(monkeypatch):
    hang = threading.Event()

    def call_set_velocity(*a):
        hang.wait(5.0)
        return 3104, None

    _real_burst(monkeypatch, call_set_velocity)
    damp_calls: list = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    started = time.monotonic()
    try:
        result = await stop_everything.run()
    finally:
        hang.set()

    assert damp_calls == [g1_protocol.Mode.DAMP]
    assert result["real_damp_fallback_reason"] == "burst_unacknowledged"
    assert result["real_damp_fallback_succeeded"] is True
    assert time.monotonic() - started < 2.0


async def test_a_damp_that_never_answers_ends_at_the_budget(monkeypatch):
    """The budget was checked only after an attempt returned: one attempt on the
    10 s sport timeout held the stop's answer for 10 s, not 4."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    _burst_unacknowledged(monkeypatch)
    monkeypatch.setattr(stop_everything, "REAL_DAMP_TOTAL_BUDGET_S", 0.2)
    hang = threading.Event()
    damp_calls: list = []

    def call_sport(mode):
        damp_calls.append(mode)
        hang.wait(5.0)
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_sport", call_sport)

    started = time.monotonic()
    try:
        result = await stop_everything.run()
    finally:
        hang.set()

    assert time.monotonic() - started < 1.5
    assert damp_calls == [g1_protocol.Mode.DAMP]  # it went out; the answer did not come back
    assert result["real_damp_fallback_rpc_code"] == 3104
    assert result["real_damp_fallback_succeeded"] is False


async def test_a_retry_the_budget_ran_out_on_is_not_counted_as_sent(monkeypatch):
    """asyncio.wait_for given no time left cancels its attempt before it starts.
    The budget ran out in the pause before a retry, and that retry was counted
    as a Damp attempt although nothing went out."""
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    _burst_unacknowledged(monkeypatch)
    monkeypatch.setattr(stop_everything, "REAL_DAMP_TOTAL_BUDGET_S", 0.2)
    monkeypatch.setattr(stop_everything, "REAL_DAMP_RETRY_DELAY_S", 0.3)
    damp_calls: list = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (3104, None))

    started = time.monotonic()
    result = await stop_everything.run()

    assert damp_calls == [g1_protocol.Mode.DAMP]
    assert result["real_damp_fallback_attempts"] == len(damp_calls)
    assert result["real_damp_fallback_rpc_code"] == 3104
    assert time.monotonic() - started < 1.5


async def test_a_full_default_executor_does_not_hold_the_stop(monkeypatch):
    """asyncio.to_thread queues on the loop's shared executor, which camera
    streams, voice long-polls and RPCs hung on a bad link can fill. The hand
    release and the zero burst waited behind them, and a Damp attempt still
    queued when its budget ran out was cancelled without being sent."""
    from concurrent.futures import ThreadPoolExecutor

    from bridge.teleop import hands

    class FakeHandDriver(hands.HandDriver):
        name = "brainco"
        sides = ("right",)

        def send(self, side, grip):
            pass

    monkeypatch.setattr(hands, "get_driver", lambda: FakeHandDriver())
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(stop_everything, "REAL_DAMP_TOTAL_BUDGET_S", 0.3)
    monkeypatch.setattr(stop_everything, "SPEECH_STOP_WAIT_S", 0.05)
    burst_at: list[float] = []

    def burst(**kwargs):
        burst_at.append(time.monotonic())
        return 20, 0  # unheard, so Damp has to go out too

    monkeypatch.setattr(stop_everything, "stop_motion_sync", burst)
    damp_calls: list = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp_calls.append(mode) or (0, ""))

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    release = threading.Event()
    busy = loop.run_in_executor(None, release.wait, 2.0)
    started = time.monotonic()
    try:
        result = await stop_everything.run()
    finally:
        release.set()
        await busy

    assert burst_at and burst_at[0] - started < 0.2
    assert damp_calls == [g1_protocol.Mode.DAMP]
    assert result["real_damp_fallback_succeeded"] is True
    assert result["hands_relaxed"] is True
