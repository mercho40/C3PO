"""perception.launch.py + Nav2, brought up UNCONFIGURED. Stage 4 and Stage 8.

    everything in perception.launch.py
    controller_server / smoother_server / planner_server / behavior_server
    bt_navigator / velocity_smoother
    lifecycle_manager_navigation  (autostart: FALSE — see nav2_params.yaml)

NOTHING HERE DRIVES THE ROBOT. Every velocity leaves on /c3po/cmd_vel (DDS
rt/c3po/cmd_vel) on domain 42, and apps/bridge — the actuation chokepoint,
default-CLOSED gate, still up while this container is being rebuilt — decides
whether it becomes SET_VELOCITY. Starting this launch file is not arming
anything; `arm_navigation` on the bridge is.

TWO REMAPS ARE LOAD-BEARING:

  controller_server  cmd_vel -> cmd_vel_nav
      Upstream Nav2's own convention: the controller feeds the smoother, never
      the base directly.

  behavior_server    cmd_vel -> cmd_vel_nav
      nav2_bringup's navigation_launch.py does NOT remap this one, and
      nav2_behaviors' TimedBehavior base class publishes on a bare "cmd_vel".
      Unremapped, Spin/BackUp/DriveOnHeading would bypass the smoother
      entirely and publish onto a topic nothing on this domain reads — the
      robot "refuses to recover" with no error anywhere. (Only `wait` is
      enabled today, which makes this a trap for whoever re-adds the others.)

  velocity_smoother  cmd_vel -> cmd_vel_nav, cmd_vel_smoothed -> /c3po/cmd_vel
      The smoother is the LAST hop inside the container, so it is the one place
      the topic becomes /c3po/cmd_vel. Never publish a bare /cmd_vel from this
      container: that name is gemm's cmd_vel_to_loco convention on domain 0 and
      the isolation here is meant to be structural, not conventional.

The lifecycle manager starts INACTIVE. Container start must never be the same
event as "the robot is ready to be driven" — the same reasoning that keeps the
boot unit at C3PO_NO_TAKEOVER=1. Transition it by hand:

    ros2 service call /lifecycle_manager_navigation/manage_nodes \\
        nav2_msgs/srv/ManageLifecycleNodes "{command: 0}"
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

# /tf and /tf_static are global; every Nav2 node gets these so a namespace
# (which we do not use today) could never split the tree in half.
TF_REMAPS = [("/tf", "tf"), ("/tf_static", "tf_static")]

LIFECYCLE_NODES = [
    "controller_server",
    "smoother_server",
    "planner_server",
    "behavior_server",
    "bt_navigator",
    "velocity_smoother",
]


def generate_launch_description() -> LaunchDescription:
    share = FindPackageShare("c3po_perception")
    params = PathJoinSubstitution([share, "config", "nav2_params.yaml"])

    args = [
        DeclareLaunchArgument("lidar_height_m", default_value="1.15"),
        DeclareLaunchArgument("mount_yaw_deg", default_value="0.0"),
        DeclareLaunchArgument("twist_source", default_value="fastlio"),
        # Exposed only so it can be read, not so it can be flipped. The
        # authoritative `autostart: false` is in nav2_params.yaml and the
        # lifecycle manager reads it from there.
        DeclareLaunchArgument("autostart", default_value="false"),
        # WHERE odom/scan COME FROM. "real" is the D3 pipeline and CLAIMS BOTH
        # SHARED SENSORS. "fake" is synthetic and claims nothing, which is what
        # makes it possible to exercise the Nav2 lifecycle, the planner, the
        # controller and the /c3po/cmd_vel path without a sensor window and
        # without taking anything from the other team.
        DeclareLaunchArgument("sources", default_value="real"),
    ]

    perception = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([share, "launch", "perception.launch.py"])),
        launch_arguments={
            "lidar_height_m": LaunchConfiguration("lidar_height_m"),
            "mount_yaw_deg": LaunchConfiguration("mount_yaw_deg"),
            "twist_source": LaunchConfiguration("twist_source"),
        }.items(),
        condition=LaunchConfigurationEquals("sources", "real"),
    )

    fake = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([share, "launch", "fake.launch.py"])),
        condition=LaunchConfigurationEquals("sources", "fake"),
    )

    # THE FAKE STACK NEEDS THIS AND IT IS NOT OPTIONAL.
    #
    # fake.launch.py publishes /odom as a MESSAGE. Nav2's costmaps do not read
    # /odom for placement — they ask TF for global_frame -> robot_base_frame,
    # i.e. odom -> base_footprint. In the real stack g1_odom_tf broadcasts that
    # edge; nothing in fake.launch.py does, and the failure mode is not an
    # error. The costmaps simply never update, the controller reports it cannot
    # transform, and it reads like a Nav2 misconfiguration rather than a
    # missing transform.
    #
    # Identity is correct here: the fake robot sits at the origin and does not
    # move, and FAKE_ODOM's pose is all zeros with an identity quaternion. If
    # the fake ever starts moving, this has to become a node that tracks it —
    # a static publisher would then be quietly lying to the planner.
    fake_tf = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="fake_odom_to_base_footprint",
        arguments=["0", "0", "0", "0", "0", "0", "odom", "base_footprint"],
        output="screen",
        condition=LaunchConfigurationEquals("sources", "fake"),
    )

    nav2_nodes = [
        Node(
            package="nav2_controller",
            executable="controller_server",
            name="controller_server",
            output="screen",
            parameters=[params],
            remappings=TF_REMAPS + [("cmd_vel", "cmd_vel_nav")],
        ),
        Node(
            package="nav2_smoother",
            executable="smoother_server",
            name="smoother_server",
            output="screen",
            parameters=[params],
            remappings=TF_REMAPS,
        ),
        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            output="screen",
            parameters=[params],
            remappings=TF_REMAPS,
        ),
        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            output="screen",
            parameters=[params],
            remappings=TF_REMAPS + [("cmd_vel", "cmd_vel_nav")],
        ),
        Node(
            package="nav2_bt_navigator",
            executable="bt_navigator",
            name="bt_navigator",
            output="screen",
            parameters=[params],
            remappings=TF_REMAPS,
        ),
        Node(
            package="nav2_velocity_smoother",
            executable="velocity_smoother",
            name="velocity_smoother",
            output="screen",
            parameters=[params],
            remappings=TF_REMAPS + [("cmd_vel", "cmd_vel_nav"),
                                    ("cmd_vel_smoothed", "/c3po/cmd_vel")],
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            output="screen",
            # node_names, autostart and the 20 s bond timeout all come from
            # nav2_params.yaml. Listed above as LIFECYCLE_NODES only so this
            # file documents what the manager owns.
            parameters=[params],
        ),
    ]

    return LaunchDescription(args + [perception, fake, fake_tf] + nav2_nodes)
