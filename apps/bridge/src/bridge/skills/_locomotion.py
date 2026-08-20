"""Shared velocity helpers for locomotion skills, dispatched by `SIM_MODE`.

The two targets take velocity through completely different channels, so this
module is the single seam that hides the difference — `walk_to` and `turn`
call `send_velocity` and never learn which one they're driving.

- **sim** (`isaac`, `mujoco_local`): publishes a Python-list-as-string
  `"[x_vel, y_vel, yaw_vel, height]"` to `rt/run_command/cmd`. This is an
  Isaac Sim convenience channel; real G1 firmware does **not** subscribe to
  it, which is why publishing there on real hardware is a silent no-op.
- **real**: a `SET_VELOCITY` RPC (api_id 7105) on `rt/api/sport/request`
  carrying `{"velocity": [vx, vy, omega], "duration": d}`. `height` has no
  place in that call — stand height is a separate api_id (7104) — so it is
  accepted and ignored here.

Velocity caps and gains come from `unitree_sim_isaaclab/send_commands_keyboard.py`
and are fitted to the *sim* walk policy, which runs at ~10-15% of commanded
velocity. **They will not transfer to real hardware** and need measuring on
the robot before `walk_to` can close a distance accurately there. The caps
are still applied on real as a safety clamp, which is the conservative
direction to be wrong in.

`_` prefix on the module: skills inside `bridge.skills` import freely; this
isn't part of the bridge's public API.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

from bridge.skills.task_runtime import Task

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

CMD_TOPIC = "rt/run_command/cmd"

# How long the firmware honours one SET_VELOCITY setpoint before stopping by
# itself. We re-issue every LOOP_PERIOD_S (20 ms), so 1.0 s is ~50x headroom
# against a slow loop while still braking the robot within a second if this
# process dies mid-stride.
#
# This matters more than it looks: it is a deadman *below* our software. The
# alternative the vendor client offers is `Move()`'s continuous mode with
# duration=864000 (ten days), where a crashed bridge leaves the robot walking
# with its last setpoint. Do not raise this to "reduce chatter".
VELOCITY_DURATION_S = 1.0

# Loop / progress
LOOP_HZ = 50
LOOP_PERIOD_S = 1.0 / LOOP_HZ
PROGRESS_NOTIFY_DELTA = 0.05  # only emit MCP progress when delta >= 5%

# Velocity caps (m/s, m/s, m/s, rad/s) — match the keyboard teleop example.
#
# These are Isaac-Sim-tuned, and 3-5x higher than the only numbers anyone has
# vetted against this physical robot (walk_velocity.py's 0.3 / 0.3, which
# come from xr_teleoperate's own controller-button safety cap). They stay as
# the *sim* caps because they are tuned and exercised there — but they are no
# longer what reaches real hardware: `send_velocity` re-clamps to the
# hardware-vetted set below whenever SIM_MODE == "real".
#
# Previously these were the only clamp on either target, which meant wiring
# real-mode odom silently gave walk_to/turn permission to command 1.0 m/s and
# ~90 deg/s at a humanoid, on a path where nothing had ever been measured.
MAX_FWD_VEL = 1.0
MAX_BACK_VEL = -0.6
MAX_LAT_VEL = 0.5
MAX_YAW_VEL = 1.57

# Proportional control gains tuned against unitree_sim_isaaclab on 2026-05-09.
KP_LIN = 0.8
KP_YAW = 1.2

DEFAULT_HEIGHT = 0.78

_publisher: Any = None


def _get_publisher() -> Any:
    """Lazy ChannelPublisher for `rt/run_command/cmd`."""
    global _publisher
    if _publisher is None:
        from unitree_sdk2py.core.channel import ChannelPublisher
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

        _publisher = ChannelPublisher(CMD_TOPIC, String_)
        _publisher.Init()
        log.info("locomotion.publisher.ready", topic=CMD_TOPIC)
    return _publisher


def send_velocity(vx: float, vy: float, vyaw: float, height: float) -> None:
    """Send one body-frame velocity setpoint, by whatever channel this target uses.

    `height` applies to the sim channel only; see the module docstring.
    """
    if SIM_MODE == "real":
        from bridge.sdk import g1_rpc

        # Re-clamp to the hardware-vetted envelope at the dispatch point, not
        # at each caller's own computation. walk_to/turn size their setpoints
        # against the sim caps above (MAX_FWD_VEL=1.0, MAX_YAW_VEL=1.57), and
        # their proportional controllers saturate those on any target more
        # than a metre or so away -- so without this, reaching real hardware
        # means commanding 3-5x the only speeds ever measured on this robot.
        # Clamping here covers every current and future caller by
        # construction; the controllers still converge, just more slowly.
        # Imported from walk_velocity so there is exactly one definition of
        # "safe on this hardware" (local import mirrors g1_rpc above and
        # keeps this module free of an import-order dependency).
        from bridge.skills.walk_velocity import MAX_LINEAR_VEL, MAX_YAW_VEL as REAL_MAX_YAW_VEL

        vx = max(-MAX_LINEAR_VEL, min(MAX_LINEAR_VEL, vx))
        vy = max(-MAX_LINEAR_VEL, min(MAX_LINEAR_VEL, vy))
        vyaw = max(-REAL_MAX_YAW_VEL, min(REAL_MAX_YAW_VEL, vyaw))

        code, _ = g1_rpc.call_set_velocity(vx, vy, vyaw, VELOCITY_DURATION_S)
        if code != 0:
            # Deliberately not raising: this is called at 50 Hz inside a control
            # loop, and one dropped setpoint is self-correcting — the next
            # iteration re-sends, and if they *all* fail the firmware's duration
            # timeout stops the robot. Raising here would instead abort the
            # skill mid-stride, skipping its own stop sequence.
            log.warning("locomotion.set_velocity.rpc_error", rpc_code=code)
        return

    from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

    payload = str([float(vx), float(vy), float(vyaw), float(height)])
    _get_publisher().Write(String_(data=payload))


async def send_velocity_async(vx: float, vy: float, vyaw: float, height: float) -> None:
    """`send_velocity`, but without blocking the event loop on real hardware.

    On real, `send_velocity` makes a synchronous DDS RPC that waits for an ack
    with SPORT_TIMEOUT_S (10 s) of headroom. Called bare from inside walk_to /
    turn's 50 Hz `async` control loop, a slow ack stalls the entire loop with
    no yield point — which means the `cancel_task` that would stop the walk
    cannot be delivered, precisely while the robot keeps moving. `g1_rpc`'s own
    docstring flags this hazard.

    Awaiting it off-thread keeps the ack semantics exactly as they are (this is
    NOT the unverified `_CallNoReply` change that docstring also floats) while
    letting the loop stay responsive: a slow robot now slows the control rate
    instead of freezing the process that has to stop it.

    Sim publishes are non-blocking, so they stay inline — no thread churn for a
    path that never needed it.
    """
    if SIM_MODE == "real":
        await asyncio.to_thread(send_velocity, vx, vy, vyaw, height)
    else:
        send_velocity(vx, vy, vyaw, height)


async def stop_motion(height: float = DEFAULT_HEIGHT, duration_s: float = 0.4) -> None:
    """Send zero-velocity commands for `duration_s` so the robot rests cleanly."""
    deadline = time.time() + duration_s
    while time.time() < deadline:
        await send_velocity_async(0, 0, 0, height)
        await asyncio.sleep(LOOP_PERIOD_S)


def stop_motion_sync(height: float = DEFAULT_HEIGHT, duration_s: float = 0.4) -> None:
    """Blocking variant of stop_motion for sync skills (e.g. stop_everything)."""
    deadline = time.time() + duration_s
    while time.time() < deadline:
        send_velocity(0, 0, 0, height)
        time.sleep(LOOP_PERIOD_S)


async def maybe_report_progress(
    ctx: Any | None, task: Task, message: str, last_reported: float
) -> float:
    """Emit an MCP progress notification when the delta since last report exceeds the threshold.

    Returns the updated `last_reported` value so callers can keep a running watermark.
    Best-effort: any exception is swallowed (progress is decorative, never load-bearing).
    """
    if ctx is None:
        return last_reported
    if task.progress - last_reported < PROGRESS_NOTIFY_DELTA:
        return last_reported
    try:
        await ctx.report_progress(progress=task.progress, total=1.0, message=message)
    except Exception as exc:
        log.debug("locomotion.progress.failed", task_id=task.task_id, error=str(exc))
    return task.progress
