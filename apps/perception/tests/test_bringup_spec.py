"""The bring-up decisions, checked without a Jetson.

Every assertion here corresponds to something that actually went wrong on the
robot while this logic lived in `case` statements and inline `docker create`
lines. None of it needed hardware to be wrong, and none of it needed hardware
to be caught — it just had nowhere to be caught.
"""

from __future__ import annotations

import pytest

from bringup.spec import (
    NAV,
    STAGES,
    STT,
    VISION,
    containers_for,
    containers_to_replace,
)

COMMON = {
    "c3po_dir": "/home/unitree/c3po",
    "home": "/home/unitree",
    "log_dir": "/home/unitree/.c3po/logs/perception",
    "env": {},
}


def build(stage_name: str, **kwargs):
    opts = dict(COMMON)
    opts.update(kwargs)
    return containers_for(STAGES[stage_name], **opts)


def by_name(containers):
    return {c.name: c for c in containers}


# --- what each stage takes --------------------------------------------------


def test_only_the_camera_stages_claim_a_device():
    claiming = {name for name, s in STAGES.items() if s.claims_camera}
    assert claiming == {"perception", "nav2"}


def test_nav2_does_not_claim_the_lidar():
    """It did, for a while, after the claim was no longer needed.

    odometry.launch.py defaults to `lidar_source:=republish` — an ordinary
    multi-consumer DDS topic measured at 9.57 Hz with the co-tenant's entire
    stack running. Claiming the Livox anyway evicted the other team for a
    sensor we were not going to open, and said so in a warning that read as
    justified.
    """
    args = by_name(build("nav2"))[NAV].command
    assert "lidar_source:=driver" not in args


def test_the_driver_override_reaches_the_launch_file():
    """Claiming the device and using it are two different things.

    Setting only the claim would stop gemm, take the Livox, and then run the
    republish path anyway: a stolen sensor nothing reads.
    """
    args = by_name(build("nav2", lidar_source="driver"))[NAV].command
    assert "lidar_source:=driver" in args


def test_the_sensor_free_stages_really_are():
    for name in ("fake", "nav2-fake", "stt", "odometry"):
        assert STAGES[name].claims_camera is False, name
        assert VISION not in STAGES[name].containers, name


# --- stt composes rather than replaces --------------------------------------


def test_stt_does_not_tear_down_a_running_nav2():
    existing = [NAV, VISION, STT]
    assert containers_to_replace(STAGES["stt"], existing) == [STT]


def test_nav2_does_not_tear_down_a_running_stt():
    existing = [NAV, VISION, STT]
    assert containers_to_replace(STAGES["nav2"], existing) == [NAV, VISION]


def test_a_stage_still_replaces_its_own_containers():
    # Otherwise `docker create` fails on the name and the stage never restarts.
    assert containers_to_replace(STAGES["nav2"], [NAV]) == [NAV]


# --- the flags that were paid for in debugging time -------------------------


def test_the_gpu_containers_ask_for_the_nvidia_runtime():
    """Not the default on this daemon (DefaultRuntime runc).

    Without it the in-image TensorRT loads and then returns NULL from
    deserialize_cuda_engine, which reads like a TensorRT bug and is not one.
    """
    assert by_name(build("nav2"))[VISION].runtime == "nvidia"
    assert by_name(build("stt"))[STT].runtime == "nvidia"


def test_the_nav_container_does_not_ask_for_a_gpu():
    assert by_name(build("nav2"))[NAV].runtime is None


def test_stt_gets_no_camera_access_at_all():
    """Speech-to-text has no business holding a camera the other team is using."""
    stt = by_name(build("stt"))[STT]
    assert stt.device_cgroup_rules == ()
    assert not any("/dev" in v for v in stt.volumes)


def test_the_vision_container_gets_both_device_classes():
    # 81 = video4linux, 189 = usb_device. The pyrealsense2 wheel is a V4L2
    # build and needs both.
    rules = by_name(build("nav2"))[VISION].device_cgroup_rules
    assert "c 81:* rmw" in rules and "c 189:* rmw" in rules


def test_usb_is_bound_as_a_directory_not_a_node():
    """libusb node numbers are renumbered on every enumeration.

    A `--device /dev/bus/usb/003/004` breaks on the first replug.
    """
    volumes = by_name(build("nav2"))[VISION].volumes
    assert "/dev/bus/usb:/dev/bus/usb" in volumes


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_nothing_restarts_itself(stage):
    """gemm uses `unless-stopped`, which is why their stack returns every boot.

    A machine powering on is not somebody asking for the sensors.
    """
    for container in build(stage):
        args = container.create_args()
        assert args[args.index("--restart") + 1] == "no"


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_nothing_runs_privileged_or_on_a_bridge_network(stage):
    """This host also runs the process that can walk the legs.

    And a bridge-network container brings docker0 UP, which the bridge's
    autodetermine DDS could bind on its next restart — seeing none of the robot.
    """
    for container in build(stage):
        args = container.create_args()
        assert "--privileged" not in args
        assert args[args.index("--network") + 1] == "host"


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_cores_are_left_for_the_bridge(stage):
    """The bridge owns stop_everything; a costmap update must not starve it."""
    for container in build(stage):
        assert container.cpuset, container.name
        assert "7" not in container.cpuset, container.name


# --- readiness --------------------------------------------------------------


def test_stt_is_not_gated_on_a_ros_topic_it_never_publishes():
    """It was, and the gate waited 90 s and then rolled back a working container."""
    assert STAGES["stt"].readiness == "http"
    assert STAGES["nav2"].readiness == "topic"


# --- the stream stays on loopback -------------------------------------------


def test_the_camera_stream_binds_loopback_unless_someone_says_otherwise():
    """An unauthenticated video feed of a shared lab is not a default."""
    vision = by_name(build("nav2"))[VISION]
    assert vision.env["C3PO_VISION_STREAM_HOST"] == "127.0.0.1"


def test_the_stream_host_can_be_overridden_deliberately():
    vision = by_name(build("nav2", env={"C3PO_VISION_STREAM_HOST": "0.0.0.0"}))[VISION]
    assert vision.env["C3PO_VISION_STREAM_HOST"] == "0.0.0.0"


# --- argv generation --------------------------------------------------------


def test_create_args_start_with_create_and_the_name():
    args = by_name(build("stt"))[STT].create_args()
    assert args[:3] == ["create", "--name", STT]


def test_the_image_precedes_the_command():
    args = by_name(build("stt"))[STT].create_args()
    assert args.index("c3po/perception-vision:r35.3.1") < args.index("python3")


def test_every_stage_labels_its_containers_with_the_stage():
    """`perception_stage()` reads this label; an unlabelled container reads as ''."""
    for name in STAGES:
        for container in build(name):
            assert container.labels.get("c3po.stage") == name


# --- the lidar ring without giving up a camera ------------------------------
#
# Added after a headset session where the only stage producing a real radar was
# `perception`, which starts the vision container, which takes the RealSense —
# so the operator had to choose between the lidar and the head camera. The
# ring comes from `world_model_publisher`, which needs no camera at all.


def test_the_lidar_stage_claims_nothing():
    """The entire point. If this ever claims the camera it has no reason to exist."""
    assert STAGES["lidar"].claims_camera is False


def test_the_lidar_stage_runs_the_world_model_and_only_the_nav_container():
    # `world_model_publisher` is what publishes /c3po/scan — the ring the
    # headset draws. It lives in perception.launch.py, which is
    # odometry.launch.py plus the world model, and neither touches a device.
    stage = STAGES["lidar"]
    assert stage.launch_file == "perception.launch.py"
    containers = build("lidar")
    assert len(containers) == 1, "a second container is what takes the camera"
    assert containers[0].labels.get("c3po.stage") == "lidar"


def test_it_runs_the_same_launch_file_as_perception():
    """Same ring, same world model — the difference is only the container set.

    If these ever diverge, `lidar` has quietly become a different stage and
    the radar it produces is no longer the one `perception` was verified with.
    """
    assert STAGES["lidar"].launch_file == STAGES["perception"].launch_file


def test_it_says_the_detector_will_be_offline():
    """An honest degradation, stated up front.

    Objects report OFFLINE rather than as an empty scene — 'absent is not
    empty'. An operator who is not told will read a clear radar as a clear
    room.
    """
    text = " ".join(STAGES["lidar"].summary).lower()
    assert "detector" in text
    assert "offline" in text
