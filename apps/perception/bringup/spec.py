"""What each container is, and what each stage claims. Declarative.

Everything in this module is a value with no side effects, which is the point:
these were `case` statements and inline `docker create` flag lists in a shell
script, so the only way to ask "does nav2 still claim the camera?" was to run it
on the robot and watch what the other team lost.

THE FLAGS ARE LOAD-BEARING AND THE REASONS ARE KEPT WITH THEM. Several were paid
for in debugging time and read as arbitrary without the note attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# --- images -----------------------------------------------------------------

NAV_IMAGE = "c3po/perception-nav:humble"
VISION_IMAGE = "c3po/perception-vision:r35.3.1"

# The container names are a contract: `_common.sh`, `stop_c3po` and the health
# check all match on the `c3po-perception` prefix.
NAV = "c3po-perception-nav"
VISION = "c3po-perception-vision"
STT = "c3po-perception-stt"


@dataclass(frozen=True)
class Container:
    """One container, as flags rather than as a shell line."""

    name: str
    image: str
    #: `--runtime nvidia` is NOT the default on this daemon (DefaultRuntime is
    #: runc). It is what triggers the CSV mount of libcuda from the host;
    #: without it the in-image TensorRT loads and then returns NULL from
    #: deserialize_cuda_engine, which reads like a TensorRT bug and is not one.
    runtime: Optional[str] = None
    #: Never a bridge network. The Mid-360 unicasts to eth0, `--network host`
    #: is what makes `lo` here the same `lo` the bridge binds for domain 42,
    #: and a bridge-network container brings docker0 UP — which the bridge's
    #: autodetermine DDS could then bind on its next restart, seeing none of
    #: the robot.
    network: str = "host"
    ipc: Optional[str] = "host"
    #: Leave cores for the bridge and the OS. The bridge owns stop_everything;
    #: it must not be starved by a costmap update.
    cpuset: str = ""
    #: When the budget is blown the OOM killer takes perception, not the MCP
    #: server. Failure DIRECTION matters more than failure.
    memory: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    volumes: Sequence[str] = ()
    #: 81 = video4linux, 189 = usb_device. The pyrealsense2 wheel is a V4L2
    #: build and needs both, plus /run/udev for enumeration and hotplug.
    device_cgroup_rules: Sequence[str] = ()
    labels: Dict[str, str] = field(default_factory=dict)
    command: Sequence[str] = ()

    def create_args(self) -> List[str]:
        """The full `docker create ...` argv. Order is stable so tests can read it."""
        args = ["create", "--name", self.name]
        # `--restart no` is not a default worth losing: gemm uses
        # `unless-stopped`, which is why their stack returns on every boot. A
        # machine powering on is not somebody asking for the sensors.
        args += ["--restart", "no"]
        if self.runtime:
            args += ["--runtime", self.runtime]
        args += ["--network", self.network]
        if self.ipc:
            args += ["--ipc", self.ipc]
        if self.cpuset:
            args += ["--cpuset-cpus", self.cpuset]
        if self.memory:
            args += ["--memory", self.memory]
        for key in sorted(self.env):
            args += ["-e", f"{key}={self.env[key]}"]
        for rule in self.device_cgroup_rules:
            args += ["--device-cgroup-rule", rule]
        for volume in self.volumes:
            args += ["-v", volume]
        for key in sorted(self.labels):
            args += ["--label", f"{key}={self.labels[key]}"]
        args.append(self.image)
        args += list(self.command)
        return args


# --- stages -----------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    """One bring-up stage: what runs, what it takes, and what to say first."""

    name: str
    #: Which containers this stage brings up.
    containers: Tuple[str, ...]
    #: THE ONLY REMAINING DEVICE CLAIM. The LiDAR stopped being one when
    #: odometry.launch.py moved to `lidar_source:=republish` — the cloud and IMU
    #: arrive as ordinary multi-consumer DDS topics from the vendor's own
    #: republish, measured at 9.57 Hz with the co-tenant's whole stack running.
    #: Claiming the Livox for these stages cost the other team a sensor we no
    #: longer need to take.
    claims_camera: bool = False
    launch_file: str = ""
    launch_args: Tuple[str, ...] = ()
    #: How readiness is proven. `stt` publishes no ROS topic at all — gating it
    #: on /c3po/world_summary waits 90 s for something that will never arrive
    #: and then rolls back a container that was working.
    readiness: str = "topic"  # "topic" | "http" | "none"
    summary: Tuple[str, ...] = ()


_NO_SENSORS = "sensors claimed: NONE — the Livox and the RealSense stay with gemm"
_CAMERA_ONLY = (
    "sensors claimed: RealSense D435i — the DETECTOR needs the device for depth",
    "  the LiDAR is NOT claimed: it comes over the vendor republish",
)
_NAV2_UNCONFIGURED = (
    "Nav2 comes up UNCONFIGURED (autostart:=false); the bridge's cmd_vel gate stays closed"
)

STAGES: Dict[str, Stage] = {
    "fake": Stage(
        name="fake",
        containers=(NAV,),
        launch_file="fake.launch.py",
        summary=(_NO_SENSORS,),
    ),
    "nav2-fake": Stage(
        name="nav2-fake",
        containers=(NAV,),
        # nav2.launch.py with sources:=fake rather than its own launch file, so
        # the seven Nav2 node definitions and their load-bearing remaps live in
        # exactly one place.
        launch_file="nav2.launch.py",
        launch_args=("sources:=fake",),
        summary=(
            _NO_SENSORS,
            "Nav2 on SYNTHETIC odom/scan: the lifecycle, the planner, the controller",
            "and the /c3po/cmd_vel path, with nothing taken from the other team",
            _NAV2_UNCONFIGURED,
        ),
    ),
    "stt": Stage(
        name="stt",
        containers=(STT,),
        readiness="http",
        summary=(
            "sensors claimed: NONE — GPU only. Speech-to-text needs the GPU, not",
            "the camera, so this opens no device and gemm keeps both sensors.",
            "Serves POST /transcribe on 127.0.0.1:8082 for the bridge.",
        ),
    ),
    "odometry": Stage(
        name="odometry",
        containers=(NAV,),
        launch_file="odometry.launch.py",
        summary=(
            "sensors claimed: NONE — the LiDAR arrives on the vendor's DDS republish",
            "  (odometry.launch.py defaults to lidar_source:=republish, an ordinary",
            "   multi-consumer topic; the Livox stays with whoever holds it)",
        ),
    ),
    # THE REAL LIDAR RING WITHOUT GIVING UP A CAMERA.
    #
    # Added 2026-08-29 after a headset session where the only way to get a real
    # radar was `perception`, which starts the vision container, which takes
    # the RealSense — so the operator had to choose between the lidar and the
    # head camera. That choice was never necessary.
    #
    # `perception.launch.py` is `odometry.launch.py` + `world_model_publisher`,
    # and neither touches a camera: the LiDAR arrives on the vendor's republish
    # and the launch file declares nothing but lidar_height_m, mount_yaw_deg,
    # twist_source and world_model_hz. The RealSense claim comes entirely from
    # the SECOND CONTAINER the `perception` stage adds alongside it. Drop that
    # container and the claim goes with it.
    #
    # The detector is then offline, and the world model says so — `absent is
    # not empty`, reported as `detector_online: false` with a note rather than
    # as a clear scene. That is an honest degradation, not a broken stage.
    "lidar": Stage(
        name="lidar",
        containers=(NAV,),
        launch_file="perception.launch.py",
        summary=(
            _NO_SENSORS,
            "the LiDAR ring and the world model, with NO detector: this is the",
            "  stage for a headset radar while the head camera stays on :8001",
            "objects are reported as OFFLINE, never as an empty scene",
        ),
    ),
    "perception": Stage(
        name="perception",
        containers=(NAV, VISION),
        claims_camera=True,
        launch_file="perception.launch.py",
        summary=_CAMERA_ONLY,
    ),
    "nav2": Stage(
        name="nav2",
        containers=(NAV, VISION),
        claims_camera=True,
        launch_file="nav2.launch.py",
        summary=_CAMERA_ONLY + (_NAV2_UNCONFIGURED,),
    ),
}


def stage_names() -> List[str]:
    return list(STAGES)


# --- building the containers for a stage ------------------------------------


def containers_for(
    stage: Stage,
    *,
    c3po_dir: str,
    home: str,
    log_dir: str,
    env: Dict[str, str],
    nav_digest: str = "",
    lidar_source: str = "republish",
) -> List[Container]:
    """The concrete containers this stage runs, in creation order."""
    config_mount = f"{c3po_dir}/apps/perception/config:/opt/c3po/config:ro"
    models_mount = f"{home}/.c3po/models:/opt/c3po/models:ro"
    logs_mount = f"{log_dir}:/logs"
    out: List[Container] = []

    if NAV in stage.containers:
        launch_args = list(stage.launch_args)
        if lidar_source == "driver":
            # Claiming the device and telling the launch file to use it are two
            # different things. Setting only the first would stop gemm, take the
            # Livox, and then run the republish path anyway — a stolen sensor
            # nothing reads, with every log line still saying "republish".
            launch_args.append("lidar_source:=driver")
        labels = {"c3po.stage": stage.name}
        if nav_digest:
            labels["c3po.image"] = nav_digest
        out.append(
            Container(
                name=NAV,
                image=NAV_IMAGE,
                cpuset="0-4",
                memory="8g",
                env={
                    "ROS_DOMAIN_ID": "42",
                    "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp",
                    "CYCLONEDDS_URI": "file:///opt/c3po/config/cyclonedds-domain42.xml",
                },
                volumes=(config_mount, logs_mount),
                labels=labels,
                command=("ros2", "launch", "c3po_perception", stage.launch_file) + tuple(launch_args),
            )
        )

    if STT in stage.containers:
        # GPU, NO CAMERA, NO ROS. No device-cgroup rules and no /dev bind,
        # because speech-to-text has no business holding a camera the other
        # team is using. Loopback-only: an endpoint that accepts a POST body
        # should not be reachable from the school Wi-Fi.
        out.append(
            Container(
                name=STT,
                image=VISION_IMAGE,
                runtime="nvidia",
                ipc=None,
                cpuset="5-6",
                memory="4g",
                env={
                    "NVIDIA_VISIBLE_DEVICES": "all",
                    "NVIDIA_DRIVER_CAPABILITIES": "all",
                    "C3PO_STT_HOST": "127.0.0.1",
                    "C3PO_STT_PORT": "8082",
                    "C3PO_STT_ONLY": "1",
                },
                volumes=(models_mount, logs_mount),
                labels={"c3po.stage": stage.name},
                command=("python3", "-m", "c3po_vision.transcribe"),
            )
        )

    if VISION in stage.containers:
        out.append(
            Container(
                name=VISION,
                image=VISION_IMAGE,
                runtime="nvidia",
                cpuset="5",
                memory="4g",
                env={
                    "NVIDIA_VISIBLE_DEVICES": "all",
                    "NVIDIA_DRIVER_CAPABILITIES": "all",
                    "C3PO_DDS_DOMAIN": "42",
                    "CYCLONEDDS_URI": "file:///opt/c3po/config/cyclonedds-domain42.xml",
                    "C3PO_VISION_STREAM": env.get("C3PO_VISION_STREAM", "1"),
                    "C3PO_VISION_STREAM_HOST": env.get("C3PO_VISION_STREAM_HOST", "127.0.0.1"),
                    "C3PO_VISION_STREAM_PORT": env.get("C3PO_VISION_STREAM_PORT", "8081"),
                    "C3PO_VISION_STREAM_HZ": env.get("C3PO_VISION_STREAM_HZ", "5"),
                },
                device_cgroup_rules=("c 81:* rmw", "c 189:* rmw"),
                # The /dev/bus/usb DIRECTORY, never `--device /dev/bus/usb/003/004`:
                # libusb node numbers are renumbered on every enumeration and the
                # specific node breaks on the first replug.
                volumes=(
                    "/dev/bus/usb:/dev/bus/usb",
                    "/dev:/dev",
                    "/run/udev:/run/udev:ro",
                    config_mount,
                    models_mount,
                    "c3po-trt-engines:/opt/c3po/engines",
                    logs_mount,
                ),
                labels={"c3po.stage": stage.name},
            )
        )

    return out


def containers_to_replace(stage: Stage, existing: Sequence[str]) -> List[str]:
    """Which existing containers this stage should remove before starting.

    STT COMPOSES; IT DOES NOT REPLACE. `stt` opens no device and runs no ROS —
    that is the entire reason it is a separate stage. The shell version removed
    every container matching the prefix, so `perception_up stt` tore down a
    running nav2 and `perception_up nav2` tore down a running STT server. The
    two things designed to coexist could not, and the failure was quiet in the
    direction that matters: the bridge falls back to CPU whisper and says so
    only in a log line nobody is reading at that moment.
    """
    if stage.containers == (STT,):
        return [c for c in existing if c == STT]
    return [c for c in existing if c != STT]
