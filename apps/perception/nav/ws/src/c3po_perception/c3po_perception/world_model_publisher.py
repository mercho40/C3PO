#!/usr/bin/env python3
"""The only thing this container tells the agent.

Publishes ONE std_msgs/String of JSON on /c3po/world_summary (DDS:
rt/c3po/world_summary) at 4 Hz, on domain 42.

WHY std_msgs/String AND NOT A CUSTOM .msg. Not effort — optionality. The D7
contract is built on it: `free_space` absent means "no LiDAR", `objects` absent
means "nothing looked". An XCDR1 @final IDL struct has no representation for
absent; the best it can do is a sentinel number or a zero-length sequence, which
is exactly the "empty list means I looked and saw nothing" failure world_model.py
exists to prevent, re-introduced at the transport layer. JSON has null and
missing keys natively, versions by adding a field rather than by breaking type
compatibility, and costs zero new IDL on the bridge side —
unitree_sdk2py.idl.std_msgs.msg.dds_.String_ is already installed and already
used by sdk/state.py for rt/sim_state.

(The /cmd_vel return path takes the opposite decision — binary
geometry_msgs::msg::dds_::Twist_ — and the split is principled: fixed shape,
frozen since 2010, 20 Hz, actuating => IDL. Variable shape, evolving, 4 Hz,
advisory => JSON.)

WHY THE BUILDER-INPUT SHAPE AND NOT world_model.to_dict(). We publish exactly
world_model.build()'s KEYWORD ARGUMENTS. Three of D7's four rules are policy,
not observation: MAX_OBJECTS is a token budget this container knows nothing
about; `notes` are LLM prompt surface that must version with the prompt; and
landmarks are bridge-side state (skills/landmarks.py) merged after the fact.
Emitting the finished snapshot here would fork the contract into two languages,
only one of which has tests that run with no robot.

WHAT THIS NODE DOES OWE D7:
  - Absent is not empty. Every *_online flag is computed from message ARRIVAL,
    never from a list being empty. An online detector seeing nothing publishes
    objects: [] with detector_online: true, and that is a different, useful fact.
  - Everything carries an age, from the message stamp, not from when we ran.
  - Truncation is declared, and the bridge SUMS its own truncation onto ours
    (world_model.build(extra_omitted=...)) — otherwise objects_omitted silently
    under-reports and looks plausible while doing it.
  - It publishes on EVERY tick including empty ones. Without that heartbeat the
    bridge cannot distinguish "the detector looked and saw nothing" from "the
    container is gone", and silence cannot say the first.
"""

from __future__ import annotations

import json
import math
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

REPORT_VERSION = 1

# A source is OFFLINE, not stale, after this. Deliberately short: the bridge
# additionally drops the whole report after 2 s, so being conservative here costs
# a little churn and buys the guarantee that a dead source is never reported as a
# clear scene.
POSE_OFFLINE_AFTER_S = 1.0
DETECTOR_OFFLINE_AFTER_S = 1.5
LIDAR_OFFLINE_AFTER_S = 1.0

# Wire cap. The bridge caps again at MAX_OBJECTS=8 and sums both counts.
# Generous here because the bridge owns the token budget, not us.
MAX_OBJECTS_ON_WIRE = 32

# Bearing convention: 0 ahead, POSITIVE LEFT (CCW) — D7's, and `turn`'s.
SECTORS = {"ahead_m": (-35.0, 35.0), "left_m": (55.0, 125.0),
           "right_m": (-125.0, -55.0)}
BEHIND_MIN_ABS_DEG = 145.0


def _norm180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


class WorldModelPublisher(Node):
    def __init__(self) -> None:
        super().__init__("world_model_publisher")
        self.declare_parameter("publish_hz", 4.0)

        # RELIABLE / KEEP_LAST(1) / VOLATILE, explicitly. The bridge's reader is
        # at cyclonedds defaults (BEST_EFFORT), and requested-BEST_EFFORT vs
        # offered-RELIABLE matches — verified live on this robot. Do NOT make
        # this TRANSIENT_LOCAL and then depend on it: a VOLATILE reader receives
        # no historical sample, so a late-joining bridge sees nothing until the
        # next tick. At 4 Hz that is fine; at 0.1 Hz it would not be.
        qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST, depth=1,
                         durability=DurabilityPolicy.VOLATILE)
        self.pub = self.create_publisher(String, "/c3po/world_summary", qos)

        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.create_subscription(String, "/c3po/objects", self._on_objects, 10)
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)

        self._pose = None
        self._pose_stamp = 0.0
        self._pose_seen = 0.0
        self._objects: list[dict] = []
        self._objects_omitted = 0
        self._objects_seen = 0.0
        self._free_space = None
        self._scan_seen = 0.0
        self._notes: list[str] = []

        hz = float(self.get_parameter("publish_hz").value)
        self.create_timer(1.0 / hz, self._emit)
        self.get_logger().info("world_model_publisher up -> /c3po/world_summary")

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_odom(self, msg: Odometry) -> None:
        q = msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                         1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self._pose = {"x_m": round(msg.pose.pose.position.x, 2),
                      "y_m": round(msg.pose.pose.position.y, 2),
                      "yaw_deg": round(math.degrees(yaw), 1)}
        self._pose_stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._pose_seen = time.time()

    def _on_objects(self, msg: String) -> None:
        """Detections from the vision container — ALREADY egocentric.

        D2.2 option 1: the vision container holds the base_link<-camera
        extrinsic as a constant and resolves range/bearing itself, so there is
        no TF work here and none in the bridge. A parse failure is a note, never
        a silent drop, and never an empty scene.
        """
        try:
            payload = json.loads(msg.data)
            if int(payload.get("v", 0)) != 1:
                raise ValueError(f"unsupported objects schema v={payload.get('v')}")
        except Exception as exc:
            self._notes.append(f"Detector payload rejected: {exc}")
            return
        self._objects_seen = time.time()
        found = payload.get("objects") or []
        self._objects_omitted = int(payload.get("objects_omitted", 0) or 0)
        if len(found) > MAX_OBJECTS_ON_WIRE:
            self._objects_omitted += len(found) - MAX_OBJECTS_ON_WIRE
        self._objects = found[:MAX_OBJECTS_ON_WIRE]

    def _on_scan(self, msg: LaserScan) -> None:
        """Free space, four coarse sectors, from the ground-filtered scan.

        Requires the scan already be in base_footprint (pointcloud_to_laserscan's
        target_frame). Doing the rotation here instead would be a second place
        for a frame bug to hide.
        """
        self._scan_seen = time.time()
        if msg.header.frame_id and msg.header.frame_id != "base_footprint":
            self._notes.append(
                f"Scan arrives in '{msg.header.frame_id}', not 'base_footprint'; "
                "free-space bearings are unrotated and should not be trusted.")

        sectors: dict[str, float | None] = {k: None for k in
                                            ("ahead_m", "left_m", "right_m", "behind_m")}
        for i, r in enumerate(msg.ranges):
            if not math.isfinite(r) or r < msg.range_min or r > msg.range_max:
                continue
            b = _norm180(math.degrees(msg.angle_min + i * msg.angle_increment))
            for name, (lo, hi) in SECTORS.items():
                if lo <= b <= hi:
                    cur = sectors[name]
                    sectors[name] = r if cur is None else min(cur, r)
            if abs(b) >= BEHIND_MIN_ABS_DEG:
                cur = sectors["behind_m"]
                sectors["behind_m"] = r if cur is None else min(cur, r)

        kept = {k: round(v, 2) for k, v in sectors.items() if v is not None}
        # An all-empty result means the scan carried nothing usable. Publishing
        # {} would read as "four sectors, all clear". None reads as "no
        # estimate", which is the true and safe statement.
        self._free_space = kept or None

    def _emit(self) -> None:
        now = time.time()
        notes, self._notes = self._notes, []

        pose_online = self._pose is not None and (now - self._pose_seen) < POSE_OFFLINE_AFTER_S
        detector_online = (now - self._objects_seen) < DETECTOR_OFFLINE_AFTER_S
        lidar_online = (now - self._scan_seen) < LIDAR_OFFLINE_AFTER_S

        report = {
            "report_version": REPORT_VERSION,
            "stamp_unix": round(now, 3),
            # Keys below are exactly world_model.build()'s keyword arguments.
            "pose": self._pose if pose_online else None,
            "pose_age_s": (round(max(0.0, self._now() - self._pose_stamp), 2)
                           if pose_online else None),
            "detector_online": detector_online,
            "objects": self._objects if detector_online else [],
            "objects_omitted": self._objects_omitted if detector_online else 0,
            "lidar_online": lidar_online and self._free_space is not None,
            "free_space": self._free_space if lidar_online else None,
            # Only what the CONTAINER knows: a rejected payload, a scan in the
            # wrong frame. The bridge appends its own degradation notes from
            # world_model._degrade(); it does not need ours restated.
            "notes": notes,
        }
        self.pub.publish(String(data=json.dumps(report, separators=(",", ":"))))


def main() -> None:
    rclpy.init()
    node = WorldModelPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
