"""`wait=false`: long skills that answer at once and keep moving in the background.

The point of the mode is that an agent stays able to observe and to stop while
the robot acts. So the properties pinned here are the ones whose failure would
quietly take that away:

- the call returns before the motion ends, with a `task_id` that is already in
  the registry — not one that appears later, or never;
- the record always reaches a terminal status, even when the skill crashes or
  forgets to — a task stuck at `running` reads as "still walking" forever;
- `get_task`, `wait_task`, `cancel_task` and `stop_everything` all see the same
  record, in stub as on hardware;
- the default stays blocking, so no existing caller changes behaviour.
"""

from __future__ import annotations

import asyncio

import pytest

from bridge.skills import task_runtime
from bridge.skills.task_runtime import get_registry, start_in_background, wait_until_done
from bridge.tools import _background, gestures, locomotion, safety, tasks


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr(task_runtime, "_registry_singleton", None)
    monkeypatch.setattr(locomotion, "SIM_MODE", "stub")
    monkeypatch.setattr(safety, "SIM_MODE", "stub")


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


async def test_start_returns_before_the_body_finishes():
    task = get_registry().create("probe")
    release = asyncio.Event()

    async def body():
        await release.wait()
        task.status = "completed"

    handle = start_in_background(task, body)
    await asyncio.sleep(0)
    assert task.status == "running"
    assert handle in task_runtime._background, "the run must be strongly referenced while live"

    release.set()
    assert await wait_until_done(task, timeout_s=1.0)
    assert task.status == "completed"
    await asyncio.sleep(0)
    assert handle not in task_runtime._background, "finished runs must not accumulate"


async def test_a_crashing_skill_still_ends_its_task():
    task = get_registry().create("probe")

    async def body():
        raise RuntimeError("pose went away")

    start_in_background(task, body)
    assert await wait_until_done(task, timeout_s=1.0)
    assert task.status == "failed"
    assert task.phase == "crashed"
    assert "pose went away" in task.error
    assert task.ended_at is not None


async def test_a_skill_that_forgets_to_finish_is_marked_failed():
    task = get_registry().create("probe")

    async def body():
        return {"oops": "no terminal status"}

    start_in_background(task, body)
    assert await wait_until_done(task, timeout_s=1.0)
    assert (task.status, task.phase) == ("failed", "unfinished")


async def test_a_torn_down_run_is_marked_failed():
    task = get_registry().create("probe")

    async def body():
        await asyncio.sleep(10)

    handle = start_in_background(task, body)
    await asyncio.sleep(0)
    handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle
    assert (task.status, task.phase) == ("failed", "torn_down")


async def test_wait_until_done_times_out_on_a_running_task():
    task = get_registry().create("probe")
    assert await wait_until_done(task, timeout_s=0.1) is False
    assert task.status == "running"


# ---------------------------------------------------------------------------
# The tools, end to end in stub
# ---------------------------------------------------------------------------


async def test_walk_to_background_returns_running_then_completes():
    started = await locomotion.walk_to(
        ctx=None,
        target_x_meters_world_frame=1.0,
        target_y_meters_world_frame=2.0,
        wait=False,
    )
    assert started["status"] == "running"
    assert started["background"] is True
    assert "NOT finished" in started["next"], "the model must not read `running` as done"

    task_id = started["task_id"]
    polled = tasks.get_task(task_id=task_id)
    assert polled["ok"] is True
    assert polled["done"] is False

    finished = await tasks.wait_task(task_id=task_id, timeout_s=5.0)
    assert finished["done"] is True
    assert finished["status"] == "completed"
    assert "Would walk to (1.00, 2.00)" in finished["result"]["message"]


async def test_the_same_session_can_cancel_a_background_walk():
    started = await locomotion.walk_to(
        ctx=None,
        target_x_meters_world_frame=1.0,
        target_y_meters_world_frame=0.0,
        wait=False,
    )
    assert tasks.cancel_task(task_id=started["task_id"])["ok"] is True
    finished = await tasks.wait_task(task_id=started["task_id"], timeout_s=5.0)
    assert finished["status"] == "cancelled"


async def test_stop_everything_stops_background_stub_tasks():
    started = await locomotion.turn(ctx=None, delta_yaw_radians=1.0, wait=False)

    stopped = await safety.stop_everything()

    assert stopped["cancelled_task_ids"] == [started["task_id"]]
    finished = await tasks.wait_task(task_id=started["task_id"], timeout_s=5.0)
    assert finished["status"] == "cancelled"


async def test_dance_background_uses_the_skills_own_stub():
    started = await gestures.dance(ctx=None, wait=False)
    finished = await tasks.wait_task(task_id=started["task_id"], timeout_s=5.0)
    assert finished["done"] is True
    assert finished["skill_name"] == "dance"


async def test_the_default_still_blocks():
    """No existing caller — console, Claude Code, the agent — changes behaviour."""
    result = await locomotion.walk_to(
        ctx=None, target_x_meters_world_frame=1.0, target_y_meters_world_frame=0.0
    )
    assert result["status"] == "ok"
    assert "background" not in result


async def test_background_on_a_real_target_hands_the_skill_the_announced_task(monkeypatch):
    """The task_id the caller is given must be the record the skill updates.

    If the skill created its own record instead, the caller would poll a task
    that never moves while the robot walks under another id.
    """
    from bridge.skills import walk_to as walk_to_skill

    seen = {}

    async def fake_run(*, task, **kwargs):
        seen["task"] = task
        seen["kwargs"] = kwargs
        task.status = "completed"
        return task.to_dict()

    monkeypatch.setattr(locomotion, "SIM_MODE", "isaac")
    monkeypatch.setattr(walk_to_skill, "run", fake_run)

    started = await locomotion.walk_to(
        ctx=None,
        target_x_meters_world_frame=3.0,
        target_y_meters_world_frame=-1.0,
        stop_distance_m=0.5,
        timeout_s=20.0,
        wait=False,
    )
    finished = await tasks.wait_task(task_id=started["task_id"], timeout_s=5.0)

    assert seen["task"].task_id == started["task_id"]
    assert seen["kwargs"] == {
        "target_x": 3.0,
        "target_y": -1.0,
        "stop_distance_m": 0.5,
        "timeout_s": 20.0,
    }, "background runs get no ctx: the request that owned it has already ended"
    assert finished["status"] == "completed"


async def test_unknown_task_ids_are_reported_not_raised():
    assert tasks.get_task(task_id="tsk_nope")["reason"] == "unknown_task_id"
    assert (await tasks.wait_task(task_id="tsk_nope", timeout_s=0.5))["reason"] == "unknown_task_id"


async def test_wait_task_reports_a_timeout_as_not_done(monkeypatch):
    monkeypatch.setattr(_background, "STUB_DURATION_S", 5.0)
    started = await locomotion.walk_to(
        ctx=None,
        target_x_meters_world_frame=1.0,
        target_y_meters_world_frame=0.0,
        wait=False,
    )
    waited = await tasks.wait_task(task_id=started["task_id"], timeout_s=0.5)
    assert waited["done"] is False
    assert waited["status"] == "running"
    assert waited["waited_s"] >= 0.5
    tasks.cancel_task(task_id=started["task_id"])
    assert (await tasks.wait_task(task_id=started["task_id"], timeout_s=5.0))["done"] is True


async def test_polls_are_operator_contact_but_a_wait_is_not_continuous_contact(monkeypatch):
    touches = []

    class FakeWatchdog:
        def touch(self):
            touches.append("touch")

    monkeypatch.setattr(tasks, "get_watchdog", lambda: FakeWatchdog())
    task = get_registry().create("probe")

    tasks.get_task(task_id=task.task_id)
    assert touches == ["touch"]

    touches.clear()
    await tasks.wait_task(task_id=task.task_id, timeout_s=0.5)
    assert touches == ["touch", "touch"], "entry and exit only — never during the wait"
