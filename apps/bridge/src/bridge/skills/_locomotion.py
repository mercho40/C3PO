"""Shared helpers for skills that publish to `rt/run_command/cmd`.

Isaac Sim's velocity command channel expects a Python-list-as-string
`"[x_vel, y_vel, yaw_vel, height]"` (m/s, m/s, rad/s, m). The publisher is
cached at module scope so multiple skill calls don't restart DDS discovery.

Constants come from `unitree_sim_isaaclab/send_commands_keyboard.py` —
forward velocity caps at 1.0 m/s, lateral 0.5, yaw 1.57 rad/s. The walk
policy runs at ~10-15% of commanded velocity, so generous timeouts are
required for skills that travel measurable distances.

`_` prefix on the module: skills inside `bridge.skills` import freely; this
isn't part of the bridge's public API.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from bridge.skills.task_runtime import Task

log = structlog.get_logger(__name__)

CMD_TOPIC = "rt/run_command/cmd"

# Loop / progress
LOOP_HZ = 50
LOOP_PERIOD_S = 1.0 / LOOP_HZ
PROGRESS_NOTIFY_DELTA = 0.05  # only emit MCP progress when delta >= 5%

# Velocity caps (m/s, m/s, m/s, rad/s) — match the keyboard teleop example.
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
    """Publish one velocity command to the run-command channel."""
    from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

    payload = str([float(vx), float(vy), float(vyaw), float(height)])
    _get_publisher().Write(String_(data=payload))


async def stop_motion(height: float = DEFAULT_HEIGHT, duration_s: float = 0.4) -> None:
    """Send zero-velocity commands for `duration_s` so the robot rests cleanly."""
    deadline = time.time() + duration_s
    while time.time() < deadline:
        send_velocity(0, 0, 0, height)
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
