"""Tests for the move_arm skill (`bridge.skills.arm_pose`).

The driver underneath (`ArmSdkDriver`) has its own tests; what is pinned here
is the seam: semantic-degrees in, clamped/sign-corrected wiring targets out,
the driver's refusals surfacing honestly, unnamed joints holding their current
targets, and cancellation handing the arms back instead of leaving a 50 Hz
loop holding a pose nobody asked to keep.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from bridge.skills import arm_pose
from bridge.skills.task_runtime import get_registry
from bridge.teleop import arm_sdk
from bridge.teleop.retarget import JOINT_NAMES, ArmAngles


class FakeDriver:
    """Instant-slew stand-in: command() teleports current to target."""

    def __init__(self, settle: bool = True) -> None:
        self.engaged = False
        self.weight = 1.0
        self._targets = [0.0] * 14
        self._current = [0.0] * 14
        self.settle = settle
        self.release_requested = False
        self.released = False
        self.commands: list[tuple[ArmAngles | None, ArmAngles | None]] = []

    async def engage(self) -> None:
        self.engaged = True

    @property
    def target_angles(self) -> tuple[float, ...]:
        return tuple(self._targets)

    @property
    def current_angles(self) -> tuple[float, ...]:
        return tuple(self._current)

    def command(self, left: ArmAngles | None, right: ArmAngles | None) -> None:
        self.commands.append((left, right))
        if left is not None:
            self._targets[0:7] = list(left.as_tuple())
        if right is not None:
            self._targets[7:14] = list(right.as_tuple())
        if self.settle:
            self._current = list(self._targets)

    def request_release(self) -> None:
        self.release_requested = True

    async def release(self) -> None:
        self.released = True
        self.engaged = False

    def clear_failure(self) -> None:
        pass

    def status(self) -> dict:
        return {"failed": None}


def test_semantic_to_wiring_clamps_then_signs():
    # 200 deg shoulder_pitch is clamped to the +90 envelope, THEN the measured
    # -1 wiring sign applies — order matters, or the clamp box would be
    # mirrored for negative-signed joints.
    wiring = arm_pose.semantic_to_wiring("right", {"shoulder_pitch": math.radians(200)})
    assert wiring["shoulder_pitch"] == pytest.approx(-math.radians(90))

    # Roll is mirrored between arms (shared body-frame axis): the same
    # semantic "away from the body" is + on the right and - on the left.
    semantic = {"shoulder_roll": math.radians(30)}
    assert arm_pose.semantic_to_wiring("right", semantic)["shoulder_roll"] == pytest.approx(
        math.radians(30)
    )
    assert arm_pose.semantic_to_wiring("left", semantic)["shoulder_roll"] == pytest.approx(
        -math.radians(30)
    )


def test_compose_targets_leaves_unnamed_joints_alone():
    base = tuple(float(i) / 100 for i in range(14))  # distinct per slot
    left, right, _ = arm_pose._compose_targets("right", {"elbow": math.radians(45)}, base)
    assert left is None
    assert right is not None
    elbow_i = JOINT_NAMES.index("elbow")
    for i, value in enumerate(right.as_tuple()):
        if i == elbow_i:
            assert value == pytest.approx(math.radians(45))
        else:
            assert value == pytest.approx(base[7 + i])


@pytest.mark.asyncio
async def test_rejects_unknown_and_empty_joint_sets():
    result = await arm_pose.run("right", {"shoulder_pitch": 10, "flipper": 5})
    assert result["status"] == "failed"
    assert "flipper" in result["error"]

    result = await arm_pose.run("right", {})
    assert result["status"] == "failed"
    assert result["error"] == "no_joints_given"


@pytest.mark.asyncio
async def test_stub_mode_reports_clamped_degrees():
    result = await arm_pose.run("right", {"shoulder_roll": 120.0})
    assert result["status"] == "completed"
    assert result["phase"] == "stub"
    assert result["result"]["commanded_deg"]["shoulder_roll"] == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_driver_refusal_surfaces_as_unavailable(monkeypatch):
    monkeypatch.setattr(arm_pose, "SIM_MODE", "real")

    class RefusingDriver:
        async def engage(self) -> None:
            raise arm_sdk.ArmSdkUnavailable("arm teleop is disabled: set TELEOP_ARM_ENABLED=1")

    monkeypatch.setattr(arm_sdk, "get_driver", lambda: RefusingDriver())

    result = await arm_pose.run("right", {"elbow": 30.0})
    assert result["status"] == "failed"
    assert result["phase"] == "unavailable"
    assert "TELEOP_ARM_ENABLED" in result["error"]


@pytest.mark.asyncio
async def test_real_mode_settles_and_holds(monkeypatch):
    monkeypatch.setattr(arm_pose, "SIM_MODE", "real")
    fake = FakeDriver()
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: fake)

    result = await arm_pose.run("right", {"shoulder_pitch": 45.0}, hold=True)

    assert result["status"] == "completed"
    assert result["phase"] == "holding"
    assert fake.engaged, "hold=True must leave the driver engaged"
    # Wiring frame: measured -1 sign on shoulder_pitch.
    _, right = fake.commands[-1]
    assert right.shoulder_pitch == pytest.approx(-math.radians(45))


@pytest.mark.asyncio
async def test_hold_false_releases_after_settle(monkeypatch):
    monkeypatch.setattr(arm_pose, "SIM_MODE", "real")
    fake = FakeDriver()
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: fake)

    result = await arm_pose.run("both", {"elbow": 20.0}, hold=False)

    assert result["status"] == "completed"
    assert result["phase"] == "released"
    assert fake.released
    # 'both' commands each arm; elbow sign is +1 on both sides.
    left, right = fake.commands[-1]
    assert left.elbow == pytest.approx(math.radians(20))
    assert right.elbow == pytest.approx(math.radians(20))


@pytest.mark.asyncio
async def test_cancellation_hands_the_arms_back(monkeypatch):
    monkeypatch.setattr(arm_pose, "SIM_MODE", "real")
    fake = FakeDriver(settle=False)  # never reaches the target
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: fake)

    run_future = asyncio.ensure_future(arm_pose.run("right", {"shoulder_pitch": 60.0}))
    await asyncio.sleep(0.15)  # let it engage and start commanding

    registry = get_registry()
    active = [t for t in registry.list_active() if t.skill_name == "move_arm"]
    assert active, "move_arm must register a cancellable task"
    registry.cancel(active[0].task_id)

    result = await run_future
    assert result["status"] == "cancelled"
    assert fake.release_requested, "cancel must start the ramp-down"


def test_move_arm_engage_is_refused_while_a_generic_gesture_runs(monkeypatch):
    """The contention check lives in the real driver's preconditions, and its
    skill list must cover the NEW generic `gesture` skill — a gesture running
    under that name has to block rt/arm_sdk engagement exactly like `wave`."""
    from bridge.sdk import state as state_module

    class FakeSampler:
        def get_arm_state(self):
            return {
                "lowstate_age_s": 0.1,
                "arm_q": tuple([0.0] * 14),
                "fsm_age_s": 0.5,
                "fsm_id": 4,
                "mode_machine": 5,
            }

    monkeypatch.setenv("TELEOP_ARM_ENABLED", "1")
    monkeypatch.setattr(arm_sdk, "SIM_MODE", "real")
    monkeypatch.setattr(state_module, "get_sampler", lambda: FakeSampler())

    task = get_registry().create("gesture")
    try:
        driver = arm_sdk.ArmSdkDriver()
        with pytest.raises(arm_sdk.ArmSdkUnavailable, match="gesture task is running"):
            driver._check_preconditions()
    finally:
        task.status = "completed"
