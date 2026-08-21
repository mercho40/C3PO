"""The teleop session: one WebSocket, one operator, one commander of the robot.

Run beside the MCP server, as its own process:

    uv run python -m bridge.teleop.server

Binds loopback by default and has no authentication of its own — same posture
as the MCP HTTP transport: reach it over an SSH tunnel. Port 8767, chosen
around everything already spoken for on that Jetson — 8000 `gemm-ai.service`,
8001 this bridge's MCP, 8081 perception's vision MJPEG, 8765 the colleague's
foxglove bridge, 55555/60000 teleimager (`docs/ROBOT-HARDWARE.md`).

What a session does, per frame
------------------------------
1. **Head yaw -> rotation.** Yaw error past a deadzone becomes `vyaw`,
   dispatched through `skills._locomotion.send_velocity_async` so the
   hardware-vetted velocity clamp and the firmware's own `duration` deadman
   apply exactly as they do to `walk_to`. Turning your head turns the robot.
2. **Walk axis -> forward/back.** Rides in the same frame precisely so this
   process is the only writer of `SetVelocity` while a session is live.
3. **Wrist poses -> arms.** Retargeted (`retarget.py`) and handed to the
   `rt/arm_sdk` driver, which is disabled unless explicitly enabled.
4. **Grip -> fingers.** Handed to whichever hand driver is configured, which
   is `NullHandDriver` until someone settles which hands are fitted.

The e-stop reaches this, and that took doing
--------------------------------------------
`stop_everything` cancels every task in the `TaskRegistry`, sends a
zero-velocity burst and (on real) Damps. A teleop session is not a skill
invocation, so at first it appeared in none of that — and the burst was useless
against it, because this dispatch loop re-issues velocity 50 ms later. Pressing
PARAR while someone wore the headset produced a brief stutter and nothing else.

So a session now registers itself as a `teleop_session` task for its whole
lifetime. That buys three things at once: `stop_everything` cancels it like
anything else, `list_active_tasks` answers "a headset is driving the robot"
instead of "nothing is running", and the link watchdog can see it.

Recovery is deliberate. A cancelled session latches stopped and stays stopped
while the operator keeps holding the control — releasing the dead-man is what
clears it. Anything softer would let an e-stop be undone by not letting go,
which is exactly what a startled person does.

Three dead-men, and why there are three
---------------------------------------
* **Client-held** (`frame.enabled`): the operator is actively holding a
  control. Releasing it stops motion on the next frame.
* **Frame staleness** (`STALE_FRAME_S`): frames stopped arriving — Wi-Fi
  dropped, the headset slept, the tab was closed. The browser cannot tell us
  this; only the absence of frames can.
* **Session duration** (`MAX_CONTINUOUS_MOTION_S`): motion has been commanded
  without pause for too long. Catches the case the other two cannot — a wedged
  client that is still faithfully sending `enabled: true` while nobody is
  wearing the headset.

The first two are the ones that fire in normal use. The third exists because
the first two both trust the client, and a dead-man that trusts the thing it
is guarding against is not one.

Never live-tested against the robot.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import time
from typing import Any

import structlog
from websockets.asyncio.server import ServerConnection, serve

from bridge.estop import (
    last_stop_at,
    record_ack,
    release_teleop_lease,
    renew_teleop_lease,
    stop_is_standing,
)
from bridge.skills.task_runtime import get_registry
from bridge.teleop import hands as hands_mod
from bridge.teleop.arm_sdk import ArmSdkUnavailable, get_driver
from bridge.teleop.protocol import FrameError, TeleopFrame, parse_frame
from bridge.teleop.retarget import (
    DEFAULT_ARM_LENGTH_M,
    calibrate_arm_length,
    retarget_arm,
)

log = structlog.get_logger(__name__)

TELEOP_HOST = os.environ.get("TELEOP_HOST", "127.0.0.1")
TELEOP_PORT = int(os.environ.get("TELEOP_PORT", "8767"))

# --- Locomotion policy -------------------------------------------------------
# Deliberately the same numbers the /vr-control page shows the operator, so the
# on-screen deadzone arc and what the robot does cannot drift apart.
# Re-tuned 2026-08-20 against measurements from the first real session, where
# the operator's verdict was that turning worked but felt sluggish. It did, and
# the numbers say why: the robot under-travels its commanded yaw by about 2.2x
# (25 deg of head yaw for 2 s produced 5.3-5.8 deg of rotation), matching the
# ~2.35x under-travel `walk_to` measures in translation. The gains are fitted
# to a sim policy and have never been re-fitted to this body.
#
# Rather than invent a correction factor, the three numbers that shape the feel
# were each moved to the edge of what is already vetted:
#
#   deadzone   8 -> 6 deg   still well clear of neck wobble at rest
#   full scale 45 -> 30 deg full rate at a natural glance, not a shoulder-check
#   max rate   0.25 -> 0.30 rad/s, the hardware-vetted cap in walk_velocity
#
# At a 20 deg head turn that is 0.081 -> 0.175 rad/s, ~2.2x more command —
# which is exactly the measured shortfall, arrived at from the measurement
# rather than fitted to it.
#
# 0.30 is a ceiling, not a target: `_locomotion.send_velocity` re-clamps to
# walk_velocity's MAX_YAW_VEL on real hardware, so this cannot exceed what that
# path already allows however it is edited.
YAW_DEADZONE_RAD = math.radians(6)
YAW_FULL_SCALE_RAD = math.radians(30)
YAW_MAX_RAD_S = 0.30
WALK_MAX_VEL = 0.2
STAND_HEIGHT = 0.78

# --- Dead-man --------------------------------------------------------------
# Every deadline below that guards MOTION reads `time.monotonic`, never
# `time.time`. A wall clock is a statement about what time it is, and it can
# be corrected: NTP steps it, a VM resumes with a stale one, an operator fixes
# a wrong timezone mid-session. Any of those move it backwards, and a backwards
# jump makes `now - last_frame_at` negative — so a link that has been silent
# for a minute reads as fresh, `stale()` returns False, and the last commanded
# velocity keeps going out to a robot nobody is talking to. The same jump
# rescues the continuous-motion latch from tripping and stretches the e-stop
# release dwell to whatever the correction was.
#
# The robot onboard is the realistic case, not a hypothetical: it has no RTC,
# so it boots at the epoch and steps its clock by decades the moment it reaches
# an NTP server — which is often seconds after the bridge starts.
#
# Wall-clock time is still right for anything an operator will read (`ended_at`
# in a task record) and for the e-stop sentinel, whose timestamps are file
# mtimes compared against other processes' file mtimes. Those are timestamps.
# These are durations.
STALE_FRAME_S = 0.4
MAX_CONTINUOUS_MOTION_S = 8.0

# The dispatch loop's own rate. Independent of the frame rate: the headset may
# deliver 30 or 120 frames a second, and `SetVelocity` wants a steady re-issue
# well inside its 1 s firmware duration either way.
DISPATCH_HZ = 20
DISPATCH_PERIOD_S = 1.0 / DISPATCH_HZ

# Arm length is measured from the first frame in which both the operator's arm
# is near-extended and the pose is stable. Below this fraction of the fallback
# reach, the sample is a bent arm and tells us nothing about their span.
CALIBRATION_MIN_FRACTION = 0.85

# How many consecutive qualifying frames must agree before a reach is accepted,
# and how far apart they may be.
#
# Calibration used to latch on the FIRST frame that cleared the minimum, and
# hold it for the whole session. Quest hand tracking emits wild positions for a
# frame or two as a hand enters or leaves the tracking volume — the protocol
# rejects those beyond 1.5 m for exactly this reason, which means anything up
# to 1.5 m gets through and is measured as if it were an arm.
#
# One such artefact set the operator's reach to nearly double a real one, and
# `_elbow_angle` is `2 acos(reach / arm_length)`: with the denominator too
# large, every real hand position reads as barely extended, so the elbow stays
# almost straight for the rest of the session. Not an error, not a failure, no
# log line — just an arm that will not bend, permanently.
#
# Five frames at 30-50 Hz is about a tenth of a second of the operator holding
# still, which extending an arm involves anyway.
CALIBRATION_SAMPLES = 5
CALIBRATION_SPREAD_M = 0.06


def yaw_to_vyaw(yaw_error: float) -> float:
    """Head yaw error -> commanded yaw rate. Deadzone, then linear to a cap.

    The deadzone is not decoration: a worn headset is never still, and without
    it the robot would creep continuously in whichever direction the operator's
    neck happens to be relaxed.
    """
    magnitude = abs(yaw_error)
    if magnitude <= YAW_DEADZONE_RAD:
        return 0.0
    span = YAW_FULL_SCALE_RAD - YAW_DEADZONE_RAD
    scaled = min(1.0, (magnitude - YAW_DEADZONE_RAD) / span)
    # Left (positive WebXR yaw) is a positive body-frame yaw rate on the G1;
    # both are counterclockwise seen from above.
    return math.copysign(scaled * YAW_MAX_RAD_S, yaw_error)


class TeleopSession:
    """State for one connected operator. Owns the dispatch loop while alive."""

    def __init__(self) -> None:
        # Registered for the session's whole lifetime, not per command. This is
        # what puts the session inside `stop_everything`'s reach; see the
        # module docstring.
        self.task = get_registry().create("teleop_session")
        self.task.phase = "streaming"
        self.stopped = False
        #: Newest stop already reacted to. Seeded from disk so a session
        #: starting after an OLD stop does not immediately latch on it — what
        #: matters is a stop during *this* session.
        self._seen_stop_at = last_stop_at()
        #: A stop outlives the session that was running when it was pressed.
        #: Reconnecting is not a way to clear one — before this, a fresh
        #: session recorded the existing stop as "already seen" and started
        #: unlatched, so PARAR followed by a reconnect (which the operator
        #: does *reflexively* when the robot stops responding) resumed a
        #: halted robot with no acknowledgement from anyone.
        self._release_since: float | None = None
        if stop_is_standing():
            self.stopped = True
            log.warning("teleop.estop.inherited", stop_at=self._seen_stop_at)
        self.frame: TeleopFrame | None = None
        self.last_frame_at = 0.0
        self.frames_received = 0
        self.frames_rejected = 0
        self.last_seq = -1
        self.arm_length_m = DEFAULT_ARM_LENGTH_M
        #: Recent qualifying reach measurements, pending agreement.
        self._calibration_samples: list[float] = []
        self.calibrated = False
        self.motion_started_at: float | None = None
        self.deadman_tripped = False
        self.moving = False
        self.arm_error: str | None = None
        #: Whether the operator asked for the arms on the previous tick. Edge
        #: detection, so hand commands go out on transitions rather than at
        #: the dispatch rate.
        self.arms_active = False
        #: Whether a stop command has already gone out for this session.
        #: `_safe_stop` runs twice by design — once from the dispatch loop's
        #: cancellation path, once from the handler's `finally`, so a crashed
        #: loop still stops the robot. Sending twice is free; *waiting* twice
        #: is not, and it doubled how long the single-session slot stayed held.
        self.stop_issued = False
        self.hands = hands_mod.get_driver()

    # -- ingest ---------------------------------------------------------

    def ingest(self, frame: TeleopFrame) -> None:
        """Accept a parsed frame, dropping ones that arrive out of order.

        Out-of-order delivery cannot happen on a single WebSocket, which is
        ordered — but a reconnecting client that restarts its counter can
        produce the same symptom, and acting on a frame older than the one
        already applied means commanding a pose the operator has left.
        """
        if frame.seq <= self.last_seq:
            if frame.seq == 0:
                # A fresh client counting from zero. Accept and resynchronise.
                self.last_seq = -1
            else:
                self.frames_rejected += 1
                return
        self.last_seq = frame.seq
        self.frame = frame
        self.last_frame_at = time.monotonic()
        self.frames_received += 1
        self._maybe_calibrate(frame)

    def _maybe_calibrate(self, frame: TeleopFrame) -> None:
        """Learn the operator's reach once they hold an arm extended.

        Deliberately not "the first frame that looks like a long arm" — see
        CALIBRATION_SAMPLES. A tracking artefact is one frame; an operator
        extending an arm is many, and they agree with each other.
        """
        if self.calibrated:
            return
        for side, hand in (("right", frame.right), ("left", frame.left)):
            if hand is None:
                continue
            measured = calibrate_arm_length(frame.head_position, frame.head_yaw, hand, side)  # type: ignore[arg-type]
            if measured < CALIBRATION_MIN_FRACTION * DEFAULT_ARM_LENGTH_M:
                # Not extended. Not evidence of anything, so it does not break
                # a run in progress either — the operator is simply between
                # reaches.
                continue

            samples = self._calibration_samples
            samples.append(measured)
            if len(samples) > CALIBRATION_SAMPLES:
                samples.pop(0)
            if len(samples) < CALIBRATION_SAMPLES:
                return

            if max(samples) - min(samples) > CALIBRATION_SPREAD_M:
                # The samples disagree by more than an arm changes length.
                # Something in there is a tracking artefact; drop the oldest
                # and keep looking rather than averaging it in.
                samples.pop(0)
                return

            self.arm_length_m = sorted(samples)[len(samples) // 2]
            self.calibrated = True
            log.info(
                "teleop.calibrated",
                side=side,
                arm_length_m=round(self.arm_length_m, 3),
                spread_m=round(max(samples) - min(samples), 3),
            )
            return

    # -- dead-man -------------------------------------------------------

    def close(self) -> None:
        """Mark the session finished so it leaves the registry.

        A session that never ends would make every later `stop_everything`
        report a cancelled task nobody is running, and would keep the link
        watchdog believing motion is in flight.
        """
        if self.task.status == "running":
            self.task.status = "completed"
            self.task.phase = "ended"
            self.task.ended_at = time.time()

    def check_estop(self, now: float | None = None) -> bool:
        """Latch `stopped` if a stop has been raised. Returns the latch.

        Two independent sources, covering different failures. The cancel event
        catches a stop raised inside THIS process. The sentinel catches one
        raised in the bridge's — which is where the console's PARAR button
        actually lands, and which the registry cannot reach.

        **Clearing is the part that matters.** It used to be a single
        condition: latched, and the last frame we hold has `enabled` false.
        Three ways that let a stopped robot start moving again with nobody
        deciding it should.

        The last frame we hold is not a live statement of operator intent. It
        is whatever arrived most recently, and it stays there forever once
        frames stop — so a stop pressed after the link died cleared itself on
        the next tick against a frame from before the stop existed. The link
        being down is precisely when a stop must hold.

        A dead-man going false is not an acknowledgement either. It is the
        single most likely thing to happen in the second after an emergency
        stop: the operator lets go. So PARAR -> flinch -> re-grip resumed the
        robot, and the flinch was doing the clearing.

        And `enabled` false is the *resting* state of the controls. Any frame
        arriving while the operator is not actively commanding — every frame
        during a pause — cleared the latch, so a stop pressed while the robot
        was idle was gone before the operator looked up.

        So clearing now needs a live link and a deliberate act: fresh frames,
        the dead-man held released continuously for `ESTOP_RELEASE_DWELL_S`,
        and no new stop during that window. Releasing is still the gesture —
        there is no new control to find in a headset — but it has to be a held
        release rather than an instant, and the link has to be alive to see it.

        Clearing writes an acknowledgement that outlives this process, so the
        next session does not inherit a stop the operator already cleared.
        """
        now = time.monotonic() if now is None else now

        stop_at = last_stop_at()
        if stop_at > self._seen_stop_at:
            self._seen_stop_at = stop_at
            if not self.stopped:
                log.warning("teleop.estop", source="sentinel", task_id=self.task.task_id)
            self.stopped = True
            self._release_since = None  # a new stop restarts the dwell
        if self.task.cancel_event.is_set():
            if not self.stopped:
                log.warning("teleop.estop", source="registry", task_id=self.task.task_id)
                self._release_since = None
            self.stopped = True

        if not self.stopped:
            self._release_since = None
            return False

        released = self.frame is not None and not self.frame.enabled and not self.stale(now)
        if not released:
            self._release_since = None
            return True
        if self._release_since is None:
            self._release_since = now
            return True
        if now - self._release_since < ESTOP_RELEASE_DWELL_S:
            return True

        self.stopped = False
        self._release_since = None
        self.task.cancel_event.clear()
        record_ack()
        log.info("teleop.estop.cleared", task_id=self.task.task_id)
        return False

    def stale(self, now: float) -> bool:
        return self.last_frame_at == 0.0 or (now - self.last_frame_at) > STALE_FRAME_S

    def wants_motion(self, now: float) -> bool:
        """Whether this frame should be allowed to command any motion at all."""
        if self.stopped or self.deadman_tripped or self.stale(now):
            return False
        return self.frame is not None and self.frame.enabled

    def update_hold_latch(self, now: float, commanding: bool) -> None:
        """Trip the duration latch, and re-arm it once the operator lets go.

        Re-arming on release rather than on a timer is what makes this usable:
        the operator who genuinely wants to keep turning simply releases and
        presses again, while a wedged client that never releases stays
        latched.
        """
        if not commanding:
            self.motion_started_at = None
            if self.frame is not None and not self.frame.enabled:
                self.deadman_tripped = False
            return
        if self.motion_started_at is None:
            self.motion_started_at = now
        elif now - self.motion_started_at > MAX_CONTINUOUS_MOTION_S:
            if not self.deadman_tripped:
                log.warning("teleop.deadman.tripped", held_s=round(now - self.motion_started_at, 1))
            self.deadman_tripped = True

    def status(self) -> dict[str, Any]:
        return {
            "frames_received": self.frames_received,
            "frames_rejected": self.frames_rejected,
            "calibrated": self.calibrated,
            "arm_length_m": round(self.arm_length_m, 3),
            "deadman_tripped": self.deadman_tripped,
            "stopped_by_estop": self.stopped,
            "task_id": self.task.task_id,
            "moving": self.moving,
            "hands": self.hands.name,
            "arm": get_driver().status(),
            "arm_error": self.arm_error,
        }


async def _dispatch_once(session: TeleopSession, now: float) -> None:
    """One control tick: locomotion, then arms, then fingers."""
    from bridge.skills._locomotion import send_velocity_async

    frame = session.frame
    # Announce that a headset is driving, so the MCP process's locomotion
    # skills hold off rather than interleaving velocity commands with ours.
    # Renewed every tick: it is a lease, not a lock, so a teleop server that
    # dies mid-session frees locomotion in about a second instead of leaving
    # a robot nobody can drive.
    renew_teleop_lease()
    # Before anything else: has the e-stop been pressed? `wants_motion` also
    # consults the latch, but this is what sets it, and it must run even on a
    # tick where no frame has arrived.
    session.check_estop(now)
    allowed = session.wants_motion(now)

    vx = 0.0
    vyaw = 0.0
    if allowed and frame is not None:
        vx = frame.walk * WALK_MAX_VEL
        vyaw = yaw_to_vyaw(frame.head_yaw)

    commanding = vx != 0.0 or vyaw != 0.0
    session.update_hold_latch(now, commanding)
    if session.deadman_tripped:
        vx = vyaw = 0.0
        commanding = False

    # Only send when moving, or on the single tick that stops. Re-issuing zero
    # velocity at 20 Hz forever would keep the firmware's `duration` deadman
    # permanently refreshed, which is the one thing it exists to prevent.
    if commanding or session.moving:
        await send_velocity_async(vx, 0.0, vyaw, STAND_HEIGHT)
    session.moving = commanding

    await _dispatch_arms(session, frame, allowed)


async def _dispatch_arms(session: TeleopSession, frame: TeleopFrame | None, allowed: bool) -> None:
    driver = get_driver()

    wants_arms = allowed and frame is not None and frame.arms
    if not wants_arms:
        if driver.engaged:
            # Non-blocking: the ramp takes 2 s and the publish loop runs it on
            # its own. Awaiting here would stall this loop for the whole ramp,
            # so an operator who lowers their arms mid-turn would find the
            # robot stops turning too.
            driver.request_release()
        if session.arms_active:
            # Once, on the falling edge. Relaxing every tick would publish hand
            # commands at the dispatch rate for as long as the session lasts,
            # to a hand that is already open.
            session.arms_active = False
            session.hands.relax()
            # Letting go is also what clears a failed publish loop, so asking
            # again is a retry rather than a permanent refusal.
            driver.clear_failure()
        return

    session.arms_active = True

    assert frame is not None  # `wants_arms` established it
    if not driver.engaged:
        try:
            await driver.engage()
            session.arm_error = None
        except ArmSdkUnavailable as exc:
            # Expected whenever the arm path is not enabled, which is the
            # default. Reported to the client so the UI can say why, logged
            # once per change rather than at 20 Hz.
            if session.arm_error != str(exc):
                log.info("teleop.arm.unavailable", reason=str(exc))
                session.arm_error = str(exc)
            return

    left = (
        retarget_arm("left", frame.head_position, frame.head_yaw, frame.left, session.arm_length_m)
        if frame.left is not None
        else None
    )
    right = (
        retarget_arm(
            "right", frame.head_position, frame.head_yaw, frame.right, session.arm_length_m
        )
        if frame.right is not None
        else None
    )
    driver.command(left, right)

    if frame.left is not None:
        session.hands.send("left", frame.left.grip)
    if frame.right is not None:
        session.hands.send("right", frame.right.grip)


async def _dispatch_loop(session: TeleopSession, ws: ServerConnection) -> None:
    """Run at a steady rate for the life of the connection."""
    last_status = 0.0
    try:
        while True:
            now = time.monotonic()
            await _dispatch_once(session, now)
            # Status back to the client ~2 Hz: enough for the UI to show the
            # dead-man and why the arms are not engaged, cheap enough to
            # ignore. Failure to send is not fatal to the control path.
            if now - last_status > 0.5:
                last_status = now
                try:
                    await ws.send(json.dumps({"type": "status", **session.status()}))
                except Exception:
                    pass
            await asyncio.sleep(DISPATCH_PERIOD_S)
    except asyncio.CancelledError:
        await _safe_stop(session)
        raise
    except Exception:
        # A control loop that cannot control has to hand back, loudly. Only
        # CancelledError used to be caught, so anything else — a DDS publisher
        # that goes away, a hand driver that raises, an unexpected failure out
        # of engage() — killed this task while the WebSocket kept ingesting
        # frames. The session looked alive from the page, status stopped
        # updating, and nothing dispatched or stopped until the operator
        # happened to disconnect.
        #
        # Ending beats retrying. Retrying at 20 Hz against a persistent fault
        # spins the log and leaves the operator holding a control that quietly
        # does nothing; closing the socket surfaces it as a lost connection,
        # which the page already knows how to say.
        log.exception("teleop.dispatch_failed")
        await _safe_stop(session)
        try:
            await ws.close(code=1011, reason="teleop dispatch failed")
        except Exception:
            pass


async def _safe_stop(session: TeleopSession) -> None:
    """Bring everything to rest. Runs on every exit path, including cancellation."""
    from bridge.skills._locomotion import send_velocity_async

    # Shielded: this is the one command that must survive the cancellation that
    # brought us here. Without it, a client that disconnects mid-turn leaves the
    # robot rotating until the firmware's 1 s duration expires. Bounded: see
    # STOP_ACK_BUDGET_S — the send continues in the background either way, we
    # simply stop *waiting* on an ack that cannot make anything safer.
    stop = asyncio.ensure_future(send_velocity_async(0.0, 0.0, 0.0, STAND_HEIGHT))
    # Consume whatever it ends with. On the unawaited path below nothing ever
    # retrieves the result, and asyncio reports that at garbage-collection time
    # as "Future exception was never retrieved" — a traceback with no context,
    # printed long after the fact, on a teardown that worked.
    stop.add_done_callback(lambda fut: fut.cancelled() or fut.exception())
    if session.stop_issued:
        # A stop already went out moments ago. Send another — cheap, and covers
        # the case where the first never left — but do not wait on it again.
        log.debug("teleop.stop_repeat_unawaited")
    else:
        session.stop_issued = True
        try:
            await asyncio.wait_for(asyncio.shield(stop), timeout=STOP_ACK_BUDGET_S)
        except asyncio.TimeoutError:
            log.warning("teleop.stop_velocity_unacked", budget_s=STOP_ACK_BUDGET_S)
        except (Exception, asyncio.CancelledError):
            log.warning("teleop.stop_velocity_failed", exc_info=True)
    session.moving = False
    try:
        session.hands.relax()
    except Exception:
        log.warning("teleop.hands_relax_failed", exc_info=True)
    driver = get_driver()
    if driver.engaged:
        try:
            # Bounded, and shorter than RECONNECT_GRACE_S on purpose. A full
            # `release()` waits RAMP_S + 2 s, which is longer than the grace
            # period the next operator gets — so tearing down a session with
            # the arms engaged held the single-session slot past the window and
            # the reconnecting client was refused by its own previous session.
            # The ramp continues in the background either way; the weight is
            # already falling, and the firmware's own arm_sdk timeout is behind
            # it. Waiting out the rest here makes nothing safer.
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(driver.release())),
                timeout=ARM_RELEASE_BUDGET_S,
            )
        except asyncio.TimeoutError:
            log.warning("teleop.arm_release_slow", budget_s=ARM_RELEASE_BUDGET_S)
        except (Exception, asyncio.CancelledError):
            log.warning("teleop.arm_release_failed", exc_info=True)


# How long a connecting operator waits for a departing one's teardown before
# being refused. Teardown is not instant on real hardware: `_safe_stop` sends a
# zero-velocity command, and that is a DDS RPC that waits for an ack. Holding
# the single-session slot across that window meant a client which dropped and
# immediately reconnected — a Wi-Fi blip, mid-session, with the headset on —
# was told "a session is already active" by its own previous self.
#
# Found on the robot 2026-08-20 by teleop_smoke_test.py, whose stage 2
# reconnects milliseconds after stage 1 closes. The unit tests missed it
# because in stub mode teardown is instantaneous.
#
# Deliberately short. It is a grace period for a reconnect, not a queue: two
# people really trying to drive at once should still be refused, promptly.
RECONNECT_GRACE_S = 3.0

# How long teardown waits for the arm weight ramp before moving on. Must stay
# below RECONNECT_GRACE_S: a session torn down with the arms engaged otherwise
# holds the single-session slot for longer than the next operator is willing to
# wait, and the reconnecting client is refused by its own previous self.
ARM_RELEASE_BUDGET_S = 2.5

# How long teardown will wait for the robot to acknowledge its stop command.
#
# `send_velocity_async` is a DDS RPC with SPORT_TIMEOUT_S (10 s) of headroom.
# That is right for a command whose result matters; it is wrong here. Teardown
# is best-effort by definition, and underneath it the firmware's own
# `duration` deadman stops the robot within a second of the last setpoint
# whatever we hear back. Waiting nine more seconds for an ack makes the robot
# no safer and holds the single-session slot the whole time.
#
# Found on the robot 2026-08-20: with no motion controller loaded the RPC never
# answers, teardown took the full timeout, and a client reconnecting was
# refused by its own previous session — past even the grace period above.
# The stop still goes out; it is shielded, so it completes in the background.
STOP_ACK_BUDGET_S = 2.0

#: How long the operator must hold the dead-man RELEASED, on a live link,
#: before a latched stop clears. See `check_estop` for why this is not zero.
ESTOP_RELEASE_DWELL_S = 1.0

_active: TeleopSession | None = None


async def handle_client(ws: ServerConnection) -> None:
    """One operator at a time. A second connection is refused, not queued.

    Two headsets driving one robot is not a mode this system has; letting the
    second connect and silently interleave setpoints with the first is the
    worst available answer.
    """
    global _active
    if _active is not None:
        # Wait out a departing session's teardown before refusing — see
        # RECONNECT_GRACE_S. Polling rather than an Event because the thing
        # being waited on is a module-level slot cleared in a `finally`, and a
        # missed set would strand the next operator for the whole grace period.
        deadline = time.monotonic() + RECONNECT_GRACE_S
        while _active is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if _active is not None:
            log.warning("teleop.session_refused", remote=ws.remote_address)
            await ws.close(code=1013, reason="a teleop session is already active")
            return
        log.info("teleop.session_slot_freed_in_time", remote=ws.remote_address)

    session = TeleopSession()
    _active = session
    log.info("teleop.session_started", remote=ws.remote_address, hands=session.hands.name)
    loop = asyncio.create_task(_dispatch_loop(session, ws))
    try:
        async for message in ws:
            try:
                session.ingest(parse_frame(message))
            except FrameError as exc:
                session.frames_rejected += 1
                # Rate-limited by only logging the first few: a client with a
                # systematic bug would otherwise emit 50 log lines a second.
                if session.frames_rejected <= 5:
                    log.warning("teleop.bad_frame", reason=str(exc))
    finally:
        loop.cancel()
        try:
            await loop
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("teleop.dispatch_loop_failed")
        # The loop's own cancellation path calls `_safe_stop`, but that path
        # only runs if the loop was still alive. If it had already crashed,
        # nothing has stopped the robot — so do it here too. `_safe_stop` is
        # idempotent by construction: a second zero-velocity command and a
        # release on a disengaged driver are both no-ops.
        await _safe_stop(session)
        session.close()
        # Drop it now rather than waiting out the TTL: the operator who just
        # disconnected should not have to wait two seconds to use the console.
        release_teleop_lease()
        _active = None
        log.info("teleop.session_ended", **session.status())


#: How long shutdown waits for the robot to be brought to rest before giving up
#: and exiting anyway. Generous compared with STOP_ACK_BUDGET_S because this
#: path also ramps the arm_sdk weight down, which is a timed ramp rather than a
#: single command — cutting it short is what drops the arms.
#:
#: This number is not free to choose. It has to fit inside the window
#: `scripts/robot/stop_teleop` allows before it escalates to SIGKILL, or the
#: shutdown this budget exists to permit gets killed halfway through — arms
#: still blended, weight part-way down, which is worse than either end state.
#: The script waits 8 s; keep this comfortably under that, and keep it at least
#: as large as STOP_ACK_BUDGET_S + ARM_RELEASE_BUDGET_S (2.0 + 2.5) or the
#: outer timeout fires before the inner ones can.
SHUTDOWN_BUDGET_S = 5.0


async def _shutdown(reason: str) -> None:
    """Bring the robot to rest on the way out. Best-effort, bounded.

    Without this, stopping the teleop server left the robot exactly as the last
    frame set it: `arm_sdk` weight still at 1.0 holding whatever pose the
    operator's arms were in, and the hands still gripping. Nothing downstream
    corrects that — `arm_sdk` has a frame timeout that ramps the weight down,
    but the hands have no timeout at all, so a closed hand stays closed until
    something tells it otherwise.

    That is the normal way this process dies. `systemctl restart`, a redeploy,
    Ctrl-C in the terminal it was started from, the supervisor cycling it — all
    SIGTERM, and all previously left a humanoid holding a pose with its fists
    shut and nobody connected to it.

    Bounded because a shutdown that hangs is worse than one that gives up: the
    supervisor escalates to SIGKILL, and then nothing runs at all.
    """
    log.warning("teleop.shutdown", reason=reason)
    session = _active
    try:
        if session is not None:
            await asyncio.wait_for(_safe_stop(session), timeout=SHUTDOWN_BUDGET_S)
            return

        # No session, but the arms and hands can still be engaged: a session
        # that ended uncleanly, or a driver engaged by something else in this
        # process. Do the same work without one.
        from bridge.skills._locomotion import send_velocity_async

        async def _rest() -> None:
            try:
                await send_velocity_async(0.0, 0.0, 0.0, STAND_HEIGHT)
            except Exception:
                log.warning("teleop.shutdown_stop_failed", exc_info=True)
            driver = get_driver()
            if driver.engaged:
                await driver.release()

        await asyncio.wait_for(_rest(), timeout=SHUTDOWN_BUDGET_S)
    except asyncio.TimeoutError:
        log.warning("teleop.shutdown_incomplete", budget_s=SHUTDOWN_BUDGET_S)
    except Exception:
        log.warning("teleop.shutdown_failed", exc_info=True)


async def _main() -> None:
    from bridge.sdk.connection import init_dds

    robot_host = os.environ.get("ROBOT_HOST", "127.0.0.1")
    init_dds(
        robot_host=robot_host,
        domain_id=int(os.environ.get("DDS_DOMAIN_ID", "0")),
        interface=os.environ.get("DDS_INTERFACE") or None,
    )
    from bridge.sdk.state import get_sampler

    get_sampler().start()

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except NotImplementedError:  # pragma: no cover - not POSIX
            # Windows has no add_signal_handler. Nothing here runs there, but
            # failing to start is a worse answer than running without it.
            log.warning("teleop.signal_handler_unavailable", signal=sig.name)

    async with serve(handle_client, TELEOP_HOST, TELEOP_PORT):
        log.info(
            "teleop.listening",
            host=TELEOP_HOST,
            port=TELEOP_PORT,
            sim_mode=os.environ.get("SIM_MODE", "stub"),
            arm=get_driver().status(),
        )
        # Waiting on an Event rather than `asyncio.Future()`: the signal
        # handler has to be able to reach the shutdown below. A bare Future
        # only ever ends by KeyboardInterrupt unwinding the stack, which does
        # not run on SIGTERM at all.
        await stopping.wait()

    await _shutdown("signal")


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
