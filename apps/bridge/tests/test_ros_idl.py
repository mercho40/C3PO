"""Tests for `bridge.sdk.ros_idl` — the two strings and the byte layout.

Everything this file asserts fails SILENTLY in production if it regresses:

* a wrong typename does not raise, it just never matches a writer, forever,
  which is indistinguishable from a planner that has nothing to say;
* a reordered field does not raise either, it makes the robot strafe when it
  was told to rotate;
* and `from __future__ import annotations` at the top of an IDL module does not
  break the *import* — the class still constructs, so a REPL check passes. It
  breaks the first `serialize()` / `Topic()`, i.e. on the robot, inside
  `PerceptionLink.start()`, at the moment something is about to move.

So the wire behaviour is exercised here rather than assumed. This needs
`cyclonedds.idl`, which is pure Python and does NOT load the CycloneDDS C
library — no participant is created anywhere in this file.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("cyclonedds.idl")

from bridge.sdk.ros_idl import Twist_, Vector3_  # noqa: E402


def test_the_typenames_are_the_ones_rmw_cyclonedds_registers():
    # ROS topic /c3po/cmd_vel -> DDS rt/c3po/cmd_vel, type
    # geometry_msgs::msg::dds_::Twist_. Both mappings were verified live on this
    # robot with a DCPSPublication builtin-reader spy. A typo here delivers
    # nothing and reports no error.
    assert Twist_.__idl_typename__ == "geometry_msgs.msg.dds_.Twist_"
    assert Vector3_.__idl_typename__ == "geometry_msgs.msg.dds_.Vector3_"


def test_the_module_does_not_stringify_its_annotations():
    # The regression guard for the future-import trap described above: the IDL
    # backend resolves these at class-construction time, so they must stay
    # concrete types, not strings.
    import bridge.sdk.ros_idl as ros_idl

    # The annotations the backend reads must be objects, not strings.
    assert not isinstance(Vector3_.__annotations__["x"], str)
    assert not isinstance(Twist_.__annotations__["linear"], str)

    # And the import that would stringify them must not be in the file. Checked
    # on the source, as a statement at column 0, because the module docstring
    # names the import in prose to explain why it is absent — and because a
    # future import leaves no importable trace once the module's own
    # annotations happen to be resolvable anyway.
    source = pathlib.Path(ros_idl.__file__).read_text()
    assert not any(
        line.startswith("from __future__ import annotations") for line in source.splitlines()
    )


def test_a_twist_serializes_to_the_six_float64s_ros_puts_on_the_wire():
    # 4-byte CDR encapsulation header + 6 * 8 bytes. If this number moves, the
    # struct gained, lost or re-typed a field and the other side stops matching.
    t = Twist_(linear=Vector3_(0.3, 0.0, 0.0), angular=Vector3_(0.0, 0.0, 0.5))
    assert len(t.serialize()) == 4 + 6 * 8


def test_field_order_is_positional_and_linear_comes_first():
    # @final + sequential autoid means layout is positional. Swapping linear and
    # angular, or x and y, is a silent sign/axis bug in the actuation path.
    t = Twist_(linear=Vector3_(1.0, 2.0, 3.0), angular=Vector3_(4.0, 5.0, 6.0))
    back = Twist_.deserialize(t.serialize())

    assert (back.linear.x, back.linear.y, back.linear.z) == (1.0, 2.0, 3.0)
    assert (back.angular.x, back.angular.y, back.angular.z) == (4.0, 5.0, 6.0)
    assert list(Vector3_.__annotations__) == ["x", "y", "z"]
    assert list(Twist_.__annotations__) == ["linear", "angular"]


def test_rep_103_signs_survive_a_round_trip():
    # +x forward, +y LEFT, +angular.z CCW — the same convention as D7's
    # bearing_deg and `turn`'s delta_yaw_radians, so nothing between Nav2 and
    # SET_VELOCITY needs a sign flip.
    t = Twist_(linear=Vector3_(-0.1, 0.2, 0.0), angular=Vector3_(0.0, 0.0, -0.4))
    back = Twist_.deserialize(t.serialize())

    assert back.linear.x == -0.1
    assert back.linear.y == 0.2
    assert back.angular.z == -0.4
