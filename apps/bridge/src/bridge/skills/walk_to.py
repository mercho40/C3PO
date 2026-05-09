"""walk_to: drive the G1 toward a world-frame XY target until within stop_distance.

Uses Isaac Sim's velocity command channel `rt/run_command/cmd`, which expects
a Python-list-as-string `"[x_vel, y_vel, yaw_vel, height]"` (m/s, m/s, rad/s, m).
The walk policy is body-frame, so we rotate the world-frame error into the
robot's frame each loop iteration.

Phase 1 v1: synchronous (the MCP tool blocks until done). task_id is generated
and returned in the result so callers can correlate logs. Live progress
streaming via MCP `progressToken` is a v2 enhancement.

Tuning notes (validated against unitree_sim_isaaclab on 2026-05-09):
- Forward gain `KP_LIN = 0.6` produces a smooth approach without overshoot.
- Yaw gain `KP_YAW = 0.8` keeps the robot facing the target during travel.
- Max body-frame velocity `MAX_VEL = 0.6 m/s` — higher tends to destabilise.
- Loop @ 50 Hz matches the example teleop's command rate.
- The policy walks at ~13% of commanded forward velocity, so even small
  targets take noticeable time. We keep a generous default timeout.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

import structlog

log = structlog.get_logger(__name__)

CMD_TOPIC = "rt/run_command/cmd"
LOOP_HZ = 50
# Match unitree_sim_isaaclab/send_commands_keyboard.py ranges. Forward velocity
# can go up to 1.0 m/s; lateral and yaw caps are tighter.
MAX_FWD_VEL = 1.0
MAX_BACK_VEL = -0.6
MAX_LAT_VEL = 0.5
MAX_YAW_VEL = 1.57
KP_LIN = 0.8
KP_YAW = 1.2
DEFAULT_HEIGHT = 0.78
# Slow forward velocity when not facing target — turn first, walk second.
# At yaw error 0 → full speed; at yaw error π/2 → zero forward speed.
YAW_GATE_DEG = 90  # degrees — beyond this, no forward velocity

# Module-level publisher cache so we don't spin up a new ChannelPublisher
# every call (which would briefly stall during DDS discovery).
_publisher: Any = None


def _get_publisher() -> Any:
    global _publisher
    if _publisher is None:
        from unitree_sdk2py.core.channel import ChannelPublisher
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

        _publisher = ChannelPublisher(CMD_TOPIC, String_)
        _publisher.Init()
        log.info("walk_to.publisher.ready", topic=CMD_TOPIC)
    return _publisher


def _send_velocity(vx: float, vy: float, vyaw: float, height: float) -> None:
    from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

    payload = str([float(vx), float(vy), float(vyaw), float(height)])
    _get_publisher().Write(String_(data=payload))


def run(
    target_x: float,
    target_y: float,
    stop_distance_m: float = 1.0,
    timeout_s: float = 60.0,
    height: float = DEFAULT_HEIGHT,
) -> dict[str, Any]:
    """Drive the G1 to (target_x, target_y) world-frame and stop.

    Returns immediately with status='no_pose' if the StateSampler hasn't seen
    a pose yet. Otherwise loops at 50 Hz until either:
      - the robot is within stop_distance_m of the target, or
      - timeout_s elapses.
    Always sends a final zero-velocity command before returning.
    """
    from bridge.sdk.state import get_sampler

    task_id = f"tsk_{uuid.uuid4().hex[:12]}"

    sampler = get_sampler()
    initial = sampler.get_state()
    pose = initial.get("pose")
    if pose is None:
        log.warning("walk_to.no_pose", task_id=task_id)
        return {
            "task_id": task_id,
            "status": "no_pose",
            "error": "no pose available — sim_state hasn't been received",
            "stub": False,
        }

    start_x = float(pose["x_meters_world"])
    start_y = float(pose["y_meters_world"])
    initial_distance = math.hypot(target_x - start_x, target_y - start_y)
    log.info(
        "walk_to.start",
        task_id=task_id,
        target=(target_x, target_y),
        start=(start_x, start_y),
        distance=initial_distance,
        stop_distance_m=stop_distance_m,
        timeout_s=timeout_s,
    )

    if initial_distance <= stop_distance_m:
        log.info("walk_to.already_there", task_id=task_id, distance=initial_distance)
        _send_velocity(0, 0, 0, height)
        return {
            "task_id": task_id,
            "status": "already_within_stop_distance",
            "initial_distance_m": initial_distance,
            "duration_s": 0.0,
            "stub": False,
        }

    period = 1.0 / LOOP_HZ
    deadline = time.time() + timeout_s
    started_at = time.time()

    last_distance = initial_distance
    while time.time() < deadline:
        state = sampler.get_state()
        pose = state.get("pose")
        if pose is None:
            time.sleep(period)
            continue
        x = float(pose["x_meters_world"])
        y = float(pose["y_meters_world"])
        yaw = float(pose["yaw_radians_world"])

        dx_world = target_x - x
        dy_world = target_y - y
        distance = math.hypot(dx_world, dy_world)
        if distance <= stop_distance_m:
            break

        # Rotate world-frame error into body frame.
        cos_y = math.cos(yaw)
        sin_y = math.sin(yaw)
        dx_body = cos_y * dx_world + sin_y * dy_world
        dy_body = -sin_y * dx_world + cos_y * dy_world

        # Yaw target & error.
        target_yaw = math.atan2(dy_world, dx_world)
        yaw_err = math.atan2(math.sin(target_yaw - yaw), math.cos(target_yaw - yaw))
        vyaw = max(-MAX_YAW_VEL, min(MAX_YAW_VEL, KP_YAW * yaw_err))

        # Yaw gate: scale forward velocity by cos(yaw_err) clipped to [0, 1].
        # When facing target → full speed; when sideways → zero forward; turn first.
        gate = max(0.0, math.cos(yaw_err))
        raw_vx = KP_LIN * dx_body * gate
        vx = max(MAX_BACK_VEL, min(MAX_FWD_VEL, raw_vx))
        vy = max(-MAX_LAT_VEL, min(MAX_LAT_VEL, KP_LIN * dy_body * gate))

        _send_velocity(vx, vy, vyaw, height)
        last_distance = distance
        time.sleep(period)

    duration = time.time() - started_at
    # Always stop.
    for _ in range(int(LOOP_HZ * 0.4)):  # 0.4s of zero velocity
        _send_velocity(0, 0, 0, height)
        time.sleep(period)

    final = sampler.get_state().get("pose") or {}
    final_x = float(final.get("x_meters_world", 0.0))
    final_y = float(final.get("y_meters_world", 0.0))
    final_distance = math.hypot(target_x - final_x, target_y - final_y)
    arrived = final_distance <= stop_distance_m

    log.info(
        "walk_to.done",
        task_id=task_id,
        arrived=arrived,
        final=(final_x, final_y),
        final_distance=final_distance,
        duration_s=duration,
    )

    return {
        "task_id": task_id,
        "status": "arrived" if arrived else "timeout",
        "arrived": arrived,
        "initial_distance_m": initial_distance,
        "final_distance_m": final_distance,
        "displacement_m": math.hypot(final_x - start_x, final_y - start_y),
        "duration_s": round(duration, 2),
        "final_pose": {
            "x_meters_world": final_x,
            "y_meters_world": final_y,
            "yaw_radians_world": float(final.get("yaw_radians_world", 0.0)),
        },
        "stub": False,
    }
