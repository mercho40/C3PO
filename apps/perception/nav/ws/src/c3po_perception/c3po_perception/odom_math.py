"""The maths g1_odom_tf hands Nav2: frames, yaw and velocity. Pure: stdlib only.

Split out of g1_odom_tf.py, which imports rclpy at module scope, so the numbers
Nav2's controller steers on can be tested on a laptop. Python 3.8-valid, like
costmap_png.py, so it stays a copy away from the vision tree.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple


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


def flat_pose(px: float, py: float, qx: float, qy: float, qz: float,
              qw: float) -> Tuple[float, float, float]:
    """FAST-LIO's camera_init -> body pose as (x, y, yaw) of base_footprint in odom.

    Re-expressed in odom by composing the static Rx(pi): under (x,y,z) ->
    (x,-y,-z) the position maps directly and the quaternion conjugates in y,z.

    z would be `-position.z + lidar_height_m`, and it is deliberately NOT
    computed: base_footprint is the flat frame by definition, published with
    translation.z = 0.0, and Nav2's costmaps are 2-D. Carrying a height here
    would be a value nothing consumes and everything could misinterpret. Same
    reason roll and pitch are discarded — the footprint frame is yaw-only, so a
    pitching torso must not rotate the costmap.
    """
    ox, oy, oz, ow = qx, -qy, -qz, qw
    _roll, _pitch, yaw = quat_to_rpy(ox, oy, oz, ow)
    return px, -py, yaw


def fastlio_twist(vx: float, vy: float, wz: float) -> List[float]:
    """The patched FAST-LIO twist as [vx, vy, wz] of base_footprint.

    Already body-frame from the patch; body is roll-pi from base_link, so y and
    z flip.
    """
    return [vx, -vy, -wz]


def differentiated_twist(
    v: List[float],
    last: Optional[Sequence[float]],
    now: float,
    x: float,
    y: float,
    yaw: float,
) -> List[float]:
    """twist_source="differentiate": the next [vx, vy, wz] estimate. The degraded path.

    `last` is the previous (t, x, y, yaw), None before the first sample; `v`
    is the previous estimate. Velocity is rotated into the CURRENT heading,
    because nav_msgs/Odometry.twist is in child_frame_id: a world-frame
    derivative would be right only facing +x.
    """
    if last is None:
        return v
    t0, x0, y0, yaw0 = last
    dt = now - t0
    if 1e-3 < dt < 0.5:
        dyaw = math.atan2(math.sin(yaw - yaw0), math.cos(yaw - yaw0))
        c, s = math.cos(yaw), math.sin(yaw)
        a = 0.35
        return [v[0] + a * (((x - x0) * c + (y - y0) * s) / dt - v[0]),
                v[1] + a * ((-(x - x0) * s + (y - y0) * c) / dt - v[1]),
                v[2] + a * (dyaw / dt - v[2])]
    if dt >= 0.5:
        # A GAP LONGER THAN HALF A SECOND MAKES THE OLD VELOCITY A LIE.
        #
        # This used to leave the estimate untouched, so the value from before
        # the stall went out on a message stamped NOW and Nav2's
        # controller_server read it as the robot's present velocity. That is
        # "a failed tick republishing the last good value without ageing it" —
        # the rule `detector.py` and `stream.py` both state, applied to a
        # number instead of a picture.
        #
        # Zero rather than hold: SET_VELOCITY carries a 1 s firmware deadman,
        # so after a gap this long the robot has almost certainly stopped, and
        # zero is the closer estimate. `dt` too SMALL is left alone
        # deliberately — that is two samples in the same millisecond, where
        # the old estimate is still the best one available and dividing by dt
        # would explode.
        return [0.0, 0.0, 0.0]
    return v
