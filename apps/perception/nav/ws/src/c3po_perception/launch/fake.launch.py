"""Stage 3: the whole D7 crossing, with NO SENSORS AT ALL.

    (synthetic) /odom, /scan, /c3po/objects
    world_model_publisher -> /c3po/world_summary  on domain 42
    apps/bridge Domain(42) -> describe_surroundings

This is the only stage that holds nothing shared: no Livox, no RealSense, no
GPU. `perception_up fake` leaves gemm running, which is why it is the stage that
proves the DDS boundary, the JSON contract and "absent is not empty" end to end
across a process boundary, a container boundary and a domain boundary — before
anything depends on it and without asking anyone for a window.

THE SYNTHETIC SOURCES ARE `ros2 topic pub`, NOT A NODE.
Deliberate: the file tree has exactly two Python modules in this package, and a
third one whose only job is to lie would eventually be imported by something
that is not a test. Three CLI publishers cost nothing, are readable in `ps`, and
— the point of Stage 3 — can be killed INDIVIDUALLY to watch a single source go
offline while the others stay up:

    docker exec c3po-perception-nav pkill -f 'topic pub /c3po/objects'

Within DETECTOR_OFFLINE_AFTER_S (1.5 s) the summary must flip to
detector_online: false with `objects: []` AND a plain-language note — not to a
silently empty scene. That distinction is the entire reason this stage exists.

`stamp: now` is the ros2 CLI's substitution for a builtin_interfaces/Time field.
It matters: the bridge marks pose `stale` when pose_age_s exceeds its
staleness bound, so a zero stamp would make a perfectly healthy fake report as
degraded and send someone hunting a clock-skew bug. If a future CLI drops the
keyword, publish real stamps some other way rather than settling for zeros.
"""

import json

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

FAKE_HZ = "4"

# Object wire shape is world_model.Observation.to_dict()'s, exactly: label,
# range_m, bearing_deg, and optional confidence/age_s. bearing_deg is D7's
# convention — 0 ahead, POSITIVE LEFT (CCW) — so `chair` below is on the
# robot's LEFT and `person` slightly to its right. Stage 7 re-checks that sign
# against a human walking to the robot's left; getting it wrong here would
# teach the fake to agree with a bug.
FAKE_OBJECTS = {
    "v": 1,
    "objects": [
        {"label": "person", "range_m": 2.4, "bearing_deg": -12.0, "confidence": 0.88},
        {"label": "chair", "range_m": 1.6, "bearing_deg": 63.0, "confidence": 0.71},
        {"label": "backpack", "range_m": 3.9, "bearing_deg": 155.0, "confidence": 0.55},
    ],
    # Non-zero on purpose: the bridge SUMS its own truncation onto this one, and
    # a fake that always says 0 would never exercise that path.
    "objects_omitted": 2,
}

# Eight bearings 45 degrees apart starting at -180, so every one of D7's four
# sectors gets exactly one sample: index 4 is ahead (0 deg), 6 is left (+90),
# 2 is right (-90), 0 is behind (180). Anything finer would just be a costmap.
FAKE_SCAN_RANGES = [3.0, 2.5, 1.8, 4.0, 2.2, 1.5, 3.5, 2.0]

FAKE_ODOM = (
    "{header: {stamp: now, frame_id: odom}, child_frame_id: base_footprint, "
    "pose: {pose: {position: {x: 0.0, y: 0.0, z: 0.0}, "
    "orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}, "
    "twist: {twist: {linear: {x: 0.0, y: 0.0, z: 0.0}, "
    "angular: {x: 0.0, y: 0.0, z: 0.0}}}}"
)

# frame_id MUST be base_footprint: world_model_publisher notes (correctly) that
# free-space bearings from any other frame are unrotated and untrustworthy, and
# a fake that trips that note teaches people to ignore it.
FAKE_SCAN = (
    "{header: {stamp: now, frame_id: base_footprint}, "
    "angle_min: -3.14159, angle_max: 3.14159, angle_increment: 0.78539816, "
    "time_increment: 0.0, scan_time: 0.25, range_min: 0.45, range_max: 12.0, "
    "ranges: " + json.dumps(FAKE_SCAN_RANGES) + ", intensities: []}"
)

# json.dumps twice: once for the payload, once to quote-and-escape it into a
# YAML scalar the CLI will parse back out intact.
FAKE_DETECTIONS = "{data: " + json.dumps(json.dumps(FAKE_OBJECTS)) + "}"


def _pub(topic: str, msg_type: str, yaml_value: str, name: str) -> ExecuteProcess:
    return ExecuteProcess(
        cmd=["ros2", "topic", "pub", "-r", FAKE_HZ, topic, msg_type, yaml_value],
        name=name,
        output="screen",
    )


def generate_launch_description() -> LaunchDescription:
    args = [DeclareLaunchArgument("world_model_hz", default_value="4.0")]

    world_model = Node(
        package="c3po_perception",
        executable="world_model_publisher",
        name="world_model_publisher",
        output="screen",
        parameters=[{
            "publish_hz": ParameterValue(
                LaunchConfiguration("world_model_hz"), value_type=float),
        }],
    )

    return LaunchDescription(args + [
        world_model,
        _pub("/odom", "nav_msgs/msg/Odometry", FAKE_ODOM, "fake_odom"),
        _pub("/scan", "sensor_msgs/msg/LaserScan", FAKE_SCAN, "fake_scan"),
        _pub("/c3po/objects", "std_msgs/msg/String", FAKE_DETECTIONS,
             "fake_detector"),
    ])
