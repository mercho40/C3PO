"""LowState + world-pose subscribers for the G1.

Maintains the most recent messages from each topic in thread-safe slots.
`get_state()` returns a typed dict matching the shape we expose to the LLM via MCP.

Topic names come from `g1_protocol.topics_for(SIM_MODE)`. Joint state is
`LowState_` on both targets. **Pose is not symmetric**, and this is the subtle
part of the module:

- sim  — `rt/sim_state`, a JSON blob in a `String_`, quaternion → yaw.
- real — `rt/odommodestate`, a `unitree_go` `SportModeState_`, Euler rpy → yaw.

Two topics, two types, two parsers. They're selected by `_POSE_SOURCE` and the
choice is reported in `get_state()["raw"]["pose_source"]`. This matters because
DDS matches by type: subscribing the wrong type doesn't raise, it just silently
never delivers a message, which is indistinguishable from a quiet robot.

FSM mode index → human label goes through `g1_protocol.mode_label`.

Faults: not exposed inside `LowState_` on G1. The decoder in `bridge.sdk.faults`
is written and unused — it was built for the WebRTC `errors`/`add_error`/
`rm_error` stream, which is no longer the plan of record (SPEC §16.3). Finding
the DDS-side fault source is open work; until then the faults field carries only
locally-derived entries (staleness), never robot-reported ones.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import structlog

from bridge.sdk import g1_protocol

log = structlog.get_logger(__name__)

_SIM_MODE = os.environ.get("SIM_MODE", "stub")
_TOPICS = g1_protocol.topics_for(_SIM_MODE)
LOWSTATE_TOPIC = _TOPICS.lowstate
SIM_STATE_TOPIC = _TOPICS.sportmodestate
ODOM_TOPIC = _TOPICS.odom
BMS_TOPIC = _TOPICS.bmsstate

# Which channel supplies world-frame pose. These are different topics carrying
# different types, so the subscriber differs too — see `StateSampler.start`.
_POSE_SOURCE = "odom" if (_SIM_MODE == "real" and ODOM_TOPIC) else "sim_state"


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
class _BmsSnapshot:
    """Latest battery state, from `BmsState_` on its own topic.

    Battery is NOT in `LowState_` on the G1 — the humanoid `unitree_hg`
    `LowState_` has no BMS field at all, unlike the quadruped `unitree_go` one.
    That is why `battery_pct` read `None` for so long, and why "faults: none,
    battery: null" was never evidence of a healthy pack: nothing was ever
    subscribed to look.
    """

    received_at: float = 0.0
    soc_pct: int | None = None
    soh_pct: int | None = None
    current_ma: int | None = None
    raw_message_count: int = 0


@dataclass
class _FsmSnapshot:
    """Latest FSM state read over RPC. Real-target only."""

    received_at: float = 0.0
    fsm_id: int | None = None
    fsm_mode: int | None = None


# How often the FSM poller asks the robot. Posture is for humans and the LLM,
# not for a control loop, so this is deliberately slow — it costs a DDS
# round-trip each time and nothing downstream needs it fresher.
FSM_POLL_INTERVAL_S = 0.5


@dataclass
class _PoseSnapshot:
    """Latest world-frame pose, from whichever source this target provides."""

    received_at: float = 0.0
    x_meters_world: float = 0.0
    y_meters_world: float = 0.0
    z_meters_world: float = 0.0
    yaw_radians_world: float = 0.0
    raw_message_count: int = 0


# Back-compat alias: the sim-specific name this used to carry.
_SimStateSnapshot = _PoseSnapshot


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
        self._pose = _PoseSnapshot()
        self._fsm = _FsmSnapshot()
        self._bms = _BmsSnapshot()
        self._lowstate_sub: Any = None
        self._pose_sub: Any = None
        self._bms_sub: Any = None
        self._queue_depth = queue_depth
        self._fsm_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Open the DDS subscribers. Call once after `init_dds`."""
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

        self._lowstate_sub = ChannelSubscriber(LOWSTATE_TOPIC, LowState_)
        self._lowstate_sub.Init(self._on_lowstate, self._queue_depth)

        # DDS matches publisher to subscriber by *type*, so the pose subscriber
        # has to be built for the source this target actually publishes. Getting
        # this wrong doesn't error — it silently receives nothing forever, which
        # is exactly the bug this replaced (real pose subscribed as String_).
        if _POSE_SOURCE == "odom":
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_

            pose_topic = ODOM_TOPIC
            self._pose_sub = ChannelSubscriber(pose_topic, SportModeState_)
            self._pose_sub.Init(self._on_odom, self._queue_depth)
        else:
            from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

            pose_topic = SIM_STATE_TOPIC
            self._pose_sub = ChannelSubscriber(pose_topic, String_)
            self._pose_sub.Init(self._on_sim_state, self._queue_depth)

        # Battery. Its own topic and its own type — see `_BmsSnapshot`. Isaac
        # Sim publishes no BMS, so the topic is None there and we simply don't
        # subscribe rather than waiting forever on a publisher that will never
        # appear.
        topics = [LOWSTATE_TOPIC, pose_topic]
        if BMS_TOPIC:
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import BmsState_

            self._bms_sub = ChannelSubscriber(BMS_TOPIC, BmsState_)
            self._bms_sub.Init(self._on_bms, self._queue_depth)
            topics.append(BMS_TOPIC)

        log.info(
            "state.subscribers.ready",
            topics=topics,
            pose_source=_POSE_SOURCE,
        )

        # FSM state is request/response, not a subscription, so it needs
        # polling. It runs on its own thread rather than inside `get_state()`
        # because `walk_to` calls `get_state()` every 20 ms and a blocking DDS
        # round-trip there would wreck the control loop.
        if _SIM_MODE == "real":
            self._fsm_thread = threading.Thread(
                target=self._poll_fsm, name="fsm-poller", daemon=True
            )
            self._fsm_thread.start()

    def stop(self) -> None:
        """Signal the FSM poller to exit. Subscribers live for the process."""
        self._stop.set()

    def _poll_fsm(self) -> None:
        from bridge.sdk import g1_rpc

        while not self._stop.wait(FSM_POLL_INTERVAL_S):
            try:
                fsm_id = g1_rpc.get_fsm_id()
                fsm_mode = g1_rpc.get_fsm_mode()
            except Exception as exc:  # never let the poller die
                log.warning("fsm.poll_failed", error=str(exc))
                continue
            if fsm_id is None and fsm_mode is None:
                continue
            with self._lock:
                self._fsm = _FsmSnapshot(
                    received_at=time.time(), fsm_id=fsm_id, fsm_mode=fsm_mode
                )

    def _on_odom(self, msg: Any) -> None:
        """Vendor odometry (unitree_go SportModeState_) → world pose.

        Yaw comes from `imu_state.rpy[2]` rather than a quaternion; this message
        carries Euler angles directly. Note this is *odometry*: it drifts and its
        origin is wherever the estimator started, not a global frame.
        """
        try:
            x, y, z = (float(v) for v in msg.position[:3])
            yaw = float(msg.imu_state.rpy[2])
        except Exception as exc:  # malformed / unexpected layout
            log.warning("odom.parse_failed", error=str(exc))
            return
        with self._lock:
            self._pose = _PoseSnapshot(
                received_at=time.time(),
                x_meters_world=x,
                y_meters_world=y,
                z_meters_world=z,
                yaw_radians_world=yaw,
                raw_message_count=self._pose.raw_message_count + 1,
            )

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

    def _on_bms(self, msg: Any) -> None:
        # `soc` is a uint8 percentage; the vendor's own low-battery predicate is
        # `soc < 20`. `current` is signed — negative while discharging — so it
        # tells you charging state without a separate flag.
        with self._lock:
            self._bms = _BmsSnapshot(
                received_at=time.time(),
                soc_pct=int(msg.soc),
                soh_pct=int(msg.soh),
                current_ma=int(msg.current),
                raw_message_count=self._bms.raw_message_count + 1,
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
            self._pose = _PoseSnapshot(
                received_at=time.time(),
                x_meters_world=x,
                y_meters_world=y,
                z_meters_world=z,
                yaw_radians_world=yaw,
                raw_message_count=self._pose.raw_message_count + 1,
            )

    def get_state(self) -> dict[str, Any]:
        """Return the current state in the shape the MCP `get_state` tool exposes."""
        with self._lock:
            low = self._lowstate
            pose_snap = self._pose
            fsm = self._fsm
            bms = self._bms

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
                    "pose_source": _POSE_SOURCE,
                    "pose_messages_received": pose_snap.raw_message_count,
                },
            }

        now = time.time()
        lowstate_age = now - low.received_at
        faults: list[str] = []
        if lowstate_age > 1.0:
            faults.append(f"stale_lowstate_{lowstate_age:.1f}s")

        # `mode_machine` (from LowState_) is NOT the locomotion FSM index that
        # `mode_label` decodes — verified on hardware: mode_machine read 5 while
        # the FSM id was 802. Never label one with the other.
        #
        # Real: the FSM id comes from the RPC poller (api_id 7001). We can't read
        # it off `sportmodestate` because this SDK ships `SportModeState_` only
        # under `unitree_go` (quadruped) and the G1 publishes `unitree_hg` — the
        # limitation is the SDK's type coverage, not the transport.
        #
        # Sim: Isaac Sim populates `mode_machine` with the real FSM value as a
        # convenience, so the label is trustworthy there and nothing to poll.
        if _SIM_MODE == "real":
            posture = g1_protocol.mode_label(fsm.fsm_id) if fsm.fsm_id is not None else "unknown"
        else:
            posture = g1_protocol.mode_label(low.mode_machine)

        # Pose comes from whichever source this target publishes (see
        # `_POSE_SOURCE`). Null until the first message arrives.
        pose: dict[str, float] | None = None
        if pose_snap.received_at > 0.0:
            pose = {
                "x_meters_world": pose_snap.x_meters_world,
                "y_meters_world": pose_snap.y_meters_world,
                "z_meters_world": pose_snap.z_meters_world,
                "yaw_radians_world": pose_snap.yaw_radians_world,
            }

        pose_age = round(now - pose_snap.received_at, 3) if pose_snap.received_at else None
        # Low battery is a real fault, and the one most likely to end a session
        # mid-task. 20% is the vendor's own threshold.
        if bms.soc_pct is not None and bms.soc_pct < 20:
            faults.append(f"low_battery_{bms.soc_pct}pct")

        return {
            "pose": pose,
            # None means "no BmsState_ received yet", not "no battery" — on sim
            # there is no BMS publisher at all. Don't read null as healthy.
            "battery_pct": bms.soc_pct,
            "posture": posture,
            "faults": faults,
            "raw": {
                "tick": low.tick,
                "mode_machine": low.mode_machine,
                "motor_count": low.motor_count,
                "lowstate_messages_received": low.raw_message_count,
                "lowstate_age_s": round(lowstate_age, 3),
                # Which channel the pose above came from, so a null pose can be
                # diagnosed without reading the source.
                "pose_source": _POSE_SOURCE,
                "pose_messages_received": pose_snap.raw_message_count,
                "pose_age_s": pose_age,
                # Raw FSM values behind `posture`, so a surprising label can be
                # traced without another round-trip. Null outside real.
                "fsm_id": fsm.fsm_id,
                "fsm_mode": fsm.fsm_mode,
                "fsm_age_s": round(now - fsm.received_at, 3) if fsm.received_at else None,
                # Battery detail. `soh` is pack health and degrades over the
                # robot's life; `current_ma` is signed, so negative means
                # discharging — which is how you tell a docked robot from one
                # running down without a separate charging flag.
                "battery_soh_pct": bms.soh_pct,
                "battery_current_ma": bms.current_ma,
                "battery_messages_received": bms.raw_message_count,
                "battery_age_s": round(now - bms.received_at, 3) if bms.received_at else None,
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
