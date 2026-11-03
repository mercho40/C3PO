"""skills/go_to_object.py: pick the target relative to the robot, then aim walk_to at it."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from bridge.skills import go_to_object as g


def entry(label, x, y, seen=100.0):
    return SimpleNamespace(label=label, x_m=x, y_m=y, last_seen=seen)


def test_a_spanish_query_finds_the_live_detection():
    objs = [
        {"label": "person", "range_m": 1.2, "bearing_deg": -10, "confidence": 0.9},
        {"label": "chair", "range_m": 3.2, "bearing_deg": 23, "confidence": 0.7},
    ]
    t = g.resolve("silla", objs, [], None, now=100.0)
    assert (t["label"], t["range_m"], t["bearing_deg"], t["source"]) == ("chair", 3.2, 23.0, "live")


def test_memory_is_converted_to_range_and_bearing_from_where_the_robot_is():
    # Robot at (1, 1) facing +y (90 deg); chair remembered 2 m straight ahead of it.
    t = g.resolve("chair", [], [entry("chair", 1.0, 3.0)], {"x_m": 1.0, "y_m": 1.0, "yaw_deg": 90.0}, now=110.0)
    assert t["source"] == "memory"
    assert t["range_m"] == pytest.approx(2.0)
    assert t["bearing_deg"] == pytest.approx(0.0, abs=1e-9)


def test_old_memories_and_missing_objects_are_not_targets():
    t = g.resolve("chair", [], [entry("chair", 1.0, 3.0, seen=0.0)], {"x_m": 0, "y_m": 0, "yaw_deg": 0}, now=10_000.0)
    assert t["error"] == "not_found"


def test_positive_bearing_is_left_in_the_walk_frame():
    # Facing +x, an object 2 m away 90 deg to the LEFT is at +y.
    x, y = g.world_target(0.0, 0.0, 0.0, 2.0, 90.0)
    assert (x, y) == pytest.approx((0.0, 2.0), abs=1e-9)
    x, y = g.world_target(1.0, 1.0, math.pi, 1.0, 0.0)
    assert (x, y) == pytest.approx((0.0, 1.0), abs=1e-9)
