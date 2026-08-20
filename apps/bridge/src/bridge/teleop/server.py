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
import time
from typing import Any

import structlog
from websockets.asyncio.server import ServerConnection, serve

from bridge.teleop import hands as hands_mod
from bridge.teleop.arm_sdk import ArmSdkUnavailable, get_driver
from bridge.teleop.protocol import FrameError, TeleopFrame, parse_frame
from bridge.skills.task_runtime import get_registry
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
YAW_DEADZONE_RAD = math.radians(8)
YAW_FULL_SCALE_RAD = math.radians(45)
YAW_MAX_RAD_S = 0.25
WALK_MAX_VEL = 0.2
STAND_HEIGHT = 0.78

# --- Dead-man --------------------------------------------------------------
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
        self.frame: TeleopFrame | None = None
        self.last_frame_at = 0.0
        self.frames_received = 0
        self.frames_rejected = 0
        self.last_seq = -1
        self.arm_length_m = DEFAULT_ARM_LENGTH_M
        self.calibrated = False
        self.motion_started_at: float | None = None
        self.deadman_tripped = False
        self.moving = False
        self.arm_error: str | None = None
        #: Whether the operator asked for the arms on the previous tick. Edge
        #: detection, so hand commands go out on transitions rather than at
        #: the dispatch rate.
        self.arms_active = False
        self.hands = hands_mod.build_driver()

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
        self.last_frame_at = time.time()
        self.frames_received += 1
        self._maybe_calibrate(frame)

    def _maybe_calibrate(self, frame: TeleopFrame) -> None:
        """Learn the operator's reach the first time they extend an arm."""
        if self.calibrated:
            return
        for side, hand in (("right", frame.right), ("left", frame.left)):
            if hand is None:
                continue
            measured = calibrate_arm_length(frame.head_position, frame.head_yaw, hand, side)  # type: ignore[arg-type]
            if measured >= CALIBRATION_MIN_FRACTION * DEFAULT_ARM_LENGTH_M:
                self.arm_length_m = measured
                self.calibrated = True
                log.info("teleop.calibrated", side=side, arm_length_m=round(measured, 3))
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

    def check_estop(self) -> bool:
        """Latch `stopped` if the session has been cancelled. Returns the latch.

        Clearing is deliberate and one-directional: only the operator releasing
        the dead-man clears it, and clearing also re-arms the underlying event
        so a second PARAR works exactly like the first.
        """
        if self.task.cancel_event.is_set():
            if not self.stopped:
                log.warning("teleop.estop", task_id=self.task.task_id)
            self.stopped = True
        if self.stopped and self.frame is not None and not self.frame.enabled:
            self.stopped = False
            self.task.cancel_event.clear()
            log.info("teleop.estop.cleared", task_id=self.task.task_id)
        return self.stopped

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
    # Before anything else: has the e-stop been pressed? `wants_motion` also
    # consults the latch, but this is what sets it, and it must run even on a
    # tick where no frame has arrived.
    session.check_estop()
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


async def _dispatch_arms(
    session: TeleopSession, frame: TeleopFrame | None, allowed: bool
) -> None:
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
        retarget_arm("right", frame.head_position, frame.head_yaw, frame.right, session.arm_length_m)
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
            now = time.time()
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

    try:
        # Shielded: this is the one command that must survive the cancellation
        # that brought us here. Without it, a client that disconnects mid-turn
        # leaves the robot rotating until the firmware's 1 s duration expires.
        await asyncio.shield(
            asyncio.ensure_future(send_velocity_async(0.0, 0.0, 0.0, STAND_HEIGHT))
        )
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
            await asyncio.shield(asyncio.ensure_future(driver.release()))
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
        deadline = time.time() + RECONNECT_GRACE_S
        while _active is not None and time.time() < deadline:
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
        _active = None
        log.info("teleop.session_ended", **session.status())


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

    async with serve(handle_client, TELEOP_HOST, TELEOP_PORT):
        log.info(
            "teleop.listening",
            host=TELEOP_HOST,
            port=TELEOP_PORT,
            sim_mode=os.environ.get("SIM_MODE", "stub"),
            arm=get_driver().status(),
        )
        await asyncio.Future()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
