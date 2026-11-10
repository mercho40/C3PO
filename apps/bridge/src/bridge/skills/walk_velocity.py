"""walk_velocity: open-loop body-frame velocity command for real G1 hardware.

Unlike walk_to/turn (blocked on real hardware — no world-frame pose source
wired yet, see README "Phase 1b"), this doesn't need pose at all. It's the
same pattern xr_teleoperate's own controller-button locomotion uses: a
body-frame (vx, vy, vyaw) held for `duration_s` by re-issuing short setpoints
(confirmed via `unitree_sdk2py`'s LocoClient.Move()/SetVelocity — repeated
calls are how continuous motion is built up, not a single unbounded one).

Re-issued, not sent once with `duration=duration_s`: one 3 s setpoint was a 3 s
deadman, so a bridge that died mid-call left the robot walking for up to three
seconds. Every setpoint now goes through `_locomotion.send_velocity` — the 1 s
firmware duration and the safety governor (bridge/safety.py) — like every other
velocity on this robot.

Deliberately conservative: no pose feedback means no way to detect "did it
actually go where I meant" or "did it hit something" — so both velocity
magnitude and duration are hard-capped here, independent of whatever the
caller asks for. An LLM agent driving this blind should only ever move in
small, correctable increments.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from bridge import safety
from bridge.skills._locomotion import (
    DEFAULT_HEIGHT,
    TELEOP_TAKEOVER,
    arm_hold_conflict,
    fsm_conflict,
    send_velocity_async,
    teleop_conflict,
)
from bridge.skills.task_runtime import get_registry
from bridge.skills.walk_to import WALK_SKILLS

log = structlog.get_logger(__name__)

# Matches xr_teleoperate's own default safety cap on its controller-button
# locomotion scheme (0.3, see xr_teleoperate issue #135) — not a sim-tuned
# number, an actual precedent for "safe on this real hardware."
#
# NOTE: walk_to/turn (`_locomotion.py`) use a *different*, 3-5x higher cap
# set (MAX_FWD_VEL=1.0, MAX_YAW_VEL=1.57) tuned against Isaac Sim. That's
# harmless today because walk_to/turn publish to a sim-only channel that's
# unsubscribed on real hardware. Once real-mode pose is wired for walk_to/
# turn (README "Phase 1b"), reconcile these two cap sets before they can
# both fire against real hardware — see `_locomotion.py`'s matching note.
MAX_LINEAR_VEL = 0.3  # m/s, vx/vy
MAX_YAW_VEL = 0.3  # rad/s

# Single-call duration ceiling. No pose feedback exists to know when to stop
# early, so keep each blind command's "blast radius" small — an agent that
# wants sustained motion should call this repeatedly, checking get_state()
# and its own judgement between calls, not issue one long blind command.
MAX_DURATION_S = 3.0

# How often the setpoint is re-issued. Well inside the firmware's 1 s duration,
# and the same tick the loop already used to watch for cancellation.
REISSUE_PERIOD_S = 0.05

# How long to wait for a preempted walk to end before driving anyway. walk_to and
# turn end on a 0.4 s zero burst, navigate_to's monitor within NAV_POLL_S (0.25 s).
PREEMPT_WAIT_S = 1.0


async def run(
    vx: float,
    vy: float,
    vyaw: float,
    duration_s: float = 1.0,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Command a body-frame velocity for `duration_s`, clamped to safety limits.

    Returns the full task dict. Cancellable: `cancel_task`/`stop_everything`
    can interrupt mid-wait, in which case a zero-velocity SetVelocity is sent
    immediately. A walk that runs its course ends on a zero setpoint too, so
    the robot stops at `duration_s` rather than coasting on the last one.
    """
    task = get_registry().create("walk_velocity")

    requested = {"vx": vx, "vy": vy, "vyaw": vyaw, "duration_s": duration_s}
    # The governor's own static caps, so `clamped` is the vector the legs get, not a per-axis
    # clamp the governor then shrinks. Its person rule and ramp still act per setpoint.
    clamped_vx, clamped_vy, clamped_vyaw = safety.cap(
        vx,
        vy,
        vyaw,
        min(MAX_LINEAR_VEL, safety.MAX_LINEAR_MPS),
        min(MAX_YAW_VEL, safety.MAX_YAW_RPS),
    )
    clamped_duration = max(0.0, min(duration_s, MAX_DURATION_S))
    clamped = {
        "vx": clamped_vx,
        "vy": clamped_vy,
        "vyaw": clamped_vyaw,
        "duration_s": clamped_duration,
    }

    log.info("walk_velocity.start", task_id=task.task_id, requested=requested, clamped=clamped)

    try:
        conflict = teleop_conflict()
        if conflict is not None:
            task.status = "failed"
            task.phase = "teleop_active"
            task.error = conflict
            task.ended_at = time.time()
            log.warning("walk_velocity.teleop_active", task_id=task.task_id)
            return task.to_dict()
        # A zero call is a stop (the headset's stop pulses), and neither check below may refuse it.
        moving = any((clamped_vx, clamped_vy, clamped_vyaw))
        conflict = arm_hold_conflict() if moving else None
        if conflict is not None:
            task.status = "failed"
            task.phase = "arm_held"
            task.error = conflict
            task.ended_at = time.time()
            log.warning("walk_velocity.arm_held", task_id=task.task_id)
            return task.to_dict()
        conflict = fsm_conflict() if moving else None
        if conflict is not None:
            task.status = "failed"
            task.phase = "fsm_not_walkable"
            task.error = conflict
            task.ended_at = time.time()
            log.warning("walk_velocity.fsm_not_walkable", task_id=task.task_id)
            return task.to_dict()

        # Decision 11.6b: deliberate operator input preempts an agent walk. Never another
        # walk_velocity: the headset renews its walk with overlapping 1 s calls.
        registry = get_registry()
        preempted = [
            t
            for t in registry.list_active()
            if t.skill_name in WALK_SKILLS and t.skill_name != "walk_velocity"
        ]
        for other in preempted:
            registry.cancel(other.task_id)
        if preempted:
            log.warning(
                "walk_velocity.preempted",
                task_id=task.task_id,
                preempted=[t.task_id for t in preempted],
            )
        # Let their stop bursts finish first, so ours is the only commander on the base.
        wait_until = time.monotonic() + PREEMPT_WAIT_S
        while any(t.status == "running" for t in preempted) and time.monotonic() < wait_until:
            await asyncio.sleep(REISSUE_PERIOD_S)

        setpoint = (clamped_vx, clamped_vy, clamped_vyaw, DEFAULT_HEIGHT)
        code = await send_velocity_async(*setpoint)
        data = None
        if code != 0:
            task.status = "failed"
            task.phase = "rpc_error"
            task.error = f"rpc_error_code_{code}"
            task.ended_at = time.time()
            task.result = {
                "requested": requested,
                "clamped": clamped,
                "rpc_code": code,
                "rpc_data": data,
            }
            return task.to_dict()

        task.phase = "moving"
        # Monotonic: an NTP step mid-walk must not stretch or cut the walk.
        deadline = time.monotonic() + clamped_duration
        cancelled = False
        taken_over = False
        while (remaining := deadline - time.monotonic()) > 0:
            if task.cancel_event.is_set():
                cancelled = True
                break
            # The headset took over mid-walk: the person in the room wins, as in walk_to.
            if teleop_conflict() is not None:
                taken_over = True
                break
            task.progress = max(0.0, min(0.999, 1.0 - remaining / max(clamped_duration, 1e-6)))
            await asyncio.sleep(min(REISSUE_PERIOD_S, remaining))
            if deadline - time.monotonic() > 0 and not task.cancel_event.is_set():
                await send_velocity_async(*setpoint)

        # Zero now rather than letting the last setpoint run out its 1 s.
        await send_velocity_async(0.0, 0.0, 0.0, DEFAULT_HEIGHT)
        if cancelled:
            task.status = "cancelled"
            task.phase = "cancelled"
        elif taken_over:
            task.status = "failed"
            task.phase = "teleop_takeover"
            task.error = TELEOP_TAKEOVER
            log.warning("walk_velocity.teleop_takeover", task_id=task.task_id)
        else:
            task.status = "completed"
            task.phase = "duration_elapsed"
            task.progress = 1.0

        task.ended_at = time.time()
        task.result = {
            "requested": requested,
            "clamped": clamped,
            "rpc_code": code,
            "rpc_data": data,
        }
        log.info(
            "walk_velocity.done",
            task_id=task.task_id,
            status=task.status,
            duration_s=round(task.ended_at - task.started_at, 2),
        )
        return task.to_dict()

    except asyncio.CancelledError:
        # CancelledError is a BaseException, so the `except Exception` below
        # does NOT catch it. Without this branch an externally-cancelled walk
        # (HTTP client disconnect, FastMCP timeout) leaves the task pinned at
        # status="running": TaskRegistry._gc_locked only reaps non-running
        # tasks, so it is never collected and anything reading "is the robot
        # busy" off the registry sees motion that ended long ago.
        task.status = "cancelled"
        task.phase = "cancelled"
        task.ended_at = time.time()
        log.info("walk_velocity.cancelled", task_id=task.task_id)
        # Best-effort immediate stop. `shield` so the stop still reaches the
        # robot even though we are being cancelled -- awaiting it re-raises
        # CancelledError at once, but the shielded thread call survives that
        # and completes on its own. Safe for this to fail either way: the
        # firmware's own `duration` deadman already guarantees a halt within
        # VELOCITY_DURATION_S (1 s); this only makes it stop sooner.
        try:
            await asyncio.shield(send_velocity_async(0.0, 0.0, 0.0, DEFAULT_HEIGHT))
        except (Exception, asyncio.CancelledError):
            # Not dangerous — the firmware deadman above still halts this — but
            # not nothing either. Swallowed silently, a stop path that fails
            # every single time is indistinguishable from one that works.
            # `teleop/_safe_stop` logs each of its failure branches for exactly
            # this reason; these two were the odd ones out.
            log.warning("walk_velocity.cancel_stop_failed", task_id=task.task_id, exc_info=True)
        raise  # never swallow cancellation -- the caller is entitled to it

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("walk_velocity.failed", task_id=task.task_id)
        try:
            await send_velocity_async(0.0, 0.0, 0.0, DEFAULT_HEIGHT)
        except Exception:
            log.warning("walk_velocity.failure_stop_failed", task_id=task.task_id, exc_info=True)
        return task.to_dict()
