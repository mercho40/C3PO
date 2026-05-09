"""LowState + sim_state subscribers for the G1 (unitree_hg IDL family).

Maintains the most recent messages from each topic in thread-safe slots.
`get_state()` returns a typed dict matching the shape we expose to the LLM via MCP.

Topics:
  - `rt/lowstate` (`unitree_hg::msg::dds_::LowState_`): real-robot-equivalent
    state — IMU + motors + FSM mode + tick. **No global XYZ pose** on this
    topic; pose comes from a separate channel.
  - `rt/sim_state` (`std_msgs::msg::dds_::String_`, sim-only): JSON-encoded
    snapshot from Isaac Sim that includes `root_pose` (world-frame x/y/z + qwxyz).
    Real G1 won't publish this; the subscriber stays alive but returns None
    when no message has arrived.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

LOWSTATE_TOPIC = "rt/lowstate"
SIM_STATE_TOPIC = "rt/sim_state"


# G1's `mode_machine` field is an FSM index. The exact mapping is firmware-
# dependent; this table covers the common values seen in unitree_sim_isaaclab
# and on real G1 EDU. Unknown values fall back to "unknown".
_POSTURE_BY_MODE: dict[int, str] = {
    0: "idle",
    1: "balance",
    2: "stand",
    3: "walking",
    4: "damp",
    5: "sit",
    9: "lie_down",
}


@dataclass
class _LowStateSnapshot:
    """Latest LowState_ values we care about, plus capture time."""

    received_at: float = 0.0
    tick: int = 0
    mode_machine: int = 0
    motor_count: int = 0
    has_imu: bool = False
    raw_message_count: int = 0


@dataclass
class _SimStateSnapshot:
    """Latest sim_state-derived pose, plus capture time. Sim-only."""

    received_at: float = 0.0
    x_meters_world: float = 0.0
    y_meters_world: float = 0.0
    z_meters_world: float = 0.0
    yaw_radians_world: float = 0.0
    raw_message_count: int = 0


def _yaw_from_quaternion(qw: float, qx: float, qy: float, qz: float) -> float:
    """Extract yaw (rotation about world Z) from a quaternion."""
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


class StateSampler:
    """Subscribes to `rt/lowstate` (real + sim) and `rt/sim_state` (sim-only).

    Both subscribers stay alive for the bridge's lifetime; `get_state()`
    composes their latest values into the shape the MCP tool exposes.
    """

    def __init__(self, queue_depth: int = 10) -> None:
        self._lock = threading.Lock()
        self._lowstate = _LowStateSnapshot()
        self._sim = _SimStateSnapshot()
        self._lowstate_sub: Any = None
        self._sim_sub: Any = None
        self._queue_depth = queue_depth

    def start(self) -> None:
        """Open the DDS subscribers. Call once after `init_dds`."""
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

        self._lowstate_sub = ChannelSubscriber(LOWSTATE_TOPIC, LowState_)
        self._lowstate_sub.Init(self._on_lowstate, self._queue_depth)

        self._sim_sub = ChannelSubscriber(SIM_STATE_TOPIC, String_)
        self._sim_sub.Init(self._on_sim_state, self._queue_depth)

        log.info("state.subscribers.ready", topics=[LOWSTATE_TOPIC, SIM_STATE_TOPIC])

    def _on_lowstate(self, msg: Any) -> None:
        with self._lock:
            self._lowstate = _LowStateSnapshot(
                received_at=time.time(),
                tick=int(msg.tick),
                mode_machine=int(msg.mode_machine),
                motor_count=len(msg.motor_state),
                has_imu=msg.imu_state is not None,
                raw_message_count=self._lowstate.raw_message_count + 1,
            )

    def _on_sim_state(self, msg: Any) -> None:
        # Isaac Sim wraps the dict in `init_state` as a nested-JSON string.
        try:
            outer = json.loads(msg.data)
            inner_raw = outer.get("init_state")
            inner = json.loads(inner_raw) if isinstance(inner_raw, str) else inner_raw
            pose = inner["articulation"]["robot"]["root_pose"][0]
            x, y, z, qw, qx, qy, qz = (float(v) for v in pose[:7])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning("sim_state.parse_failed", error=str(exc))
            return

        yaw = _yaw_from_quaternion(qw, qx, qy, qz)
        with self._lock:
            self._sim = _SimStateSnapshot(
                received_at=time.time(),
                x_meters_world=x,
                y_meters_world=y,
                z_meters_world=z,
                yaw_radians_world=yaw,
                raw_message_count=self._sim.raw_message_count + 1,
            )

    def get_state(self) -> dict[str, Any]:
        """Return the current state in the shape the MCP `get_state` tool exposes."""
        with self._lock:
            low = self._lowstate
            sim = self._sim

        if low.received_at == 0.0:
            return {
                "pose": None,
                "battery_pct": None,
                "posture": "no_data_yet",
                "faults": ["no_lowstate_received"],
                "raw": {
                    "tick": 0,
                    "mode_machine": 0,
                    "motor_count": 0,
                    "lowstate_messages_received": 0,
                    "sim_state_messages_received": 0,
                },
            }

        now = time.time()
        lowstate_age = now - low.received_at
        faults: list[str] = []
        if lowstate_age > 1.0:
            faults.append(f"stale_lowstate_{lowstate_age:.1f}s")

        posture = _POSTURE_BY_MODE.get(low.mode_machine, "unknown")

        # Pose is sim-only. If sim_state hasn't arrived (real robot, or sim
        # without the bridge), leave it null.
        pose: dict[str, float] | None = None
        if sim.received_at > 0.0:
            pose = {
                "x_meters_world": sim.x_meters_world,
                "y_meters_world": sim.y_meters_world,
                "z_meters_world": sim.z_meters_world,
                "yaw_radians_world": sim.yaw_radians_world,
            }

        return {
            "pose": pose,
            # Battery is on its own DDS topic — wire in Phase 1.
            "battery_pct": None,
            "posture": posture,
            "faults": faults,
            "raw": {
                "tick": low.tick,
                "mode_machine": low.mode_machine,
                "motor_count": low.motor_count,
                "lowstate_messages_received": low.raw_message_count,
                "lowstate_age_s": round(lowstate_age, 3),
                "sim_state_messages_received": sim.raw_message_count,
                "sim_state_age_s": round(now - sim.received_at, 3) if sim.received_at else None,
            },
        }


_sampler_singleton: StateSampler | None = None


def get_sampler() -> StateSampler:
    """Lazy module-level singleton — bridges the MCP tool to the subscriber."""
    global _sampler_singleton
    if _sampler_singleton is None:
        _sampler_singleton = StateSampler()
        _sampler_singleton.start()
    return _sampler_singleton
