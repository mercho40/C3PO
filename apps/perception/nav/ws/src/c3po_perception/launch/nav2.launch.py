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
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
)
from launch.conditions import LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

# /tf and /tf_static are global; every Nav2 node gets these so a namespace
# (which we do not use today) could never split the tree in half.
TF_REMAPS = [("/tf", "tf"), ("/tf_static", "tf_static")]

# An EMPTY but well-formed PointCloud2, for the fake stack's /cloud_registered_body.
#
# The fields are declared and the cloud carries ZERO points. Both halves matter.
# nav2_costmap_2d's ObservationBuffer::bufferCloud() transforms the cloud through
# tf2, which iterates "x"/"y"/"z" by name — a cloud with no `fields` raises
# std::runtime_error inside the buffer rather than being treated as empty. With
# the fields present and width 0 the iterators are simply never dereferenced.
#
# Zero points is the CORRECT fake here, not a shortcut: this source is
# marking-only (`clearing: false` in nav2_params.yaml), so an empty cloud marks
# nothing and every synthetic obstacle keeps coming from /scan, which is the one
# source that can also clear. A fake cloud carrying invented points would paint
# obstacles no fake scan ever clears, and the costmap would silt up.
FAKE_CLOUD = (
    "{header: {stamp: now, frame_id: livox_frame}, "
    "height: 1, width: 0, "
    "fields: ["
    "{name: x, offset: 0, datatype: 7, count: 1}, "
    "{name: y, offset: 4, datatype: 7, count: 1}, "
    "{name: z, offset: 8, datatype: 7, count: 1}, "
    "{name: intensity, offset: 12, datatype: 7, count: 1}], "
    "is_bigendian: false, point_step: 16, row_step: 0, "
    "data: [], is_dense: true}"
)

# Matches fake.launch.py's FAKE_HZ. The binding constraint is nav2_params.yaml's
# `expected_update_rate: 0.5` on both observation sources: anything at or below
# 2 Hz makes the buffer report stale and the controller abort.
FAKE_CLOUD_HZ = "4"

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

    # Behaviour trees, resolved HERE and not in the YAML. A parameter file is
    # read literally — `$(find-pkg-share ...)` in it reaches bt_navigator as a
    # dollar sign and a filename. Substitutions only expand in a launch file,
    # so the absolute paths are built here and passed as an override.
    #
    # Both are ours rather than upstream's: behavior_plugins is ["wait"], and
    # bt_navigator resolves every action server its trees name at LOAD time, so
    # the stock trees' <Spin>/<BackUp> abort bringup for behaviours we
    # deliberately never run. See the trees' own headers.
    bt_to_pose = PathJoinSubstitution(
        [share, "behavior_trees", "navigate_to_pose_biped.xml"])
    bt_through_poses = PathJoinSubstitution(
        [share, "behavior_trees", "navigate_through_poses_biped.xml"])

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

    # THE FAKE STACK NEEDS THESE TWO AS WELL, FOR THE SAME REASON.
    #
    # local_costmap's obstacle_layer runs `observation_sources: scan cloud`, and
    # the `cloud` source is /cloud_registered_body — FAST-LIO's output, which
    # exists only in the real pipeline. Under `sources:=fake` nothing publishes
    # it, so that observation buffer is never updated and the controller refuses
    # to produce a command:
    #
    #   local_costmap: The /cloud_registered_body observation buffer has not been
    #                  updated for 171.00 seconds, ...
    #   controller_server: [follow_path] [ActionServer] Aborting handle.
    #
    # Note where that surfaced: the goal was ACCEPTED and PLANNED. global_costmap
    # lists only `scan`, so planning succeeded and just the controller starved —
    # which reads like a controller tuning problem rather than a missing topic.
    #
    # Faking the topic is deliberate, rather than trimming `cloud` out of
    # observation_sources for fake runs. Rewriting the costmap config would mean
    # nav2-fake exercised a DIFFERENT configuration than the real stack, so the
    # rehearsal would stop covering the thing it exists to rehearse — including
    # the livox_frame lookup below, which is a real failure mode.
    fake_cloud = ExecuteProcess(
        cmd=["ros2", "topic", "pub", "-r", FAKE_CLOUD_HZ,
             "/cloud_registered_body", "sensor_msgs/msg/PointCloud2", FAKE_CLOUD],
        name="fake_cloud",
        output="screen",
        condition=LaunchConfigurationEquals("sources", "fake"),
    )

    # ...and the frame that cloud names. `sensor_frame: livox_frame` is the
    # raytrace ORIGIN, so the buffer must be able to resolve livox_frame -> odom
    # or every cloud is dropped on a transform exception — the identical symptom
    # as publishing nothing at all.
    #
    # In the real stack g1_odom_tf broadcasts this via odom -> body -> livox_frame.
    # The fake collapses that chain to a single static edge because the fake robot
    # is bolted to the origin; `body` and `base_link` have no other subscriber
    # here. If the fake ever starts moving, this and fake_tf both have to become
    # real broadcasters together.
    fake_livox_tf = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="fake_base_footprint_to_livox",
        arguments=["0", "0", LaunchConfiguration("lidar_height_m"),
                   "0", "0", "0", "base_footprint", "livox_frame"],
        output="screen",
        condition=LaunchConfigurationEquals("sources", "fake"),
    )

    # Nav2's global costmap -> a sub-kB PNG on /c3po/costmap, for the operator
    # console. It lives HERE and not in perception.launch.py because there is no
    # costmap until Nav2 runs — global_costmap belongs to planner_server. That
    # also means `nav2-fake` produces a real (if sparse) costmap from the
    # synthetic scan, so the whole map path can be built and rendered before the
    # Livox is ever claimed.
    costmap = Node(
        package="c3po_perception",
        executable="costmap_publisher",
        name="costmap_publisher",
        output="screen",
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
            parameters=[params, {
                "default_nav_to_pose_bt_xml": bt_to_pose,
                "default_nav_through_poses_bt_xml": bt_through_poses,
            }],
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

    return LaunchDescription(
        args
        + [perception, fake, fake_tf, fake_cloud, fake_livox_tf, costmap]
        + nav2_nodes
    )
