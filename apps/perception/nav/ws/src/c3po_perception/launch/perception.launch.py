"""odometry.launch.py + the D7 handover. Stage 7's bring-up.

    everything in odometry.launch.py
    world_model_publisher -> /c3po/world_summary (std_msgs/String JSON, 4 Hz)

THIS STAGE CLAIMS THE LIVOX AND THE REALSENSE. The detections it summarises
arrive on /c3po/objects from the vision container, which `perception_up
perception` starts alongside this one; that container holds the
base_link<-camera extrinsic itself and publishes already-egocentric
range/bearing, so there is no TF work on this side and none in the bridge
(D2.2 option 1).

Note what is NOT here: Nav2. Perception without a planner is instrumentation —
it can be watched for an hour with nothing on domain 42 that could ever emit a
velocity. That separation is the whole reason the stages exist.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    share = FindPackageShare("c3po_perception")

    args = [
        DeclareLaunchArgument("lidar_height_m", default_value="1.15"),
        DeclareLaunchArgument("mount_yaw_deg", default_value="0.0"),
        DeclareLaunchArgument("twist_source", default_value="fastlio"),
        DeclareLaunchArgument("world_model_hz", default_value="4.0"),
    ]

    odometry = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([share, "launch", "odometry.launch.py"])),
        launch_arguments={
            "lidar_height_m": LaunchConfiguration("lidar_height_m"),
            "mount_yaw_deg": LaunchConfiguration("mount_yaw_deg"),
            "twist_source": LaunchConfiguration("twist_source"),
        }.items(),
    )

    # The heartbeat. It publishes on EVERY tick including empty ones: without
    # that, the bridge cannot tell "the detector looked and saw nothing" from
    # "the container is gone", and silence cannot say the first.
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

    return LaunchDescription(args + [odometry, world_model])
