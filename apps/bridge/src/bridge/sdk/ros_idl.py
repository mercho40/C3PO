"""Hand-written ROS 2 IDL types — the whole of what D2.2 costs us.

One rule decides what belongs here: a type earns an IDL definition only if its
shape is FIXED and FROZEN. geometry_msgs/Twist qualifies — six float64s,
unchanged since 2010, no strings, no optionals, in the actuation path at 20 Hz.
The world-model summary does not, and crosses as JSON inside a
`std_msgs::msg::dds_::String_` — a type we already have installed.

THE TYPENAME STRINGS ARE THE LOAD-BEARING PART. Cyclone matches a reader to a
writer on topic name AND type name. `geometry_msgs.msg.dds_.Twist_` here maps to
`geometry_msgs::msg::dds_::Twist_` on the wire, which is what rmw_cyclonedds_cpp
registers for geometry_msgs/msg/Twist, and ROS topic /c3po/cmd_vel becomes DDS
`rt/c3po/cmd_vel`. A typo does not raise — it delivers nothing, forever, which
is indistinguishable from a quiet planner. Both mappings were verified live on
this robot via a DCPSPublication builtin-reader spy.

Field ORDER is equally load-bearing and equally silent: @final + sequential
autoid means the wire layout is positional. linear before angular, x/y/z in that
order. Swapping two doubles makes the robot strafe when told to rotate.

Nav2 on HUMBLE publishes bare Twist, not TwistStamped (verified against
nav2_velocity_smoother 1.1.20's velocity_smoother.hpp on this robot). The
stamped variant is a Jazzy option and a Kilted default. If this ever becomes
TwistStamped, the reader stops matching SILENTLY.

WHY THIS IS DUPLICATED RATHER THAN SHARED. The vision container has its own
hand-written copy in `apps/perception/vision/c3po_vision/ros_idl.py` (String_).
There is no shared package and there cannot cheaply be one: that file runs under
python3.8 inside the l4t-jetpack focal container, this one under python3.12 on
the Jetson host via uv, with different cyclonedds wheels and no filesystem
overlap. The two are the SAME SHAPE by convention, checked by review, not by an
import. If either side gains a field, changes a typename, or reorders a member,
the other stops matching **silently**. Change both in one commit or not at all.

Deliberately NOT `from __future__ import annotations`: the cyclonedds IDL
backend reads these annotations at class-construction time to build the topic
descriptor, and PEP 563 stringified annotations break that. With the future
import present, `Twist_` still *constructs* — it only explodes the first time
something serializes it or builds a Topic from it, i.e. on the robot, at
`start()`, and never in a REPL that just imported the class:

    TypeError: Type types.float64 as used in bridge.sdk.ros_idl cannot be
    resolved.

Keep the concrete types. Do not add the future import back for style.
"""

from dataclasses import dataclass

import cyclonedds.idl as idl
import cyclonedds.idl.annotations as annotate
import cyclonedds.idl.types as types


@dataclass
@annotate.final
@annotate.autoid("sequential")
class Vector3_(idl.IdlStruct, typename="geometry_msgs.msg.dds_.Vector3_"):
    x: types.float64
    y: types.float64
    z: types.float64


@dataclass
@annotate.final
@annotate.autoid("sequential")
class Twist_(idl.IdlStruct, typename="geometry_msgs.msg.dds_.Twist_"):
    """Body-frame velocity, REP-103: +x forward, +y LEFT, +angular.z CCW.

    Same sign convention as D7's bearing_deg and `turn`'s delta_yaw_radians, so
    no sign flip is needed anywhere between Nav2 and SET_VELOCITY (7105). Verify
    it on the first supervised run anyway — no non-zero SET_VELOCITY has ever
    executed on this robot.

    Twist carries NO timestamp, which is why the consumer stamps arrival time
    itself and runs its own staleness deadman.
    """

    linear: Vector3_
    angular: Vector3_
