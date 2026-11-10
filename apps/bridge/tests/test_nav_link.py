"""Nav2 goals from the bridge: the link, the tools, and what stop does to them.

No DDS and no container: NavLink takes a `publish` callable (a list here) and
statuses are fed straight into `_ingest_status`, the way perception_link's
tests feed reports. The goal relay's side of the same JSON is tested against
this module in apps/perception/tests/test_nav_goal.py.

What these guard is mostly what must NOT happen: a goal sent through a closed
gate, a tool that opens the gate itself, a goal left active in Nav2 after the
gate closed or the robot was stopped.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

import pytest

os.environ.setdefault("SIM_MODE", "stub")

from bridge.sdk import nav_link as nl  # noqa: E402
from bridge.sdk import perception_link as pl  # noqa: E402
from bridge.skills import landmarks as lm  # noqa: E402
from bridge.skills.task_runtime import get_registry  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def status(goal=None, *, ready=True, frame="odom", session="s1", pose=(0.0, 0.0, 0.0)) -> str:
    return json.dumps(
        {
            "v": 1,
            "stamp_unix": 1.0,
            "session": session,
            "global_frame": frame,
            "nav2_ready": ready,
            "pose": None if pose is None else {"x_m": pose[0], "y_m": pose[1], "yaw_rad": pose[2]},
            "goal": goal,
        }
    )


def goal_rec(goal_id, state, dist=None):
    return {
        "id": goal_id,
        "state": state,
        "distance_remaining_m": dist,
        "eta_s": None,
        "recoveries": 0,
        "nav_time_s": None,
        "reason": None,
    }


@pytest.fixture
def rig(monkeypatch):
    """Real-mode server with a list for a DDS writer and a gate we control."""
    import bridge.mcp_server as server

    sent: list[dict] = []
    clock = Clock()
    nav = nl.NavLink(publish=lambda d: sent.append(json.loads(d)), clock=clock)
    gate = pl.PerceptionLink(forward=lambda *v: None, clock=clock)
    monkeypatch.setattr(nl, "_nav_singleton", nav)
    monkeypatch.setattr(pl, "_link_singleton", gate)
    monkeypatch.setattr(server, "SIM_MODE", "real")
    monkeypatch.setattr(server, "NAV_POLL_S", 0.01)
    for t in get_registry().list_active():
        get_registry().cancel(t.task_id)
    return server, nav, gate, sent, clock


# --- the link ---------------------------------------------------------------


def test_goal_and_cancel_messages():
    assert json.loads(nl.goal_message("nav_1", 1.0, 2.0, 0.5, "map")) == {
        "v": 1,
        "id": "nav_1",
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.5,
        "frame": "map",
    }
    assert json.loads(nl.cancel_message()) == {"v": 1, "cancel": True}
    assert json.loads(nl.cancel_message("nav_1"))["id"] == "nav_1"


def test_a_silent_relay_is_offline_not_idle():
    clock = Clock()
    nav = nl.NavLink(publish=lambda d: None, clock=clock)
    assert nav.latest_status() == (None, None)
    assert nav._ingest_status(status())
    assert nav.latest_status()[0] is not None
    clock.t += nl.STATUS_OFFLINE_AFTER_S + 0.1
    assert nav.latest_status() == (None, None)


@pytest.mark.parametrize(
    "bad",
    [
        "[]",
        "{}",
        '{"v":2,"session":"x"}',
        '{"v":1}',
        '{"v":1,"session":"x","goal":{"id":"a"}}',
        "not json",
    ],
)
def test_a_malformed_status_is_counted_and_dropped(bad):
    nav = nl.NavLink(publish=lambda d: None)
    assert nav._ingest_status(bad) is False
    assert nav.statuses_rejected == 1 and nav.latest_status() == (None, None)


def test_goal_lookup_only_returns_the_goal_asked_about():
    nav = nl.NavLink(publish=lambda d: None)
    nav._ingest_status(status(goal_rec("nav_a", "active")))
    assert nav.goal("nav_a")["state"] == "active"
    assert nav.goal("nav_b") is None


# --- refusals: the gate is never opened from here -----------------------------


async def test_refused_while_the_relay_is_silent(rig):
    server, _nav, _gate, sent, _ = rig
    out = await server.navigate_to(x=0.5, y=0.0)
    assert out["status"] == "refused" and out["reason"] == "nav_relay_offline"
    assert sent == []


async def test_refused_while_nav2_is_inactive(rig):
    server, nav, _gate, sent, _ = rig
    nav._ingest_status(status(ready=False))
    out = await server.navigate_to(x=0.5, y=0.0)
    assert out["reason"] == "nav2_inactive" and "manage_nodes" in out["how_to_fix"]
    assert sent == []


async def test_refused_while_the_gate_is_closed_and_the_gate_stays_closed(rig):
    server, nav, gate, sent, _ = rig
    nav._ingest_status(status())
    out = await server.navigate_to(x=0.5, y=0.0)
    assert out["reason"] == "gate_closed"
    assert "arm_navigation" in out["how_to_fix"]
    assert sent == [] and gate.is_enabled() is False


# --- the happy path, and the ways it ends -----------------------------------


async def _armed(rig):
    server, nav, gate, sent, clock = rig
    nav._ingest_status(status(pose=(1.0, 1.0, 0.0)))
    gate.enable(reason="test", ttl_s=60)
    return server, nav, gate, sent, clock


async def test_a_goal_returns_a_task_at_once_and_completes_on_success(rig):
    server, nav, _gate, sent, _ = await _armed(rig)
    out = await server.navigate_to(x=1.0, y=2.0)
    assert out["status"] == "started" and out["task_id"].startswith("tsk_")
    assert sent[-1]["x"] == 1.0 and sent[-1]["y"] == 2.0
    assert sent[-1]["yaw"] == pytest.approx(1.5708, abs=1e-3)  # faces the direction of travel
    goal_id = out["goal_id"]
    nav._ingest_status(status(goal_rec(goal_id, "active", dist=1.0)))
    await asyncio.sleep(0.05)
    task = get_registry().get(out["task_id"])
    assert task.status == "running" and task.phase == "active"
    nav._ingest_status(status(goal_rec(goal_id, "succeeded", dist=0.0)))
    await asyncio.sleep(0.05)
    assert task.status == "completed" and task.progress == 1.0


async def test_the_gate_closing_mid_goal_cancels_it_in_nav2(rig):
    """A goal left active would walk the robot again on the next arm."""
    server, nav, gate, sent, _ = await _armed(rig)
    out = await server.navigate_to(x=1.0, y=0.0)
    nav._ingest_status(status(goal_rec(out["goal_id"], "active", dist=1.0)))
    gate.disable("ttl")
    await asyncio.sleep(0.05)
    task = get_registry().get(out["task_id"])
    assert task.status == "failed" and task.phase == "gate_closed"
    assert sent[-1] == {"v": 1, "cancel": True, "id": out["goal_id"]}


async def test_refused_while_the_headset_drives(rig, monkeypatch):
    server, _nav, _gate, sent, _ = await _armed(rig)
    monkeypatch.setattr("bridge.estop.teleop_is_driving", lambda: True)
    out = await server.navigate_to(x=1.0, y=0.0)
    assert (out["status"], out["reason"]) == ("refused", "teleop_active")
    assert sent == []


async def test_the_headset_taking_over_mid_goal_cancels_it(rig, monkeypatch):
    server, nav, _gate, sent, _ = await _armed(rig)
    out = await server.navigate_to(x=1.0, y=0.0)
    nav._ingest_status(status(goal_rec(out["goal_id"], "active", dist=1.0)))
    monkeypatch.setattr("bridge.estop.teleop_is_driving", lambda: True)
    await asyncio.sleep(0.05)
    assert get_registry().get(out["task_id"]).phase == "teleop_takeover"
    assert sent[-1] == {"v": 1, "cancel": True, "id": out["goal_id"]}


async def test_cancel_navigation_cancels_the_task_and_the_goal(rig):
    server, nav, _gate, sent, _ = await _armed(rig)
    out = await server.navigate_to(x=1.0, y=0.0)
    nav._ingest_status(status(goal_rec(out["goal_id"], "active")))
    result = server.cancel_navigation()
    assert out["task_id"] in result["cancelled_task_ids"]
    assert {"v": 1, "cancel": True} in sent
    await asyncio.sleep(0.05)
    assert get_registry().get(out["task_id"]).status == "cancelled"


async def test_an_unacknowledged_goal_fails_rather_than_hangs(rig, monkeypatch):
    server, *_ = await _armed(rig)
    monkeypatch.setattr(server, "NAV_ACK_TIMEOUT_S", 0.02)
    out = await server.navigate_to(x=1.0, y=0.0)
    await asyncio.sleep(0.1)
    assert get_registry().get(out["task_id"]).phase == "goal_not_acknowledged"


async def test_stop_everything_cancels_the_nav2_goal(rig, monkeypatch):
    from bridge.skills import stop_everything

    sent = rig[3]
    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: (20, 20))
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    await stop_everything.run()
    assert {"v": 1, "cancel": True} in sent


def test_stop_everything_cancels_before_the_first_await():
    import pathlib

    from bridge.skills import stop_everything

    src = pathlib.Path(stop_everything.__file__).read_text().split("async def run(", 1)[-1]
    assert src.index("nav.cancel(None)") < src.index("await ")


async def test_soft_stop_cancels_the_nav2_goal(rig, monkeypatch):
    """A goal left active walks the robot again as soon as the gate is re-armed."""
    from bridge.skills import stop_everything

    sent = rig[3]
    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kwargs: (20, 20))
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    await stop_everything.soft_stop()
    assert {"v": 1, "cancel": True} in sent


def test_soft_stop_cancels_before_the_first_await():
    from bridge.skills import stop_everything

    src = pathlib.Path(stop_everything.__file__).read_text()
    src = src.split("async def soft_stop(", 1)[-1].split("async def run(", 1)[0]
    assert src.index("nav.cancel(None)") < src.index("await ")


# --- landmarks: only a pose in a frame Nav2 owns ----------------------------


def _landmark(nav_pose):
    return lm.Landmark(
        name="cocina",
        x_meters_world=9.0,
        y_meters_world=9.0,
        yaw_radians_world=0.0,
        saved_at=0.0,
        nav_pose=nav_pose,
    )


def test_nav_target_refuses_what_nav2_would_resolve_elsewhere():
    live_odom = json.loads(status(session="s2"))
    live_map = json.loads(status(frame="map", session="s3"))
    odom_pose = {"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "odom", "session": "s1"}
    map_pose = {"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map", "session": "s1"}

    assert lm.nav_target(_landmark(None), live_odom) == (None, "no_nav_pose")
    assert lm.nav_target(_landmark(odom_pose), None) == (None, "nav_relay_offline")
    assert lm.nav_target(_landmark(odom_pose), live_odom) == (None, "odom_frame_restarted")
    assert lm.nav_target(_landmark(map_pose), live_odom) == (None, "map_not_loaded")
    # The whole point of the saved map: a different session, same place.
    assert lm.nav_target(_landmark(map_pose), live_map) == (map_pose, None)
    same = dict(odom_pose, session="s2")
    assert lm.nav_target(_landmark(same), live_odom) == (same, None)


def test_nav_pose_survives_a_restart_and_old_files_still_load(tmp_path):
    path = tmp_path / "landmarks.json"
    store = lm.LandmarkStore(path=path)
    pose = {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0}
    nav_pose = lm.nav_pose_from_status(json.loads(status(frame="map", pose=(1.0, 2.0, 0.5))))
    store.remember("cocina", pose, nav_pose=nav_pose)
    store.remember("viejo", pose)
    again = lm.LandmarkStore(path=path)
    assert again.recall("cocina").nav_pose == {
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.5,
        "frame": "map",
        "session": "s1",
    }
    assert again.recall("viejo").nav_pose is None


async def test_navigate_to_landmark_sends_the_nav_pose_not_the_vendor_pose(rig, monkeypatch):
    server, nav, _gate, sent, _ = await _armed(rig)
    store = lm.LandmarkStore()
    store.remember(
        "cocina",
        {"x_meters_world": 9.0, "y_meters_world": 9.0, "yaw_radians_world": 0.0},
        nav_pose={"x": 1.5, "y": -0.5, "yaw": 0.3, "frame": "odom", "session": "s1"},
    )
    monkeypatch.setattr(lm, "get_store", lambda: store)
    out = await server.navigate_to_landmark(name="cocina")
    assert out["status"] == "started"
    assert sent[-1] == {
        "v": 1,
        "id": out["goal_id"],
        "x": 1.5,
        "y": -0.5,
        "yaw": 0.3,
        "frame": "odom",
    }
    server.cancel_navigation()


# --- the relay's side of the same JSON (apps/perception/.../nav_goal.py) ----
#
# Nothing on domain 42 type-checks a String: a renamed key is a goal that never
# arrives or a status that always reads offline. nav_goal.py is stdlib-only, so
# it is imported from its source tree rather than mocked.

NAV_PKG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "perception"
    / "nav"
    / "ws"
    / "src"
    / "c3po_perception"
)


@pytest.fixture
def relay():
    sys.path.insert(0, str(NAV_PKG))
    try:
        from c3po_perception import nav_goal

        yield nav_goal
    finally:
        sys.path.remove(str(NAV_PKG))


def test_the_bridge_goal_is_what_the_relay_parses(relay):
    g = relay.parse_goal(json.loads(nl.goal_message("nav_x", 1.5, -0.5, 0.3, "map")))
    assert g == {"cancel": False, "id": "nav_x", "x": 1.5, "y": -0.5, "yaw": 0.3, "frame": "map"}
    assert relay.parse_goal(json.loads(nl.cancel_message())) == {"cancel": True, "id": None}
    assert relay.TERMINAL_STATES == nl.TERMINAL_STATES


def test_the_relay_status_is_what_the_bridge_and_landmarks_accept(relay):
    goal = relay.goal_record(relay.parse_goal({"v": 1, "id": "nav_x", "x": 1, "y": 0}), "map")
    payload = relay.status_payload(
        stamp_unix=1.0,
        session="s",
        global_frame="map",
        nav2_ready=True,
        pose=(1.0, 2.0, 0.5),
        goal=goal,
    )
    link = nl.NavLink(publish=lambda d: None)
    assert link._ingest_status(json.dumps(payload))
    assert link.goal("nav_x")["state"] == "pending"
    assert lm.nav_pose_from_status(payload) == {
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.5,
        "frame": "map",
        "session": "s",
    }
    no_tf = relay.status_payload(
        stamp_unix=1.0, session="s", global_frame="map", nav2_ready=True, pose=None, goal=None
    )
    assert link._ingest_status(json.dumps(no_tf))
    assert lm.nav_pose_from_status(no_tf) is None


async def test_refused_while_the_arms_are_held(rig, monkeypatch):
    from types import SimpleNamespace

    from bridge.teleop import arm_sdk

    server, _nav, _gate, sent, _ = await _armed(rig)
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: SimpleNamespace(engaged=True))
    out = await server.navigate_to(x=1.0, y=0.0)
    assert (out["status"], out["reason"]) == ("refused", "arm_held")
    assert "release_arm_control" in out["how_to_fix"]
    assert sent == []


async def test_refused_while_another_walk_runs(rig):
    server, _nav, _gate, sent, _ = await _armed(rig)
    walking = get_registry().create("walk_to")
    try:
        out = await server.navigate_to(x=1.0, y=0.0)
    finally:
        walking.status = "completed"
    assert (out["status"], out["reason"]) == ("refused", "walk_task_active")
    assert sent == []


async def test_a_new_goal_still_replaces_the_running_one(rig):
    server, nav, _gate, sent, _ = await _armed(rig)
    first = await server.navigate_to(x=1.0, y=0.0)
    second = await server.navigate_to(x=2.0, y=0.0)
    assert second["status"] == "started"
    assert get_registry().get(first["task_id"]).cancel_event.is_set()
    await asyncio.sleep(0.05)
    server.cancel_navigation()
    await asyncio.sleep(0.05)


async def test_operator_velocity_preempting_a_goal_cancels_it_in_nav2(rig, monkeypatch):
    """11.6b: walk_velocity cancels navigate_to's task, and the goal must not stay active in Nav2."""
    from bridge.sdk import g1_rpc
    from bridge.skills import _locomotion, walk_velocity

    server, nav, _gate, sent, _ = await _armed(rig)
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_set_velocity", lambda *a: (0, ""))
    out = await server.navigate_to(x=1.0, y=0.0)
    nav._ingest_status(status(goal_rec(out["goal_id"], "active", dist=1.0)))

    result = await walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=0.1)

    assert result["status"] == "completed"
    assert get_registry().get(out["task_id"]).status == "cancelled"
    assert {"v": 1, "cancel": True, "id": out["goal_id"]} in sent
