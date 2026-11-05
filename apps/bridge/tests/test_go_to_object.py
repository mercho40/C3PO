"""skills/go_to_object.py: pick the target relative to the robot, then aim walk_to at it."""

from __future__ import annotations

import asyncio
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
    get_sampler and walk_to.run inside its body, so the module attributes are patched.
    The Nav2 relay and the gate are the real links with no DDS behind them (as in
    test_nav_link): silent relay by default, so the camera walk is taken."""
    import json
    import time

    import bridge.mcp_server as server
    from bridge import object_memory
    from bridge.sdk import nav_link, perception_link, state
    from bridge.skills import walk_to
    from bridge.skills.task_runtime import get_registry

    def current_pose():
        p = t.state["pose"]
        return (p["x_meters_world"], p["y_meters_world"], p["yaw_radians_world"])

    t = SimpleNamespace(
        server=server,
        objects=[],
        detector_online=True,
        report_age=0.1,
        memory=[],
        mem_pose=PERCEPTION_POSE,
        state={"pose": {"x_meters_world": 1.0, "y_meters_world": 1.0, "yaw_radians_world": 0.0}},
        pose_at=lambda when: current_pose(),
        walks=[],
        walk_result={"task_id": "walk-1", "status": "running"},
        sent=[],
        now=time.time(),
    )
    t.nav = nav_link.NavLink(publish=lambda d: t.sent.append(json.loads(d)), clock=time.monotonic)
    t.gate = perception_link.PerceptionLink(forward=lambda *v: None, clock=time.monotonic)
    monkeypatch.setattr(
        t.gate,
        "latest_report",
        lambda: ({"detector_online": t.detector_online, "objects": t.objects}, t.report_age),
    )

    async def fake_run(**kwargs):
        t.walks.append(kwargs)
        return t.walk_result

    monkeypatch.setattr(server, "SIM_MODE", "real")
    monkeypatch.setattr(server, "NAV_POLL_S", 0.01)
    monkeypatch.setattr(server, "_memory_pose", lambda: t.mem_pose)
    monkeypatch.setattr(nav_link, "_nav_singleton", t.nav)
    monkeypatch.setattr(perception_link, "_link_singleton", t.gate)
    monkeypatch.setattr(
        object_memory, "get_object_memory", lambda: SimpleNamespace(list_all=lambda: t.memory)
    )
    monkeypatch.setattr(
        state,
        "get_sampler",
        lambda: SimpleNamespace(get_state=lambda: t.state, pose_at=lambda when: t.pose_at(when)),
    )
    monkeypatch.setattr(walk_to, "run", fake_run)
    for old in get_registry().list_active():
        get_registry().cancel(old.task_id)
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
    # No near_m on the wire (an older detector): today's walk, and the model is told so.
    assert out["target"]["range_to"] == "centre" and "clearance_m" not in out
    assert "CENTRE" in out["note"]


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


# --- E2: a live detection's age, and the pose it was seen from ---------------


def aged(age_s, range_m=2.0, bearing_deg=90.0):
    return {**chair(range_m), "bearing_deg": bearing_deg, "age_s": age_s}


def test_a_fresh_detection_is_accepted_with_its_total_age():
    t = g.resolve("chair", [aged(0.3)], [], None, now=0.0, live_age_s=0.2)
    assert t["source"] == "live" and t["age_s"] == pytest.approx(0.5)


def test_the_report_age_adds_to_the_object_age():
    assert g.resolve("chair", [aged(0.6)], [], None, now=0.0, live_age_s=0.1)["source"] == "live"
    stale = g.resolve("chair", [aged(0.6)], [], None, now=0.0, live_age_s=0.5)
    assert stale["error"] == "stale_detection" and stale["in_view"] == ["chair"]


def test_a_detection_without_age_is_accepted_as_before():
    t = g.resolve("chair", [chair(2.0)], [], None, now=0.0, live_age_s=0.9)
    assert t["source"] == "live" and t["age_s"] is None


async def test_the_bearing_is_applied_to_the_pose_at_capture(tool, monkeypatch):
    # Seen from (1, 1) facing +x: 2 m to the left is (1, 3). Since then the robot
    # stepped 0.15 m and turned 30 deg; the target must not move with it.
    import time

    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    tool.objects = [aged(0.3)]
    tool.state = {
        "pose": {
            "x_meters_world": 1.15,
            "y_meters_world": 1.0,
            "yaw_radians_world": math.radians(30),
        }
    }
    asked = []
    tool.pose_at = lambda when: asked.append(when) or (1.0, 1.0, 0.0)
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    walk = tool.walks[0]
    assert (walk["target_x"], walk["target_y"]) == pytest.approx((1.0, 3.0), abs=1e-9)
    assert out["target"]["age_s"] == pytest.approx(0.4)  # 0.3 object + 0.1 report
    assert asked == [pytest.approx(100.0 - 0.4)]  # monotonic now minus the total age


async def test_a_detection_older_than_the_pose_history_is_stale(tool):
    tool.objects = [aged(0.3)]
    tool.pose_at = lambda when: None
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "stale_detection"
    assert tool.walks == []


async def test_an_old_detection_is_refused(tool):
    tool.objects = [aged(1.2)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "stale_detection"
    assert tool.walks == []


async def test_objects_from_an_offline_detector_are_not_targets(tool):
    tool.objects = [chair(2.0)]
    tool.detector_online = False
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["error"] == "not_found"


# --- E3: Follow, and the retarget closure the tool hands walk_to -------------

HERE = (0.0, 0.0, 0.0)  # at the origin facing +x


def test_follow_takes_the_nearest_sighting_within_the_gate():
    f = g.Follow((2.0, 0.0), now=0.0, abort_when_lost=True)
    assert f.update([(2.3, 0.4), (2.1, 0.1)], HERE, now=1.0) == (2.1, 0.1)


def test_follow_ignores_a_jump_and_a_creep_past_the_first_fix():
    f = g.Follow((2.0, 0.0), now=0.0, abort_when_lost=True)
    assert f.update([(3.2, 0.0)], HERE, now=1.0) == (2.0, 0.0)  # 1.2 m jump: another object
    # 0.9 m steps are each inside the gate, but the third is 2.0 m from the first fix.
    assert f.update([(2.9, 0.0)], HERE, now=1.1) == (2.9, 0.0)
    assert f.update([(2.9, 0.9)], HERE, now=1.2) == (2.9, 0.9)
    assert f.update([(2.9, 1.8)], HERE, now=1.3) == (2.9, 0.9)


def test_follow_rechecks_the_range_from_where_the_robot_is():
    f = g.Follow((3.8, 0.0), now=0.0, abort_when_lost=True)
    assert f.update([(4.3, 0.0)], HERE, now=1.0) == (3.8, 0.0)  # 4.3 m > MAX_RANGE_M


def test_follow_is_lost_only_while_the_target_should_be_in_view():
    f = g.Follow((2.0, 0.0), now=0.0, abort_when_lost=True)
    assert f.update([], HERE, now=2.0) == (2.0, 0.0)
    assert f.update([], HERE, now=3.5) is None  # dead ahead at 2 m and missing for 3.5 s


def test_follow_out_of_view_is_not_lost():
    f = g.Follow((0.0, 2.0), now=0.0, abort_when_lost=True)  # 90 deg to the left
    assert f.update([], HERE, now=10.0) == (0.0, 2.0)


def test_follow_close_to_the_target_keeps_the_last_fix():
    # 1.0 m ahead: in object memory's view window, but the pitched camera may crop it.
    f = g.Follow((1.0, 0.0), now=0.0, abort_when_lost=True)
    assert f.update([], HERE, now=10.0) == (1.0, 0.0)


def test_follow_a_memory_target_is_never_lost():
    f = g.Follow((2.0, 0.0), now=0.0, abort_when_lost=False)
    assert f.update([], HERE, now=100.0) == (2.0, 0.0)


async def test_the_walk_re_aims_at_the_moved_object_and_gives_up_when_it_is_gone(tool, monkeypatch):
    import time

    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    # Now at (1.15, 1); every sighting was taken from (1, 1), 0.3 s ago (0.2 + 0.1 report).
    tool.state["pose"]["x_meters_world"] = 1.15
    asked = []
    tool.pose_at = lambda when: (
        asked.append(when) or ((1.0, 1.0, 0.0) if when < 100.0 else (1.15, 1.0, 0.0))
    )
    tool.objects = [aged(0.2, bearing_deg=0.0)]  # (3, 1) from (1, 1) facing +x
    await tool.server.go_to_object(ctx=None, query="silla", stop_distance_m=0.7)
    assert tool.walks[0]["target_x"] == pytest.approx(3.0)
    retarget = tool.walks[0]["retarget"]
    asked.clear()
    tool.objects = [aged(0.2, bearing_deg=10.0)]
    moved = retarget()
    assert moved == pytest.approx(g.world_target(1.0, 1.0, 0.0, 2.0, 10.0))
    assert pytest.approx(100.0 - 0.3) in asked
    tool.objects = []
    monkeypatch.setattr(g, "LOST_AFTER_S", -1.0)
    assert retarget() is None


# --- E4: Nav2 when a person armed the gate; the camera walk otherwise --------


def nav_status(*, ready=True, pose=(5.0, 5.0, math.pi / 2)):
    import json

    return json.dumps(
        {
            "v": 1,
            "stamp_unix": 1.0,
            "session": "s1",
            "global_frame": "odom",
            "nav2_ready": ready,
            "pose": {"x_m": pose[0], "y_m": pose[1], "yaw_rad": pose[2]},
            "goal": None,
        }
    )


async def test_nav2_gets_the_stop_short_goal_in_its_own_frame(tool, monkeypatch):
    from bridge.skills import stop_everything
    from bridge.skills.task_runtime import get_registry

    tool.nav._ingest_status(nav_status())
    tool.gate.enable(reason="operator", ttl_s=60)
    # 2 m dead ahead on vendor odometry; Nav2 has the robot at (5, 5) facing +y.
    tool.objects = [{**chair(2.0), "bearing_deg": 0.0}]
    out = await tool.server.go_to_object(ctx=None, query="silla", stop_distance_m=0.7)

    assert (out["status"], out["via"]) == ("started", "nav2")
    goal = tool.sent[-1]
    assert (goal["x"], goal["y"]) == pytest.approx((5.0, 6.3), abs=1e-9)
    assert goal["frame"] == "odom"
    assert tool.walks == []
    assert tool.gate.is_enabled()  # opened by the person, untouched by the tool

    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: (20, 20))
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    await stop_everything.run()
    await asyncio.sleep(0.05)
    assert get_registry().get(out["task_id"]).status == "cancelled"
    assert {"v": 1, "cancel": True} in tool.sent


@pytest.mark.parametrize(
    ("status", "arm", "reason"),
    [
        (None, True, "nav_relay_offline"),
        (nav_status(ready=False), True, "nav2_inactive"),
        (nav_status(), False, "gate_closed"),
    ],
)
async def test_without_nav2_it_walks_and_says_why(tool, status, arm, reason):
    if status is not None:
        tool.nav._ingest_status(status)
    if arm:
        tool.gate.enable(reason="operator", ttl_s=60)
    tool.objects = [chair(2.0)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert (out["via"], out["nav2_refused"]) == ("walk_to", reason)
    assert len(tool.walks) == 1
    assert tool.sent == []
    assert tool.gate.is_enabled() is arm


async def test_already_within_the_stop_distance_sends_no_goal(tool):
    tool.nav._ingest_status(nav_status())
    tool.gate.enable(reason="operator", ttl_s=60)
    tool.objects = [chair(0.5)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert tool.sent == []
    assert out["via"] == "walk_to" and "nav2_refused" not in out


async def test_nav2_is_not_used_while_the_headset_drives(tool, monkeypatch):
    monkeypatch.setattr("bridge.estop.teleop_is_driving", lambda: True)
    tool.nav._ingest_status(nav_status())
    tool.gate.enable(reason="operator", ttl_s=60)
    tool.objects = [chair(2.0)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert out["nav2_refused"] == "teleop_active"
    assert tool.sent == []  # walk_to itself then refuses with teleop_active


async def test_nav2_is_not_used_while_a_walk_is_running(tool):
    from bridge.skills.task_runtime import get_registry

    walking = get_registry().create("walk_to")
    tool.nav._ingest_status(nav_status())
    tool.gate.enable(reason="operator", ttl_s=60)
    tool.objects = [chair(2.0)]
    try:
        out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    finally:
        walking.status = "completed"  # no skill is looping on it to end it
    assert out["nav2_refused"] == "walk_task_active"
    assert tool.sent == []


# --- near_m: the robot's FRONT stops stop_distance_m from the object's NEAR side ----


def chair_near(range_m, near_m, bearing_deg=90.0):
    return {**chair(range_m), "near_m": near_m, "bearing_deg": bearing_deg}


async def test_a_near_face_walk_aims_at_the_face_and_stops_the_pelvis_body_front_further_back(tool):
    tool.objects = [chair_near(2.0, 1.7)]
    tool.walk_result = {"status": "completed", "result": {"final_distance_m": 0.85}}
    out = await tool.server.go_to_object(ctx=None, query="silla", stop_distance_m=0.7)
    walk = tool.walks[0]
    # From (1, 1) facing +x, the near face 1.7 m to the LEFT is (1, 2.7).
    assert (walk["target_x"], walk["target_y"]) == pytest.approx((1.0, 2.7), abs=1e-9)
    assert walk["stop_distance_m"] == pytest.approx(0.7 + g.BODY_FRONT_M)
    assert out["target"]["near_m"] == 1.7 and "range_to" not in out["target"]
    # final_distance_m is pelvis-to-face; the model is given the front-to-face gap.
    assert out["clearance_m"] == pytest.approx(0.7)
    assert "near side" in out["note"]


async def test_a_near_face_target_further_than_the_cap_by_its_centre_is_not_too_far(tool):
    tool.objects = [chair_near(4.2, 3.9)]
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert "error" not in out and len(tool.walks) == 1


def test_the_stop_is_clamped_so_the_pelvis_never_drives_into_the_face():
    t = {"range_m": 2.0, "near_m": 1.5}
    assert g.approach(t, 0.0) == (1.5, g.MIN_BASE_TO_FACE_M)
    assert g.approach(t, -1.0) == (1.5, g.MIN_BASE_TO_FACE_M)
    assert g.MIN_BASE_TO_FACE_M == pytest.approx(g.BODY_FRONT_M + 0.1)
    assert g.approach({"range_m": 2.0}, 0.7) == (2.0, 0.7)  # centre: as before


async def test_the_near_face_walk_re_aims_only_at_near_face_sightings(tool, monkeypatch):
    import time

    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    tool.pose_at = lambda when: (1.0, 1.0, 0.0)
    tool.objects = [{**chair_near(2.0, 1.8, bearing_deg=0.0), "age_s": 0.2}]
    await tool.server.go_to_object(ctx=None, query="silla", stop_distance_m=0.7)
    assert tool.walks[0]["target_x"] == pytest.approx(2.8)
    retarget = tool.walks[0]["retarget"]
    tool.objects = [{**chair_near(2.0, 1.5, bearing_deg=10.0), "age_s": 0.2}]
    assert retarget() == pytest.approx(g.world_target(1.0, 1.0, 0.0, 1.5, 10.0))
    # A sighting with no near face (the plane fallback) is not mixed in as one.
    tool.objects = [{**chair(1.9), "bearing_deg": 0.0, "age_s": 0.2}]
    assert retarget() == pytest.approx(g.world_target(1.0, 1.0, 0.0, 1.5, 10.0))


async def test_nav2_gets_the_near_face_goal_with_the_body_front(tool):
    tool.nav._ingest_status(nav_status())
    tool.gate.enable(reason="operator", ttl_s=60)
    tool.objects = [chair_near(2.0, 1.6, bearing_deg=0.0)]
    out = await tool.server.go_to_object(ctx=None, query="silla", stop_distance_m=0.7)
    assert out["via"] == "nav2"
    goal = tool.sent[-1]
    # Face 1.6 m ahead; pelvis stops 0.7 + BODY_FRONT_M short of it. Nav2 at (5, 5) facing +y.
    assert (goal["x"], goal["y"]) == pytest.approx(
        (5.0, 5.0 + 1.6 - 0.7 - g.BODY_FRONT_M), abs=1e-9
    )
    from bridge.skills.task_runtime import get_registry

    get_registry().cancel(out["task_id"])  # the goal's watcher would outlive the test
    await asyncio.sleep(0.05)


async def test_already_at_the_face_sends_no_nav2_goal(tool):
    tool.nav._ingest_status(nav_status())
    tool.gate.enable(reason="operator", ttl_s=60)
    tool.objects = [chair_near(1.2, 0.8)]  # 0.8 m <= 0.7 + BODY_FRONT_M
    out = await tool.server.go_to_object(ctx=None, query="chair", stop_distance_m=0.7)
    assert tool.sent == [] and out["via"] == "walk_to"
