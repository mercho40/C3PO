#!/usr/bin/env python3
"""Turn FAST-LIO2's output into something Nav2 can navigate on.

FAST-LIO2 publishes TF `camera_init -> body` and Odometry with
frame_id="camera_init", child_frame_id="body". Those names are hard-coded string
literals in laserMapping.cpp — there is no frame_id parameter. And `body` is the
Mid-360's IMU frame, which on this robot is mounted UPSIDE DOWN and pitches with
the pelvis on every step.

So the tree we publish is:

    odom --(static, roll=pi, z=h)--> camera_init --(FAST-LIO)--> body
                                                                  |
                                             --(static, roll=pi, mount)--> base_link
    odom --(this node, dynamic: x, y, yaw; z=0)--> base_footprint

`base_link` and `base_footprint` are siblings under `odom`, which is
unconventional (URDF convention parents base_link to base_footprint) and is
correct here: base_footprint is a PROJECTION of base_link, not a joint, and we
have no URDF and no robot_state_publisher. TF resolves any pair in a connected
tree, so Nav2 gets `odom -> base_footprint` and the costmap gets
`livox_frame -> odom`.

Why the flat frame matters: nav2_costmap_2d's ObservationBuffer::bufferCloud
transforms the cloud into the costmap's global_frame and THEN compares each
point's z against min/max_obstacle_height. So "metres above the floor" is only
true if that frame is gravity-aligned and floor-referenced. On a biped whose
pelvis pitches every step, a base_link-referenced costmap makes the height
filter oscillate with the gait and the floor intermittently become an obstacle.

THE TWIST. FAST-LIO's publish_odometry() sets pose and pose.covariance and never
touches twist. Nav2's controller_server reads the robot's ACTUAL velocity from
exactly that field (nav_2d_utils::OdomSubscriber). Left at zero, DWB samples a
velocity space anchored at standstill every cycle. We do NOT fix this by
differentiating the pose: nav_msgs/Odometry.twist is specified in
child_frame_id, so a world-frame derivative would be correct only when the robot
faces +x and swapped/sign-flipped at 90/180 degrees of yaw — a silent,
heading-dependent failure that reads as "the controller is unstable". Instead
patches/fastlio-publish-twist.patch fills twist from the IESKF state velocity
rotated into the body frame, and this node just re-expresses it.
"""

from __future__ import annotations

import math

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from tf2_ros import TransformBroadcaster
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster


def quat_to_rpy(x, y, z, w):
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


def rpy_to_quat(roll, pitch, yaw):
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    return (sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy)


# Rx(pi) as a quaternion. The mount correction, in one constant.
RX180 = (1.0, 0.0, 0.0, 0.0)


class G1OdomTf(Node):
    def __init__(self) -> None:
        super().__init__("g1_odom_tf")

        # MEASURE THESE. Height of the Mid-360's internal IMU above the floor
        # with the robot standing in its nominal posture. FAST-LIO's camera_init
        # origin IS the IMU pose at initialisation, so this offset is what makes
        # z == 0 mean "the floor" for every costmap height filter downstream.
        self.declare_parameter("lidar_height_m", 1.15)
        # base_link origin expressed in the `body` (inverted IMU) frame, i.e.
        # x forward, y RIGHT, z DOWN. Pelvis below and behind a head-mounted
        # sensor => negative x, positive z. PLACEHOLDERS.
        self.declare_parameter("base_in_body_xyz", [-0.10, 0.0, 0.45])
        self.declare_parameter("mount_yaw_deg", 0.0)
        # If the FAST-LIO twist patch is not applied, set this to "differentiate"
        # and read the docstring first. Degraded, not equivalent.
        self.declare_parameter("twist_source", "fastlio")

        self.h = float(self.get_parameter("lidar_height_m").value)
        self.twist_source = self.get_parameter("twist_source").value

        self.tf_bc = TransformBroadcaster(self)
        self.static_bc = StaticTransformBroadcaster(self)
        self._publish_static()

        self.pub = self.create_publisher(Odometry, "odom", 20)
        self.create_subscription(
            Odometry, "/Odometry", self.on_odom,
            QoSProfile(depth=20, reliability=ReliabilityPolicy.RELIABLE),
        )
        self._last = None
        self._v = [0.0, 0.0, 0.0]

        if self.twist_source != "fastlio":
            self.get_logger().warning(
                "twist_source=%s — Nav2 will be fed a differentiated velocity. "
                "This is the degraded path; verify the FAST-LIO patch instead."
                % self.twist_source)

    def _publish_static(self) -> None:
        """The mount correction, published once.

        odom -> camera_init carries the roll=pi that un-inverts the sensor's
        world frame, plus the z offset that puts the floor at z=0. It is only
        exactly right if the robot was LEVEL when FAST-LIO initialised —
        camera_init inherits the pelvis attitude at t=0 either way, so
        initialise standing still and level. (This is a property of FAST-LIO's
        design, not of this correction.)
        """
        h = self.h
        mount = [float(v) for v in self.get_parameter("base_in_body_xyz").value]
        myaw = math.radians(float(self.get_parameter("mount_yaw_deg").value))

        t1 = TransformStamped()
        t1.header.stamp = self.get_clock().now().to_msg()
        t1.header.frame_id = "odom"
        t1.child_frame_id = "camera_init"
        t1.transform.translation.z = h
        (t1.transform.rotation.x, t1.transform.rotation.y,
         t1.transform.rotation.z, t1.transform.rotation.w) = RX180

        t2 = TransformStamped()
        t2.header.stamp = t1.header.stamp
        t2.header.frame_id = "body"
        t2.child_frame_id = "base_link"
        t2.transform.translation.x = mount[0]
        t2.transform.translation.y = mount[1]
        t2.transform.translation.z = mount[2]
        qx, qy, qz, qw = rpy_to_quat(math.pi, 0.0, myaw)
        (t2.transform.rotation.x, t2.transform.rotation.y,
         t2.transform.rotation.z, t2.transform.rotation.w) = qx, qy, qz, qw

        # The LiDAR is the origin of `body`'s parent chain; livox_frame is
        # co-located with the sensor, i.e. identity relative to `body` for our
        # purposes. camera_link is the D435i mount — MEASURE IT, see the
        # detector, which holds the same number as a constant.
        t3 = TransformStamped()
        t3.header.stamp = t1.header.stamp
        t3.header.frame_id = "body"
        t3.child_frame_id = "livox_frame"
        t3.transform.rotation.w = 1.0

        self.static_bc.sendTransform([t1, t2, t3])

    def on_odom(self, msg: Odometry) -> None:
        q = msg.pose.pose.orientation
        # msg is camera_init -> body. Re-express in odom by composing the static
        # Rx(pi): under (x,y,z) -> (x,-y,-z) the position maps directly and the
        # quaternion conjugates in y,z.
        x = msg.pose.pose.position.x
        y = -msg.pose.pose.position.y
        # z would be `-position.z + self.h`, and it is deliberately NOT computed:
        # base_footprint is the flat frame by definition, published below with
        # translation.z = 0.0, and Nav2's costmaps are 2-D. Carrying a height
        # here would be a value nothing consumes and everything could
        # misinterpret. Same reason roll and pitch are discarded — the footprint
        # frame is yaw-only, so a pitching torso must not rotate the costmap.
        ox, oy, oz, ow = q.x, -q.y, -q.z, q.w
        _roll, _pitch, yaw = quat_to_rpy(ox, oy, oz, ow)

        now = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self.twist_source == "fastlio":
            # Already body-frame from the patch; body is roll-pi from base_link,
            # so y and z flip.
            self._v = [msg.twist.twist.linear.x,
                       -msg.twist.twist.linear.y,
                       -msg.twist.twist.angular.z]
        elif self._last is not None:
            t0, x0, y0, yaw0 = self._last
            dt = now - t0
            if 1e-3 < dt < 0.5:
                dyaw = math.atan2(math.sin(yaw - yaw0), math.cos(yaw - yaw0))
                c, s = math.cos(yaw), math.sin(yaw)
                a = 0.35
                self._v[0] += a * (((x - x0) * c + (y - y0) * s) / dt - self._v[0])
                self._v[1] += a * ((-(x - x0) * s + (y - y0) * c) / dt - self._v[1])
                self._v[2] += a * (dyaw / dt - self._v[2])
        self._last = (now, x, y, yaw)

        tf = TransformStamped()
        tf.header.stamp = msg.header.stamp
        tf.header.frame_id = "odom"
        tf.child_frame_id = "base_footprint"
        tf.transform.translation.x = x
        tf.transform.translation.y = y
        tf.transform.translation.z = 0.0
        fx, fy, fz, fw = rpy_to_quat(0.0, 0.0, yaw)
        (tf.transform.rotation.x, tf.transform.rotation.y,
         tf.transform.rotation.z, tf.transform.rotation.w) = fx, fy, fz, fw
        self.tf_bc.sendTransform(tf)

        out = Odometry()
        out.header.stamp = msg.header.stamp
        out.header.frame_id = "odom"
        out.child_frame_id = "base_footprint"
        out.pose.pose.position.x = x
        out.pose.pose.position.y = y
        out.pose.pose.orientation.x = fx
        out.pose.pose.orientation.y = fy
        out.pose.pose.orientation.z = fz
        out.pose.pose.orientation.w = fw
        out.pose.covariance = msg.pose.covariance
        out.twist.twist.linear.x = self._v[0]
        out.twist.twist.linear.y = self._v[1]
        out.twist.twist.angular.z = self._v[2]
        self.pub.publish(out)


def main() -> None:
    rclpy.init()
    node = G1OdomTf()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
