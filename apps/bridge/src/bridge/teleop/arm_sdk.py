"""Publish arm joint targets to `rt/arm_sdk`. The dangerous half of teleop.

`rt/arm_sdk` does not bypass the locomotion FSM — it *blends into* it:

    executed = motion_controller_cmd * (1 - w) + our_cmd * w

with `w` carried in `motor_cmd[29].q`, range 0..1 (index 29 is `kNotUsedJoint`,
not a real motor). The legs stay under the high-level controller throughout.
Official docs restrict the path to FSM 4 / 500 / 501 — Locked Stance and the
two Movement Control modes — which is the useful part: **arm control works
while merely standing**, no gait required. `docs/ROBOT-API.md` has
the sourcing for all of the above.

Four things will hurt someone here, and each has a countermeasure below:

1. **Stepping the weight.** Unitree: *"If there is a gap between the positions
   of the motion control commands and the user commands ... suddenly changing
   the weight value may cause the robotic arm to move at high speed."* So the
   ramp both rises and falls over `RAMP_S`, and — more important than the ramp
   — engagement *starts from the measured arm pose* (`get_arm_state()["arm_q"]`)
   so there is no gap to cross in the first place.
2. **Contention.** The arm action service (our `wave`, `dance`, every gesture)
   is itself implemented on `rt/arm_sdk`. Two owners of one topic is the
   documented cause of `7400`. This driver therefore refuses to engage while a
   gesture task is running, and gestures should not be dispatched while it is
   engaged.
3. **Driving it while walking.** `unitree_sdk2_python` #146: a G1 driven
   through `arm_sdk` *while walking* bent forward at the waist and lost
   balance. Untested and unresolved. Bring this up standing.
4. **A silent no-op.** `mode_machine` in the command must match the value in
   `rt/lowstate`, and each `motor_cmd[i].mode` must be 1, or the firmware
   ignores the message without complaint. Two independent reports in
   `unitree_rl_lab` #44 converge on this. Both are set from live state here,
   and a stale `lowstate` blocks engagement rather than shipping a guess.

Enablement
----------
**Off unless `TELEOP_ARM_ENABLED=1` and `SIM_MODE=real`.** Not a config knob —
a deliberate second hand on the switch. The joint sign conventions this feeds
on (`retarget.JOINT_SIGNS`) are unverified, and an unverified sign on a
shoulder is an arm swinging the opposite way from the one the operator
expects. Run `scripts/arm_sign_check.py` with a person watching the robot,
settle the signs, then set the variable.

Never live-tested. Same posture as `walk_velocity` and `dance`: written from
documented interfaces, verified against fakes, and not to be trusted until a
human has watched it run.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

import structlog

from bridge.teleop.retarget import NEUTRAL, ArmAngles, clamp

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

ARM_SDK_TOPIC = "rt/arm_sdk"

# Motor array layout — docs/ROBOT-API.md §9.3.
LEFT_ARM_BASE = 15
RIGHT_ARM_BASE = 22
WEIGHT_SLOT = 29
MOTOR_SLOTS = 35

# Vendor example gains for the arm_sdk path.
KP = 60.0
KD = 1.5

CONTROL_HZ = 50
CONTROL_PERIOD_S = 1.0 / CONTROL_HZ

# Weight ramp duration, each way. The vendor's own release is
# `np.linspace(1, 0, num=101)` at 0.02 s = 2.0 s; matched rather than improved.
RAMP_S = 2.0

# Maximum joint speed this driver will ever slew a target at, rad/s. The
# operator's own hands can move far faster than is safe to mirror onto a
# 1.3 m humanoid, and hand tracking spikes when a hand re-enters the tracking
# volume. ~34 deg/s is slow enough to watch and step away from.
MAX_JOINT_RATE_RAD_S = 0.6

# A target jump larger than this has to persist before it is accepted.
#
# The rate limiter above bounds how fast the arm moves, not how far. Handed a
# target 180 degrees away it does its job perfectly: it sweeps the arm the
# whole way, at 0.6 rad/s, for five seconds.
#
# Something does hand it exactly that. `shoulder_pitch` spans half a circle, so
# any mapping from hand position to it has a branch cut somewhere, and the
# honest place for it is directly overhead (see `_shoulder_pitch` in
# retarget.py). A hand held up there, wobbling a degree either side of
# vertical, flips the target between the two joint limits — and without this,
# each flip launches another five-second sweep, in whichever direction the
# wobble last landed.
#
# 60 degrees is well above any single-frame motion a human arm produces at
# 50 Hz (0.6 rad/s of commanded rate is 0.7 degrees per frame) and well below
# the 180 the singularity produces.
MAX_TARGET_JUMP_RAD = math.radians(60)

# How many consecutive frames a large jump must persist before it is accepted.
#
# Not a rejection — a delay. An operator who genuinely moves fast, or who
# really does want the arm on the other side, gets there 60 ms later and will
# not notice. A singularity flip does not survive it, because a wobble crosses
# back before the count is met.
JUMP_CONFIRM_FRAMES = 3

# Refuse to engage on state older than this. `rt/lf/lowstate` runs ~20 Hz, so
# 0.5 s is ten missed messages — comfortably a dead link, not a hiccup.
MAX_LOWSTATE_AGE_S = 0.5

# How old the FSM reading may be when deciding whether it is safe to take the
# arms. Much more generous than the lowstate limit because the FSM is polled
# over RPC rather than streamed — seconds between reads is normal, and holding
# it to a streaming topic's standard would refuse the arms constantly. What it
# must not do is accept a reading from before the robot changed state.
MAX_FSM_AGE_S = 5.0

# FSM ids where Unitree documents `rt/arm_sdk` as supported.
ALLOWED_FSM_IDS = frozenset({4, 500, 501})

# Stop commanding if the operator's frames stop arriving. Shorter than the
# locomotion dead-man because a stalled arm holding a pose mid-air is a worse
# resting state than a stopped walk.
FRAME_TIMEOUT_S = 0.5


class ArmSdkUnavailable(RuntimeError):
    """Engagement refused. The message says which precondition failed."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() in {"1", "true", "yes", "on"}


class ArmSdkDriver:
    """Owns the 50 Hz `rt/arm_sdk` publish loop and the blend weight.

    Deliberately decoupled from the WebSocket frame rate: the headset runs at
    72-120 Hz and drops frames when tracking is lost, while this path wants a
    steady 50 Hz with a bounded slew rate regardless. `command()` only moves a
    target; the loop is what talks to the robot.
    """

    def __init__(self) -> None:
        self._publisher: Any = None
        self._task: asyncio.Task[None] | None = None
        self._engaged = False
        self._releasing = False
        self._weight = 0.0
        # Current (commanded) and target joint angles, 14 values: left then right.
        self._current: list[float] = [0.0] * 14
        self._target: list[float] = [0.0] * 14
        self._mode_machine = 0
        #: Monotonic, not wall clock — see the note above STALE_FRAME_S in
        #: server.py. This one guards the arm-hold timeout, and a backwards
        #: clock step here holds a stale arm pose indefinitely instead of
        #: ramping the arm_sdk weight down.
        #: Per-joint count of consecutive frames asking for an implausibly
        #: large target change. See `_set_target`.
        self._jump_frames = [0] * 14
        self._last_frame_at = 0.0
        self._published = 0
        # Set when the publish loop dies. Latched, because its error path
        # clears `_engaged`, and the dispatch loop reads that as "not engaged
        # yet" and calls engage() again — 20 times a second, each attempt
        # building a publisher and ramping a weight, against a fault that is
        # not going away. Cleared only by letting go of the arms.
        self._failed: str | None = None

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def weight(self) -> float:
        return self._weight

    def clear_failure(self) -> None:
        """Allow engaging again after a publish-loop failure.

        Deliberate, same shape as every other latch here: the operator stops
        asking for the arms, then asks again. `server._dispatch_arms` calls
        this on the falling edge.
        """
        if self._failed is not None:
            log.info("arm_sdk.failure_cleared", was=self._failed)
            self._failed = None

    def status(self) -> dict[str, Any]:
        return {
            "engaged": self._engaged,
            "failed": self._failed,
            "releasing": self._releasing,
            "weight": round(self._weight, 3),
            "published": self._published,
            "enabled_by_env": _env_flag("TELEOP_ARM_ENABLED"),
            "sim_mode": SIM_MODE,
        }

    # -- preconditions --------------------------------------------------

    def _check_preconditions(self) -> tuple[tuple[float, ...], int]:
        """Raise `ArmSdkUnavailable` unless it is safe to engage.

        Returns the measured arm pose and `mode_machine` on success, so the
        caller cannot engage using values read at a different instant than the
        ones that were checked.
        """
        if self._failed is not None:
            raise ArmSdkUnavailable(
                f"the rt/arm_sdk publish loop failed ({self._failed}) and has not been "
                "reset. Let go of the arms and ask again to retry."
            )
        if not _env_flag("TELEOP_ARM_ENABLED"):
            raise ArmSdkUnavailable(
                "arm teleop is disabled: set TELEOP_ARM_ENABLED=1 to allow rt/arm_sdk. "
                "Joint sign conventions are unverified — run scripts/arm_sign_check.py first, "
                "with someone watching the robot."
            )
        if SIM_MODE != "real":
            raise ArmSdkUnavailable(
                f"arm teleop requires SIM_MODE=real (this process has {SIM_MODE!r}); "
                "no simulator we run subscribes rt/arm_sdk, so publishing there would be a "
                "silent no-op that reads as success."
            )

        from bridge.sdk.state import get_sampler

        state = get_sampler().get_arm_state()
        age = state["lowstate_age_s"]
        if age is None:
            raise ArmSdkUnavailable(
                "no rt/lowstate received yet — cannot read mode_machine or the current arm "
                "pose, and both are required for a gapless engage."
            )
        if age > MAX_LOWSTATE_AGE_S:
            raise ArmSdkUnavailable(
                f"rt/lowstate is stale ({age:.2f}s > {MAX_LOWSTATE_AGE_S}s); the link to the "
                "robot is not healthy enough to start blending into its arm controller."
            )

        arm_q: tuple[float, ...] = state["arm_q"]
        if len(arm_q) != 14:
            raise ArmSdkUnavailable(
                f"lowstate reported {len(arm_q)} arm joints, expected 14 (G1JointIndex 15-28). "
                "A 23-DoF or arms-only variant needs its own slot mapping before this runs."
            )

        fsm_age = state["fsm_age_s"]
        if fsm_age is None:
            raise ArmSdkUnavailable(
                "the FSM state has never been read, so there is no way to tell whether the "
                "robot is standing locked or walking. Both are possible and only one is safe "
                "to blend arm setpoints into."
            )
        if fsm_age > MAX_FSM_AGE_S:
            raise ArmSdkUnavailable(
                f"the FSM reading is {fsm_age:.1f}s old (> {MAX_FSM_AGE_S}s). It is polled over "
                "RPC, separately from rt/lowstate, so a healthy lowstate says nothing about "
                "whether this number is current — and a robot that started walking since it "
                "was taken still reads as the state it was in when it stopped answering."
            )

        fsm_id = state["fsm_id"]
        if fsm_id not in ALLOWED_FSM_IDS:
            raise ArmSdkUnavailable(
                f"FSM {fsm_id} is not one of {sorted(ALLOWED_FSM_IDS)} (Locked Stance / Movement "
                "Control 1 / Movement Control 2), the only states Unitree documents rt/arm_sdk "
                "as supported in. An empty or unknown FSM usually means no motion controller is "
                "loaded at all — run motion_switcher CheckMode."
            )

        from bridge.skills.task_runtime import get_registry

        gestures = [t for t in get_registry().list_active() if t.skill_name in _GESTURE_SKILLS]
        if gestures:
            raise ArmSdkUnavailable(
                f"a gesture task is running ({', '.join(t.skill_name for t in gestures)}). The arm "
                "action service is itself built on rt/arm_sdk, and two owners of that topic is "
                "the documented cause of error 7400. Let it finish, or stop_everything first."
            )

        return arm_q, int(state["mode_machine"])

    # -- lifecycle ------------------------------------------------------

    async def engage(self) -> None:
        """Take the arms, starting from wherever they currently are."""
        if self._engaged:
            return
        arm_q, mode_machine = self._check_preconditions()

        self._mode_machine = mode_machine
        # Both current and target start at the *measured* pose. Countermeasure
        # 1 in the module docstring: with no gap, the ramp has nothing to drag
        # the arm across, and the first operator frame moves it from there at
        # MAX_JOINT_RATE_RAD_S like every frame after it.
        self._current = list(arm_q)
        self._target = list(arm_q)
        self._jump_frames = [0] * 14
        self._weight = 0.0
        self._releasing = False
        self._last_frame_at = time.monotonic()
        self._engaged = True
        self._task = asyncio.create_task(self._run())
        log.info(
            "arm_sdk.engaged", mode_machine=mode_machine, start_pose=[round(q, 3) for q in arm_q]
        )

    def request_release(self) -> None:
        """Start the ramp-down without waiting for it. For the control loop.

        `release()` takes RAMP_S (2 s) to finish, and awaiting that from inside
        the dispatch loop would stall locomotion for the whole ramp — an
        operator who lowers their arms mid-turn would find the robot stops
        turning for two seconds. The publish loop already knows how to ramp
        down and how to clean up after itself, so this only has to raise the
        flag and get out of the way.
        """
        if not self._engaged or self._releasing:
            return
        self._releasing = True
        log.info("arm_sdk.releasing", weight=round(self._weight, 3))

    async def release(self) -> None:
        """Ramp the weight to zero and wait for the arms to be handed back.

        Holds the last commanded pose throughout: the arm must not move while
        authority is being transferred, or the transfer itself becomes the
        motion. Once the weight reaches 0 the built-in controller owns the arms
        again and will restore its own posture at its own rate.

        Use this on shutdown paths, where waiting is the point. Inside a
        control loop use `request_release()`.
        """
        if not self._engaged:
            return
        self.request_release()
        task = self._task
        try:
            if task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=RAMP_S + 2.0)
                except asyncio.TimeoutError:
                    # The loop should exit on its own once the weight hits 0.
                    # If it has not, cancelling is still better than holding
                    # the arms: with no publisher the firmware's own arm_sdk
                    # timeout applies.
                    log.warning("arm_sdk.release_timeout")
                    task.cancel()
                except asyncio.CancelledError:
                    task.cancel()
                    raise
        finally:
            # In a `finally` because cancellation used to re-raise straight
            # past these three lines. The publish task was cancelled but the
            # driver still said `engaged`, so the next `_safe_stop` called
            # `release()` again, found a task that was already cancelled, and
            # raised out of the teardown path — while the caller went on
            # believing the arms had been handed back. Whatever else happens
            # here, this driver is no longer publishing, and must not claim to
            # be holding arms it has let go of.
            self._engaged = False
            self._releasing = False
            self._task = None

    # -- commanding -----------------------------------------------------

    def command(self, left: ArmAngles | None, right: ArmAngles | None) -> None:
        """Set the target pose. Non-blocking; the loop does the talking.

        `None` for a side means "that hand is not tracked" — that arm holds its
        current target rather than falling to neutral, so an operator whose
        hand briefly leaves the tracking volume does not get a dropped arm.
        """
        if left is not None:
            self._set_target(0, left.as_tuple())
        if right is not None:
            self._set_target(7, right.as_tuple())
        self._last_frame_at = time.monotonic()

    def _set_target(self, base: int, values: tuple[float, ...]) -> None:
        """Write a side's targets, holding any that jumped implausibly far.

        See MAX_TARGET_JUMP_RAD. A jump has to be asked for on
        JUMP_CONFIRM_FRAMES frames in a row before it lands, which costs a
        genuine fast movement 60 ms and costs a singularity flip everything.
        """
        for offset, value in enumerate(values):
            i = base + offset
            if abs(value - self._target[i]) <= MAX_TARGET_JUMP_RAD:
                self._target[i] = value
                self._jump_frames[i] = 0
                continue
            self._jump_frames[i] += 1
            if self._jump_frames[i] >= JUMP_CONFIRM_FRAMES:
                log.info(
                    "arm_sdk.large_jump_accepted",
                    joint=i,
                    from_rad=round(self._target[i], 3),
                    to_rad=round(value, 3),
                )
                self._target[i] = value
                self._jump_frames[i] = 0

    def hold(self) -> None:
        """Freeze the target where it is — the dead-man's arm behaviour."""
        self._target = list(self._current)
        self._last_frame_at = time.monotonic()

    def relax_toward_neutral(self) -> None:
        """Aim both arms at the resting pose, still rate-limited."""
        self._target = list(NEUTRAL.as_tuple()) * 2
        self._last_frame_at = time.monotonic()

    # -- the loop -------------------------------------------------------

    def _get_publisher(self) -> Any:
        if self._publisher is None:
            from unitree_sdk2py.core.channel import ChannelPublisher
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_

            self._publisher = ChannelPublisher(ARM_SDK_TOPIC, LowCmd_)
            self._publisher.Init()
            log.info("arm_sdk.publisher.ready", topic=ARM_SDK_TOPIC)
        return self._publisher

    def _slew(self) -> None:
        """Move `_current` toward `_target`, no faster than MAX_JOINT_RATE."""
        step = MAX_JOINT_RATE_RAD_S * CONTROL_PERIOD_S
        for i, target in enumerate(self._target):
            delta = target - self._current[i]
            self._current[i] += clamp(delta, -step, step)

    def _build_command(self) -> Any:
        from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
        from unitree_sdk2py.utils.crc import CRC

        cmd = unitree_hg_msg_dds__LowCmd_()
        cmd.mode_machine = self._mode_machine
        for i in range(14):
            slot = (LEFT_ARM_BASE + i) if i < 7 else (RIGHT_ARM_BASE + i - 7)
            motor = cmd.motor_cmd[slot]
            motor.mode = 1  # 1 = Enable. 0 is silently ignored — see the docstring.
            motor.q = float(self._current[i])
            motor.dq = 0.0
            motor.tau = 0.0
            motor.kp = KP
            motor.kd = KD
        cmd.motor_cmd[WEIGHT_SLOT].q = float(self._weight)
        cmd.crc = CRC().Crc(cmd)
        return cmd

    def _publish(self) -> None:
        self._get_publisher().Write(self._build_command())
        self._published += 1

    async def _run(self) -> None:
        """50 Hz: ramp the weight, slew the joints, publish, repeat."""
        weight_step = CONTROL_PERIOD_S / RAMP_S
        try:
            while True:
                if self._releasing:
                    self._weight = max(0.0, self._weight - weight_step)
                else:
                    self._weight = min(1.0, self._weight + weight_step)
                    # Frames stopped arriving: freeze rather than continuing to
                    # chase a stale target. This is the arm half of the
                    # dead-man; `server.py` owns the locomotion half.
                    if time.monotonic() - self._last_frame_at > FRAME_TIMEOUT_S:
                        self._target = list(self._current)

                self._slew()
                await asyncio.to_thread(self._publish)

                if self._releasing and self._weight <= 0.0:
                    # One final zero-weight frame has now gone out; the built-in
                    # controller has the arms back. Clearing the flags here
                    # rather than in `release()` is what makes
                    # `request_release()` complete on its own — the loop is the
                    # only thing that knows when the ramp actually finished.
                    self._engaged = False
                    self._releasing = False
                    log.info("arm_sdk.released", published=self._published)
                    return
                await asyncio.sleep(CONTROL_PERIOD_S)
        except asyncio.CancelledError:
            # Do NOT try to ramp down here: cancellation means the process is
            # going away or a stop is in flight, and a 2 s ramp is exactly the
            # wrong thing to insist on. Publish one zero-weight frame so the
            # blend collapses immediately, and let go.
            self._weight = 0.0
            try:
                self._publish()
            except Exception:
                log.exception("arm_sdk.zero_weight_on_cancel_failed")
            log.info("arm_sdk.cancelled", published=self._published)
            raise
        except Exception as exc:
            # A publish that keeps failing must not leave the weight up with
            # nobody driving. Collapse the blend, then let the task die.
            log.exception("arm_sdk.loop_failed")
            self._weight = 0.0
            try:
                self._publish()
            except Exception:
                pass
            self._engaged = False
            self._releasing = False
            self._failed = repr(exc)
            # Deliberately NOT re-raised. Nothing awaits this task on the
            # failure path, so raising only produces a "Task exception was
            # never retrieved" warning at garbage-collection time — which is
            # both easy to miss and detached from the moment it happened. The
            # log line above and the `_failed` latch carry it instead.
            return


# Skills that drive the arms through the arm action service, which is itself
# implemented on rt/arm_sdk. Kept here rather than inline so the contention
# check has one list to widen when a new gesture skill lands.
_GESTURE_SKILLS = frozenset({"dance", "wave", "shake_hand", "hug", "clap", "point_at", "gesture"})


_driver: ArmSdkDriver | None = None


def get_driver() -> ArmSdkDriver:
    """Process-wide driver. One publisher, one weight, one owner of the arms."""
    global _driver
    if _driver is None:
        _driver = ArmSdkDriver()
    return _driver
