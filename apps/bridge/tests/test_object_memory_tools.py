"""The object-memory MCP tools and their wiring (PLAN Phase 4.1-4.2).

The store itself is covered in test_object_memory.py. These pin what the agent
sees: recall in Spanish with distance/bearing from here, the memory section of
describe_surroundings, look_around stopping when it must, and — the bug class
this repo keeps meeting — that the memory is actually fed by the perception link.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import bridge.mcp_server as server
from bridge import estop
from bridge import object_memory as om
from bridge.sdk import perception_link as pl

ORIGIN = {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 0.0}


@pytest.fixture
def mem(monkeypatch):
    memory = om.ObjectMemory()
    monkeypatch.setattr(om, "_memory_singleton", memory)
    monkeypatch.setattr(server, "_memory_pose", lambda: (ORIGIN, "odom"))
    return memory


def see(memory: om.ObjectMemory, label: str, rng: float, bearing: float, times: int = 1) -> None:
    for _ in range(times):
        memory.observe(ORIGIN, [{"label": label, "range_m": rng, "bearing_deg": bearing}])


def test_recall_object_answers_a_spanish_query_with_distance_and_bearing(mem):
    see(mem, "wallet", 2.0, 90.0)
    out = server.recall_object(query="billetera")
    assert out["status"] == "ok"
    [obj] = out["objects"]
    assert obj["label"] == "wallet"
    assert obj["distance_m"] == 2.0
    assert obj["bearing_deg"] == 90.0  # to the left


def test_recall_object_not_found_lists_what_memory_has(mem):
    see(mem, "tv", 3.0, 0.0)
    out = server.recall_object(query="paraguas")
    assert out == {"status": "not_found", "query": "paraguas", "known_labels": ["tv"]}


def test_recall_object_without_a_pose_says_so(mem, monkeypatch):
    see(mem, "tv", 3.0, 0.0)
    monkeypatch.setattr(server, "_memory_pose", lambda: (None, None))
    out = server.recall_object(query="tele")
    assert "distance_m" not in out["objects"][0]
    assert "unavailable" in out["note"]


def test_list_and_forget_object(mem):
    see(mem, "cup", 1.0, 0.0)
    see(mem, "tv", 3.0, 45.0)
    listed = server.list_objects()
    assert listed["count"] == 2
    cup = next(o for o in listed["objects"] if o["label"] == "cup")
    assert server.forget_object(object_id=cup["id"])["status"] == "ok"
    assert server.forget_object(object_id=cup["id"])["status"] == "not_found"
    assert server.forget_object(object_id="all")["removed"] == 1


def test_describe_surroundings_carries_a_short_memory_summary(mem, monkeypatch):
    see(mem, "tv", 3.0, 0.0, times=3)
    see(mem, "cup", 1.0, 60.0, times=1)  # seen once: maybe a false positive
    snapshot = server.describe_surroundings()
    assert snapshot["sources"]["detector"] == "offline", "D7 contract untouched"
    assert [o["label"] for o in snapshot["memory"]["objects"]] == ["tv"]
    assert snapshot["memory"]["objects"][0]["distance_m"] == 3.0

    monkeypatch.setattr(server, "MEMORY_SUMMARY_MAX", 1)
    see(mem, "chair", 2.0, -60.0, times=2)
    assert server.describe_surroundings()["memory"]["omitted"] == 1


def test_describe_surroundings_with_empty_memory_adds_nothing(mem):
    assert "memory" not in server.describe_surroundings()


# -- look_around ---------------------------------------------------------------


class FakeTurn:
    def __init__(self, memory=None, status="completed", on_call=None):
        self.calls: list[float] = []
        self.memory, self.status, self.on_call = memory, status, on_call

    async def __call__(self, ctx, delta_yaw_radians, timeout_s, tolerance_degrees):
        self.calls.append(delta_yaw_radians)
        if self.on_call:
            self.on_call(len(self.calls))
        return {"status": self.status, "phase": "x", "error": None}


@pytest.mark.asyncio
async def test_look_around_refuses_without_perception(monkeypatch, mem):
    monkeypatch.setattr(server, "_memory_pose", lambda: (None, None))
    fake = FakeTurn()
    monkeypatch.setattr(server, "turn", fake)
    out = await server.look_around(None, total_degrees=360)
    assert out["error"] == "perception_offline"
    assert fake.calls == [], "turning blind updates nothing; it must not move"


@pytest.mark.asyncio
async def test_look_around_sweeps_in_steps_and_reports_what_is_new(monkeypatch, mem):
    see(mem, "tv", 3.0, 0.0)
    fake = FakeTurn(on_call=lambda n: see(mem, "bottle", 2.0, 0.0) if n == 3 else None)
    monkeypatch.setattr(server, "turn", fake)

    out = await server.look_around(None, total_degrees=360)

    assert out["status"] == "ok"
    assert len(fake.calls) == 6 and all(c == pytest.approx(1.0472, abs=1e-3) for c in fake.calls)
    assert out["turned_degrees"] == 360.0
    assert [o["label"] for o in out["new_objects"]] == ["bottle"]
    assert out["seen_again"] == []


@pytest.mark.asyncio
async def test_look_around_stops_when_stop_everything_is_pressed(monkeypatch, mem):
    fake = FakeTurn(on_call=lambda n: estop.signal_stop())
    monkeypatch.setattr(server, "turn", fake)
    out = await server.look_around(None, total_degrees=360)
    assert len(fake.calls) == 1
    assert out["status"] == "stopped"
    assert out["stopped_reason"] == "stop_everything"


@pytest.mark.asyncio
async def test_look_around_stops_at_the_first_failed_turn(monkeypatch, mem):
    fake = FakeTurn(status="failed")
    monkeypatch.setattr(server, "turn", fake)
    out = await server.look_around(None, total_degrees=180)
    assert len(fake.calls) == 1
    assert out["status"] == "stopped"
    assert out["turned_degrees"] == 0.0


def _look_around_tasks():
    from bridge.skills.task_runtime import get_registry

    return [t for t in get_registry().list_active() if t.skill_name == "look_around"]


@pytest.mark.asyncio
async def test_a_cancel_during_a_dwell_ends_the_sweep_at_once(monkeypatch, mem):
    """PARAR's soft path (soft_stop) cancels every registry task; a dwell must hear it."""
    import asyncio

    from bridge.skills.task_runtime import get_registry

    monkeypatch.setattr(server, "SIM_MODE", "real")  # real dwells
    monkeypatch.setattr(server, "LOOK_DWELL_S", 5.0)
    fake = FakeTurn()
    monkeypatch.setattr(server, "turn", fake)

    sweep = asyncio.ensure_future(server.look_around(None, total_degrees=360))
    await asyncio.sleep(0.05)  # inside the first dwell
    [task] = _look_around_tasks()
    get_registry().cancel(task.task_id)
    out = await asyncio.wait_for(sweep, 1.0)

    assert (out["status"], out["stopped_reason"]) == ("stopped", "cancelled")
    assert fake.calls == []
    assert task.status == "cancelled"


@pytest.mark.asyncio
async def test_a_cancel_mid_turn_stops_the_turn(monkeypatch, mem):
    """cancel_task on look_around: the turn in progress is its own task and must hear it too."""
    import asyncio

    from bridge.skills.task_runtime import get_registry

    turns = []

    async def slow_turn(ctx, delta_yaw_radians, timeout_s, tolerance_degrees):
        t = get_registry().create("turn")  # what skills.turn.run registers
        turns.append(t)
        try:
            await asyncio.wait_for(t.cancel_event.wait(), 5.0)
            t.status = "cancelled"
        except TimeoutError:
            t.status = "completed"
        return {"status": t.status, "phase": t.status, "error": None}

    monkeypatch.setattr(server, "turn", slow_turn)
    sweep = asyncio.ensure_future(server.look_around(None, total_degrees=360))
    await asyncio.sleep(0.05)  # inside the first turn
    [task] = _look_around_tasks()
    get_registry().cancel(task.task_id)
    out = await asyncio.wait_for(sweep, 1.0)

    assert (out["status"], out["stopped_reason"]) == ("stopped", "cancelled")
    assert [t.status for t in turns] == ["cancelled"]


@pytest.mark.asyncio
async def test_a_finished_sweep_leaves_no_running_task(monkeypatch, mem):
    monkeypatch.setattr(server, "turn", FakeTurn())
    out = await server.look_around(None, total_degrees=120)
    assert out["status"] == "ok"
    assert _look_around_tasks() == []


# -- wiring --------------------------------------------------------------------


def test_main_feeds_the_memory_from_the_perception_link():
    """A tested memory nothing feeds reports "never seen" forever — see
    test_singletons_are_started.py for the five times that shape bit."""
    src = (pathlib.Path(server.__file__)).read_text()
    main = src[src.index("def main() -> None:") :]
    assert "get_link().on_report = get_object_memory().observe_report" in main


def _link():
    return pl.PerceptionLink(forward=lambda *a: None, clock=lambda: 0.0)


def _report(**kw) -> str:
    base = {"report_version": 1, "pose": ORIGIN, "detector_online": True, "objects": []}
    return json.dumps({**base, **kw})


def test_the_link_hands_accepted_reports_to_its_listener():
    link, seen = _link(), []
    link.on_report = seen.append
    assert link._ingest_report(_report())
    assert not link._ingest_report(_report(report_version=99))
    assert not link._ingest_report("{nope")
    assert len(seen) == 1


def test_a_failing_listener_does_not_cost_the_report():
    link = _link()

    def boom(report):
        raise RuntimeError("bug")

    link.on_report = boom
    assert link._ingest_report(_report())
    assert link.latest_report()[0] is not None


@pytest.mark.asyncio
async def test_look_around_is_refused_while_a_walk_runs(monkeypatch, mem):
    from bridge.skills.task_runtime import get_registry

    fake = FakeTurn()
    monkeypatch.setattr(server, "turn", fake)
    walking = get_registry().create("walk_to")
    try:
        out = await server.look_around(None, total_degrees=360)
    finally:
        walking.status = "completed"
    assert (out["status"], out["error"]) == ("failed", "walk_task_active")
    assert fake.calls == []
    assert _look_around_tasks() == []
