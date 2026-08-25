"""ROS 2 IDL types this container puts on the wire — one struct, frozen forever.

std_msgs/String is the only type that leaves the vision container. The payload
inside `data` is JSON (see detector.py), which is D7's "absent is not empty"
rule taken to the transport layer: an XCDR1 @final struct has no representation
for a missing field, so a custom .msg could not say "nothing looked" without
inventing a sentinel. JSON has null and missing keys natively.

THE TYPENAME STRING IS THE LOAD-BEARING PART. Cyclone matches a reader to a
writer on topic name AND type name. `std_msgs.msg.dds_.String_` here maps to
`std_msgs::msg::dds_::String_` on the wire — exactly what rmw_cyclonedds_cpp
registers for std_msgs/msg/String — and ROS topic /c3po/objects becomes DDS
`rt/c3po/objects`. A typo does not raise: it delivers nothing, forever, which is
indistinguishable from a detector that sees nothing. That is precisely the
failure mode the heartbeat exists to expose, so do not let a typo reintroduce
it here.

WHY THIS IS DUPLICATED RATHER THAN SHARED, AND WHY THE TWO COPIES MUST STAY IN
LOCKSTEP.  The bridge has its own hand-written copy in
`apps/bridge/src/bridge/sdk/ros_idl.py` (Vector3_/Twist_, and it reuses
unitree_sdk2py's already-installed `String_`). There is no shared package and
there cannot cheaply be one: this file runs under **python3.8 inside the
l4t-jetpack focal container** (JetPack 5's TensorRT bindings hard-depend
`python3 (>= 3.8), python3 (<< 3.9)`), while the bridge runs under **python3.12
on the Jetson host via uv**, with different cyclonedds wheels and no filesystem
overlap. Publishing a shared wheel to bridge that gap would be more machinery
than these five lines.

So: this definition and the bridge's are the SAME SHAPE by convention, checked
by review, not by an import. If either side ever gains a field, changes a
typename, or reorders a member, the other side stops matching **silently**.
Change both in one commit or not at all.

Deliberately NOT `from __future__ import annotations`: the cyclonedds IDL
backend reads these annotations at class-construction time to build the
topic descriptor, and stringified annotations break that. Keep concrete types.
"""

from dataclasses import dataclass

import cyclonedds.idl.annotations as annotate
from cyclonedds import idl
from cyclonedds.idl import types  # noqa: F401  (kept: any added field needs it)


@dataclass
@annotate.final
@annotate.autoid("sequential")
class String_(idl.IdlStruct, typename="std_msgs.msg.dds_.String_"):
    """std_msgs/String. Byte-for-byte the struct idlc emits for it.

    Field order is positional on the wire under @final + sequential autoid.
    There is exactly one field, so there is nothing to reorder — which is half
    of why this type was chosen for a payload that will keep evolving.
    """

    data: str
