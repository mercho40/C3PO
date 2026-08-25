"""Tests for the rt/arm_sdk driver.

No DDS: the publish call is replaced with a recorder, so these assert the
*shape* of what would go on the wire and the order of the weight ramp, which
is where the danger actually is. The preconditions get most of the attention
because refusing to engage is this class's most important behaviour -- it is
disabled by default and every gate that lets it through has to be deliberate.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from bridge.teleop import arm_sdk
from bridge.teleop.arm_sdk import ArmSdkDriver, ArmSdkUnavailable
from bridge.teleop.retarget import ArmAngles


@pytest.fixture
def enabled(monkeypatch):
    """Satisfy every precondition, so a test can focus on one at a time."""
    monkeypatch.setenv("TELEOP_ARM_ENABLED", "1")
    monkeypatch.setattr(arm_sdk, "SIM_MODE", "real")

    state = {
        "arm_q": tuple(0.0 for _ in range(14)),
        "mode_machine": 5,
        "lowstate_age_s": 0.01,
        "fsm_id": 500,
        # Carried separately from lowstate_age_s on purpose — they come from
        # different snapshots, and the gate checks both.
        "fsm_age_s": 0.5,
    }

    class _Sampler:
        def get_arm_state(self):
            return state

    monkeypatch.setattr("bridge.sdk.state.get_sampler", lambda: _Sampler())
    return state


@pytest.fixture
def driver(monkeypatch):
    """A driver whose publishes are recorded instead of sent."""
    d = ArmSdkDriver()
    published: list[dict] = []

    def fake_publish():
        published.append({"weight": d._weight, "q": list(d._current)})
        d._published += 1

    monkeypatch.setattr(d, "_publish", fake_publish)
    d.published_frames = published  # type: ignore[attr-defined]
    return d


# -- preconditions ----------------------------------------------------------


async def test_refuses_without_the_env_flag(driver, enabled, monkeypatch):
    monkeypatch.delenv("TELEOP_ARM_ENABLED", raising=False)
    with pytest.raises(ArmSdkUnavailable, match="TELEOP_ARM_ENABLED"):
        await driver.engage()


async def test_refuses_outside_real_mode(driver, enabled, monkeypatch):
    # Publishing to rt/arm_sdk on a simulator nothing subscribes to is a
    # silent no-op that reads as success -- the worst kind of green light.
    monkeypatch.setattr(arm_sdk, "SIM_MODE", "isaac")
    with pytest.raises(ArmSdkUnavailable, match="SIM_MODE=real"):
        await driver.engage()


async def test_refuses_on_stale_lowstate(driver, enabled):
    enabled["lowstate_age_s"] = 5.0
    with pytest.raises(ArmSdkUnavailable, match="stale"):
        await driver.engage()


async def test_refuses_before_any_lowstate(driver, enabled):
    enabled["lowstate_age_s"] = None
    with pytest.raises(ArmSdkUnavailable, match="no rt/lowstate"):
        await driver.engage()


async def test_refuses_on_an_unsupported_fsm(driver, enabled):
    # 801 is "Run". Unitree documents arm_sdk for 4 / 500 / 501 only.
    enabled["fsm_id"] = 801
    with pytest.raises(ArmSdkUnavailable, match="not one of"):
        await driver.engage()


async def test_refuses_when_the_fsm_is_unknown(driver, enabled):
    # None means no motion controller is loaded (motion_switcher CheckMode
    # returned an empty name) -- there is nothing to blend into.
    enabled["fsm_id"] = None
    with pytest.raises(ArmSdkUnavailable, match="not one of"):
        await driver.engage()


async def test_refuses_on_a_variant_with_the_wrong_joint_count(driver, enabled):
    enabled["arm_q"] = tuple(0.0 for _ in range(10))
    with pytest.raises(ArmSdkUnavailable, match="expected 14"):
        await driver.engage()


async def test_refuses_while_a_gesture_is_running(driver, enabled):
    # The arm action service is itself built on rt/arm_sdk; two owners of that
    # topic is the documented cause of error 7400.
    from bridge.skills.task_runtime import get_registry

    task = get_registry().create("dance")
    try:
        with pytest.raises(ArmSdkUnavailable, match="7400"):
            await driver.engage()
    finally:
        task.status = "cancelled"
        task.ended_at = 0.0


# -- the ramp ---------------------------------------------------------------


async def test_engage_starts_from_the_measured_pose_at_zero_weight(driver, enabled):
    measured = tuple(0.1 * i for i in range(14))
    enabled["arm_q"] = measured

    await driver.engage()
    try:
        await asyncio.sleep(0)  # let the loop publish its first frame
        first = driver.published_frames[0]
        # Zero gap between commanded and measured is what makes the ramp safe;
        # the ramp itself is the second line of defence, not the first.
        assert first["q"] == pytest.approx(list(measured))
        assert first["weight"] == pytest.approx(arm_sdk.CONTROL_PERIOD_S / arm_sdk.RAMP_S)
    finally:
        await driver.release()


async def test_weight_rises_monotonically_and_never_steps(driver, enabled):
    await driver.engage()
    try:
        await asyncio.sleep(0.25)
    finally:
        await driver.release()

    weights = [f["weight"] for f in driver.published_frames]
    rising = weights[: weights.index(max(weights)) + 1]
    assert rising == sorted(rising)
    step = arm_sdk.CONTROL_PERIOD_S / arm_sdk.RAMP_S
    assert all(b - a <= step + 1e-9 for a, b in zip(rising, rising[1:]))


async def test_release_ramps_the_weight_back_to_zero(driver, enabled):
    await driver.engage()
    await asyncio.sleep(0.1)
    await driver.release()

    assert driver.engaged is False
    assert driver.published_frames[-1]["weight"] == pytest.approx(0.0)


async def test_cancellation_collapses_the_weight_immediately(driver, enabled):
    # A 2 s ramp is exactly the wrong thing to insist on when a stop is in
    # flight. One zero-weight frame hands the arms back at once.
    await driver.engage()
    await asyncio.sleep(0.05)
    task = driver._task
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert driver.published_frames[-1]["weight"] == 0.0


# -- slew rate --------------------------------------------------------------


async def test_targets_are_slewed_not_jumped(driver, enabled):
    await driver.engage()
    try:
        # A full-scale shoulder command the operator could produce in one frame.
        # Sent repeatedly, the way the dispatch loop sends it — a target this
        # far from the current one has to persist to be accepted at all (see
        # JUMP_CONFIRM_FRAMES), and one isolated frame asking for 86 degrees is
        # a singularity flip, not an operator.
        far = ArmAngles(1.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        for _ in range(arm_sdk.JUMP_CONFIRM_FRAMES):
            driver.command(far, far)
        await asyncio.sleep(0.1)
    finally:
        await driver.release()

    reached = driver.published_frames[-1]["q"][0]
    elapsed_steps = len(driver.published_frames)
    ceiling = arm_sdk.MAX_JOINT_RATE_RAD_S * arm_sdk.CONTROL_PERIOD_S * elapsed_steps
    assert 0 < reached <= ceiling + 1e-9


async def test_an_untracked_side_holds_rather_than_dropping(driver, enabled):
    await driver.engage()
    try:
        left = ArmAngles(0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        driver.command(left, None)
        assert driver._target[0:7] == pytest.approx(list(left.as_tuple()))
        # The right arm's target is untouched, not zeroed.
        assert driver._target[7:14] == [0.0] * 7
    finally:
        await driver.release()


async def test_stale_frames_freeze_the_target(driver, enabled, monkeypatch):
    await driver.engage()
    try:
        driver.command(ArmAngles(1.0, 0, 0, 0, 0, 0, 0), None)
        # Pretend the last frame arrived long ago.
        driver._last_frame_at = 0.0
        await asyncio.sleep(0.05)
        # Target has been collapsed onto current, so the arm stops chasing a
        # setpoint the operator may have left seconds ago.
        assert driver._target == pytest.approx(driver._current)
    finally:
        await driver.release()


# -- wire shape -------------------------------------------------------------


def test_command_writes_only_arm_slots_and_the_weight(enabled):
    pytest.importorskip("unitree_sdk2py.idl.default")
    d = ArmSdkDriver()
    d._mode_machine = 5
    d._current = [0.25] * 14
    d._weight = 0.5

    cmd = d._build_command()

    assert cmd.mode_machine == 5
    for slot in range(arm_sdk.MOTOR_SLOTS):
        motor = cmd.motor_cmd[slot]
        if 15 <= slot <= 28:
            # mode must be 1, or the firmware ignores the whole message
            # without complaint (unitree_rl_lab #44).
            assert motor.mode == 1
            assert motor.q == pytest.approx(0.25)
            assert motor.kp == arm_sdk.KP and motor.kd == arm_sdk.KD
        elif slot == arm_sdk.WEIGHT_SLOT:
            assert motor.q == pytest.approx(0.5)
        else:
            # Legs and waist are untouched: this path blends into the running
            # controller, it does not take the body.
            assert motor.mode == 0
            assert motor.q == 0.0
    assert cmd.crc != 0


async def test_request_release_returns_immediately_and_finishes_on_its_own(driver, enabled):
    # The dispatch loop cannot afford to wait out a 2 s ramp: an operator who
    # lowers their arms mid-turn would find the robot stops turning too.
    await driver.engage()
    await asyncio.sleep(0.05)

    started = asyncio.get_running_loop().time()
    driver.request_release()
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.05
    assert driver.engaged is True  # still ramping

    # The publish loop finishes the ramp and clears the flags by itself.
    for _ in range(200):
        if not driver.engaged:
            break
        await asyncio.sleep(0.02)

    assert driver.engaged is False
    assert driver.published_frames[-1]["weight"] == pytest.approx(0.0)


async def test_repeated_release_requests_are_idempotent(driver, enabled):
    await driver.engage()
    await asyncio.sleep(0.05)
    for _ in range(10):
        driver.request_release()

    weights = [f["weight"] for f in driver.published_frames]
    peak = weights.index(max(weights))
    falling = weights[peak:]
    # Still one smooth ramp down, not ten overlapping ones.
    assert falling == sorted(falling, reverse=True)
    await driver.release()


# -- failure containment ----------------------------------------------------


async def test_a_dead_publish_loop_is_not_retried_every_tick(driver, enabled, monkeypatch):
    """A failed loop must latch, not invite an immediate re-engage.

    `_run`'s error path clears `_engaged`, so the dispatch loop's next tick saw
    a disengaged driver and called `engage()` again — 20 times a second, each
    attempt building a publisher and ramping a weight, against a fault that is
    not going away.
    """
    boom = {"n": 0}

    def exploding_publish():
        boom["n"] += 1
        raise RuntimeError("DDS publisher went away")

    monkeypatch.setattr(driver, "_publish", exploding_publish)

    await driver.engage()
    for _ in range(50):
        if not driver.engaged:
            break
        await asyncio.sleep(0.02)
    assert driver.engaged is False

    # The dispatch loop would try again here. It must be refused, with a reason
    # the UI can show, rather than starting another doomed publish loop.
    with pytest.raises(ArmSdkUnavailable, match="publish loop failed"):
        await driver.engage()


async def test_letting_go_of_the_arms_clears_the_failure(driver, enabled, monkeypatch):
    # Recovery is deliberate, same shape as every other latch here: stop asking
    # for the arms, then ask again.
    monkeypatch.setattr(driver, "_publish", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    await driver.engage()
    for _ in range(50):
        if not driver.engaged:
            break
        await asyncio.sleep(0.02)
    with pytest.raises(ArmSdkUnavailable):
        await driver.engage()

    driver.clear_failure()

    published: list[dict] = []
    monkeypatch.setattr(driver, "_publish", lambda: published.append({"weight": driver._weight}))
    await driver.engage()
    try:
        await asyncio.sleep(0.05)
        assert published
    finally:
        await driver.release()


async def test_a_one_frame_flip_is_not_chased(driver, enabled):
    """The singularity guard, stated as the failure it prevents.

    `shoulder_pitch` spans half a circle, so the mapping from hand position to
    it has a branch cut, and the honest place for it is directly overhead. A
    hand held up there wobbling a degree either side of vertical flips the
    target between the two joint limits. The rate limiter cannot help: handed a
    target 180 degrees away it does exactly what it is for, and sweeps the arm
    the whole way at 0.6 rad/s. Five seconds of travel per wobble.
    """
    await driver.engage()
    try:
        here = ArmAngles(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        flipped = ArmAngles(math.pi / 2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        # Two frames of the flip, then back — a wobble, not a movement.
        driver.command(flipped, None)
        driver.command(flipped, None)
        driver.command(here, None)

        assert driver._target[0] == pytest.approx(0.0), (
            "a flip that did not persist must not become the target"
        )
    finally:
        await driver.release()


async def test_a_sustained_large_move_still_lands(driver, enabled):
    """The guard is a delay, not a refusal.

    An operator who genuinely swings an arm gets there 60 ms later. If a large
    but real movement could be rejected outright, the mirror would simply stop
    following past a certain speed, which is worse than the problem.
    """
    await driver.engage()
    try:
        far = ArmAngles(math.pi / 2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        for _ in range(arm_sdk.JUMP_CONFIRM_FRAMES):
            driver.command(far, None)

        assert driver._target[0] == pytest.approx(math.pi / 2)
    finally:
        await driver.release()


async def test_ordinary_motion_is_never_delayed(driver, enabled):
    """Everything under the threshold lands on the frame it arrives.

    0.6 rad/s of commanded rate is 0.7 degrees per frame at 50 Hz, so real
    teleoperation lives nowhere near the 60-degree threshold. If normal frames
    were being held even briefly, the mirror would feel laggy for no reason.
    """
    await driver.engage()
    try:
        for step in (0.1, 0.2, 0.35, 0.5):
            driver.command(ArmAngles(step, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), None)
            assert driver._target[0] == pytest.approx(step)
    finally:
        await driver.release()


async def test_a_stale_fsm_reading_refuses_the_arms(driver, enabled):
    """A healthy lowstate says nothing about the FSM.

    They arrive by different routes — lowstate is a streamed DDS topic, the FSM
    is polled over RPC — and only the lowstate's age was being checked. So the
    gate could pass on an FSM reading taken before the robot started walking,
    and blend arm setpoints into a walking humanoid.
    """
    enabled["fsm_age_s"] = arm_sdk.MAX_FSM_AGE_S + 1.0

    with pytest.raises(ArmSdkUnavailable, match="FSM reading"):
        await driver.engage()

    assert driver.engaged is False


async def test_a_never_read_fsm_refuses_the_arms(driver, enabled):
    enabled["fsm_age_s"] = None

    with pytest.raises(ArmSdkUnavailable, match="never been read"):
        await driver.engage()


async def test_cancelling_a_release_still_lets_go(driver, enabled):
    """Cancellation must not leave the driver claiming arms it dropped.

    `release()` used to re-raise CancelledError straight past the three lines
    that clear its state. The publish task was cancelled but `engaged` stayed
    True — so the next `_safe_stop` called `release()` again, found an
    already-cancelled task, and raised out of the teardown path, while the
    caller went on believing the arms had been handed back.
    """
    await driver.engage()
    assert driver.engaged is True

    task = asyncio.ensure_future(driver.release())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert driver.engaged is False, "a cancelled release must still let go"
    assert driver._task is None


async def test_release_is_idempotent_after_cancellation(driver, enabled):
    """The second teardown attempt must be a no-op, not an exception."""
    await driver.engage()
    task = asyncio.ensure_future(driver.release())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await driver.release()  # must not raise
