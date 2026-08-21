"""Stage 5's bring-up: the LiDAR, the SLAM, the frames, the 2D projection.

    livox_ros_driver2 -> /livox/lidar (CustomMsg) + /livox/imu
    fast_lio          -> /Odometry, /cloud_registered_body, TF camera_init->body
    g1_odom_tf        -> TF odom->base_footprint (+ the static mount TFs), /odom
    pointcloud_to_laserscan -> /scan in base_footprint

THIS STAGE CLAIMS THE LIVOX. It is the first stage that needs a shared-sensor
window with the other team; `perception_up odometry` stops gemm to get it.

WHERE THE UPSIDE-DOWN MOUNT IS CORRECTED, AND WHY IT IS HERE
Nothing in MID360_config.json rotates (the driver's extrinsic applies to the
cloud only, never to the IMU — see apps/perception/README.md, refuted-claims
row 1), and nothing in fastlio_mid360_g1.yaml
rotates (extrinsic_R is identity so the LiDAR<-IMU geometry stays exactly what
the datasheet says). The whole correction is one static TF, odom -> camera_init
with roll = pi and z = the LiDAR height, and it is owned by THIS layer: the
numbers below are the launch file's, and g1_odom_tf's static broadcaster is what
puts them on the wire.

It is deliberately NOT a second static_transform_publisher node here. Two
authorities for one static transform do not error — the buffer takes whichever
arrived last, and the tree flickers between two mount corrections. g1_odom_tf
already publishes odom->camera_init, body->base_link and body->livox_frame in
one shot precisely so there is a single writer, and it needs `lidar_height_m`
anyway to put the floor at z = 0 in its dynamic odom->base_footprint. Passing
the constant here and letting that node broadcast it keeps one number in one
place.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import LaunchConfigurationEquals
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

# base_link's origin expressed in the `body` (inverted IMU) frame: x forward,
# y RIGHT, z DOWN. PLACEHOLDER — Stage 6 measures it. Kept as a Python constant
# rather than a launch argument because a double-array argument would arrive as
# a string and need parsing, which is a third place for the sign to go wrong.
BASE_IN_BODY_XYZ = [-0.10, 0.0, 0.45]


def generate_launch_description() -> LaunchDescription:
    share = FindPackageShare("c3po_perception")
    mid360_json = PathJoinSubstitution([share, "config", "MID360_config.json"])
    fastlio_yaml = PathJoinSubstitution([share, "config", "fastlio_mid360_g1.yaml"])
    p2l_yaml = PathJoinSubstitution([share, "config", "pointcloud_to_laserscan.yaml"])

    args = [
        # MEASURE THIS (apps/perception/README.md, decisions list). Height of
        # the Mid-360's internal IMU above the
        # floor with the robot standing in its nominal posture. camera_init IS
        # the IMU pose at initialisation, so this is what makes "z = 0" mean
        # "the floor" for every costmap height filter downstream.
        DeclareLaunchArgument("lidar_height_m", default_value="1.15"),
        DeclareLaunchArgument("mount_yaw_deg", default_value="0.0"),
        # "fastlio" requires patches/fastlio-publish-twist.patch to have
        # applied. "differentiate" is the degraded fallback and is documented,
        # not recommended — read g1_odom_tf's docstring before setting it.
        DeclareLaunchArgument("twist_source", default_value="fastlio"),
        # WHERE THE POINTS COME FROM. Defaults to the vendor republish because
        # that is the one that WORKS: our own driver does not get the sensor
        # while the vendor `lidar_driver` service holds it — the command channel
        # connects, the config push looks fine, and the points keep going to the
        # control board (ROBOT-HARDWARE.md §4.3, measured).
        #
        #   republish  domain_bridge carries rt/utlidar/* from domain 0 to 42.
        #              Multi-consumer, so the co-tenant keeps its LiDAR, and
        #              nothing is written to the sensor's flash.
        #   driver     our own livox_ros_driver2, which TAKES the unicast. Only
        #              works with the vendor service switched off, and mutates
        #              the sensor's persistent config to do it.
        DeclareLaunchArgument("lidar_source", default_value="republish"),
    ]

    # The bridge: domain 0 -> 42, two named topics, one way. It replaces the
    # driver rather than joining it — two sources on one FAST-LIO input would
    # interleave clouds from different clocks.
    lidar_bridge = Node(
        package="domain_bridge",
        executable="domain_bridge",
        name="c3po_lidar_bridge",
        output="screen",
        arguments=[PathJoinSubstitution(
            [share, "config", "lidar_domain_bridge.yaml"])],
        condition=LaunchConfigurationEquals("lidar_source", "republish"),
    )

    livox = Node(
        condition=LaunchConfigurationEquals("lidar_source", "driver"),
        package="livox_ros_driver2",
        executable="livox_ros_driver2_node",
        name="livox_lidar_publisher",
        output="screen",
        parameters=[{
            # 1 = CustomMsg. FAST-LIO REQUIRES it: only CustomMsg carries the
            # per-point offset_time the motion undistortion runs on. With
            # xfer_format 0 the livox callback never fires and the node sits
            # silent with no error.
            "xfer_format": 1,
            "multi_topic": 0,        # one topic, one sensor
            "data_src": 0,           # 0 = live lidar (1 = lvx replay)
            "publish_freq": 10.0,    # must equal preprocess.scan_rate
            "output_data_type": 0,
            "frame_id": "livox_frame",
            "lvx_file_path": "",
            "cmdline_input_bd_code": "livox0000000001",
            # Its host_net_info must name THIS Jetson's eth0 (192.168.123.164).
            # Wrong address => the command channel connects, the driver reports
            # nothing wrong, and not one point is ever published.
            "user_config_path": mid360_json,
        }],
    )

    # Two configs, because the two sources differ in message type, topic names
    # and timestamp units — not merely in where the bytes come from.
    fast_lio_republish = Node(
        condition=LaunchConfigurationEquals("lidar_source", "republish"),
        package="fast_lio",
        executable="fastlio_mapping",
        name="laserMapping",
        output="screen",
        parameters=[PathJoinSubstitution(
            [share, "config", "fastlio_utlidar_g1.yaml"]), {"use_sim_time": False}],
    )

    fast_lio = Node(
        condition=LaunchConfigurationEquals("lidar_source", "driver"),
        package="fast_lio",
        executable="fastlio_mapping",
        name="laserMapping",
        output="screen",
        parameters=[fastlio_yaml, {"use_sim_time": False}],
    )

    odom_tf = Node(
        package="c3po_perception",
        executable="g1_odom_tf",
        name="g1_odom_tf",
        output="screen",
        parameters=[{
            "lidar_height_m": ParameterValue(
                LaunchConfiguration("lidar_height_m"), value_type=float),
            "mount_yaw_deg": ParameterValue(
                LaunchConfiguration("mount_yaw_deg"), value_type=float),
            "base_in_body_xyz": BASE_IN_BODY_XYZ,
            "twist_source": ParameterValue(
                LaunchConfiguration("twist_source"), value_type=str),
        }],
    )

    # Ground removal + 2D projection. Not a costmap voxel_layer: filtering the
    # floor out of a PointCloud2 costmap source also deletes the rays that would
    # have CLEARED free floor, so that source can mark and never un-mark and the
    # local costmap fills with ghosts. See the config header.
    to_scan = Node(
        package="pointcloud_to_laserscan",
        executable="pointcloud_to_laserscan_node",
        name="mid360_to_scan",     # must match the top-level key in the YAML
        output="screen",
        parameters=[p2l_yaml],
        remappings=[
            # The deskewed BODY-frame cloud, which exists only because
            # publish.scan_bodyframe_pub_en is true in fastlio_mid360_g1.yaml.
            ("cloud_in", "/cloud_registered_body"),
            ("scan", "/scan"),
        ],
    )

    return LaunchDescription(
        args
        + [lidar_bridge, livox, fast_lio_republish, fast_lio, odom_tf, to_scan]
    )
