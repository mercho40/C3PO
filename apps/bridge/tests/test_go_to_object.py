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
    t = g.resolve(
        "chair",
        [],
        [entry("chair", 1.0, 3.0)],
        {"x_m": 1.0, "y_m": 1.0, "yaw_deg": 90.0},
        now=110.0,
    )
    assert t["source"] == "memory"
    assert t["range_m"] == pytest.approx(2.0)
    assert t["bearing_deg"] == pytest.approx(0.0, abs=1e-9)


def test_old_memories_and_missing_objects_are_not_targets():
    t = g.resolve(
        "chair",
        [],
        [entry("chair", 1.0, 3.0, seen=0.0)],
        {"x_m": 0, "y_m": 0, "yaw_deg": 0},
        now=10_000.0,
    )
    assert t["error"] == "not_found"


def test_positive_bearing_is_left_in_the_walk_frame():
    # Facing +x, an object 2 m away 90 deg to the LEFT is at +y.
    x, y = g.world_target(0.0, 0.0, 0.0, 2.0, 90.0)
    assert (x, y) == pytest.approx((0.0, 2.0), abs=1e-9)
    x, y = g.world_target(1.0, 1.0, math.pi, 1.0, 0.0)
    assert (x, y) == pytest.approx((0.0, 1.0), abs=1e-9)


def test_memory_from_another_frame_is_not_a_target():
    e = entry("chair", 1.0, 3.0)
    e.frame = "odom_before_restart"
    pose = {"x_m": 1.0, "y_m": 1.0, "yaw_deg": 90.0}
    assert g.resolve("chair", [], [e], pose, now=110.0, frame="odom")["error"] == "not_found"
    assert (
        g.resolve("chair", [], [e], pose, now=110.0, frame="odom_before_restart")["source"]
        == "memory"
    )


# --- the tool body (mcp_server.go_to_object), no GovernedMCP gate ------------

PERCEPTION_POSE = ({"x_m": 10.0, "y_m": 10.0, "yaw_deg": 0.0}, "odom")


@pytest.fixture
def tool(monkeypatch):
    """The real server with its inputs replaced. The tool imports get_object_memory,
    get_sampler and walk_to.run inside its body, so the module attributes are patched."""
    import time

    import bridge.mcp_server as server
    from bridge import object_memory
    from bridge.sdk import state
    from bridge.skills import walk_to

    t = SimpleNamespace(
        server=server,
        objects=[],
        memory=[],
        mem_pose=PERCEPTION_POSE,
        state={"pose": {"x_meters_world": 1.0, "y_meters_world": 1.0, "yaw_radians_world": 0.0}},
        walks=[],
        now=time.time(),
    )

    async def fake_run(**kwargs):
        t.walks.append(kwargs)
        return {"task_id": "walk-1", "status": "running"}

    monkeypatch.setattr(server, "SIM_MODE", "real")
    monkeypatch.setattr(server, "surroundings_snapshot", lambda: {"objects": t.objects})
    monkeypatch.setattr(server, "_memory_pose", lambda: t.mem_pose)
    monkeypatch.setattr(
        object_memory, "get_object_memory", lambda: SimpleNamespace(list_all=lambda: t.memory)
    )
    monkeypatch.setattr(state, "get_sampler", lambda: SimpleNamespace(get_state=lambda: t.state))
    monkeypatch.setattr(walk_to, "run", fake_run)
    return t


def chair(range_m):
    return {"label": "chair", "range_m": range_m, "bearing_deg": 90.0, "confidence": 0.8}


async def test_a_live_object_is_aimed_from_the_vendor_pose(tool):
    tool.objects = [chair(2.0)]
    out = await tool.server.go_to_object(ctx=None, query="silla", stop_distance_m=0.7)
    assert len(tool.walks) == 1
    walk = tool.walks[0]
    # Facing +x from (1, 1), 2 m to the LEFT is (1, 3).
    assert (walk["target_x"], walk["target_y"]) == pytest.approx((1.0, 3.0), abs=1e-9)
    assert (walk["stop_distance_m"], walk["timeout_s"], walk["autonomous"]) == (0.7, 60.0, True)
    assert out["target"]["source"] == "live"
    assert out["env"] == "real"


async def test_memory_is_relative_from_perception_then_steered_on_vendor_odometry(tool):
    # Perception says the chair is 2 m dead ahead; the vendor frame faces +y from (0, 0).
    tool.memory = [
        SimpleNamespace(label="chair", x_m=12.0, y_m=10.0, last_seen=tool.now, frame="odom")
    ]
    tool.state = {
        "pose": {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": math.pi / 2}
    }
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    walk = tool.walks[0]
    assert (walk["target_x"], walk["target_y"]) == pytest.approx((0.0, 2.0), abs=1e-9)
    assert out["target"]["source"] == "memory"


async def test_memory_from_another_frame_does_not_walk(tool):
    tool.memory = [
        SimpleNamespace(label="chair", x_m=12.0, y_m=10.0, last_seen=tool.now, frame="odom_old")
    ]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "not_found"
    assert tool.walks == []


async def test_a_far_object_is_refused_with_its_target(tool):
    tool.objects = [chair(5.0)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "too_far"
    assert out["target"]["range_m"] == 5.0
    assert tool.walks == []


async def test_no_odometry_pose_does_not_walk(tool):
    tool.objects = [chair(2.0)]
    tool.state = {}
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "no_pose"
    assert tool.walks == []


async def test_stub_mode_is_not_applicable(tool, monkeypatch):
    monkeypatch.setattr(tool.server, "SIM_MODE", "stub")
    tool.objects = [chair(2.0)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "not_applicable"
    assert tool.walks == []
