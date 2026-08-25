"""Tests for TaskRegistry — the core primitive every skill (walk_velocity,
stop_everything, walk_to, turn, ...) builds on, and until now only ever
exercised indirectly through those skills' own tests. Direct tests here
catch regressions in cancellation/GC semantics that an individual skill's
test wouldn't necessarily notice.

Uses fresh TaskRegistry() instances, not get_registry()'s module-level
singleton, so tests can't leak state into each other.
"""

from __future__ import annotations

import time

from bridge.skills.task_runtime import TaskRegistry


def test_create_assigns_unique_ids_and_running_status():
    registry = TaskRegistry()
    t1 = registry.create("walk_to")
    t2 = registry.create("turn")

    assert t1.task_id != t2.task_id
    assert t1.task_id.startswith("tsk_")
    assert t1.status == "running"
    assert t1.skill_name == "walk_to"


def test_cancel_returns_true_once_then_false():
    registry = TaskRegistry()
    task = registry.create("walk_to")

    assert registry.cancel(task.task_id) is True
    assert task.cancel_event.is_set()
    # Second cancel request on the same task: caller already knows they
    # weren't first, per the docstring's contract.
    assert registry.cancel(task.task_id) is False


def test_cancel_unknown_task_id_returns_false():
    registry = TaskRegistry()
    assert registry.cancel("tsk_does_not_exist") is False


def test_cancel_non_running_task_returns_false():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.status = "completed"

    assert registry.cancel(task.task_id) is False
    assert not task.cancel_event.is_set()


def test_list_active_only_returns_running_tasks():
    registry = TaskRegistry()
    running = registry.create("walk_to")
    done = registry.create("turn")
    done.status = "completed"
    done.ended_at = time.time()

    active = registry.list_active()

    assert [t.task_id for t in active] == [running.task_id]


def test_list_recent_sorted_newest_first_and_respects_limit():
    registry = TaskRegistry()
    tasks = []
    for i in range(5):
        t = registry.create(f"skill_{i}")
        t.started_at = 1000.0 + i  # deterministic ordering, no real sleeps
        tasks.append(t)

    recent = registry.list_recent(limit=3)

    assert [t.task_id for t in recent] == [tasks[4].task_id, tasks[3].task_id, tasks[2].task_id]


def test_gc_drops_old_completed_tasks_but_keeps_running_ones():
    registry = TaskRegistry(retention_seconds=60.0)
    old_completed = registry.create("walk_to")
    old_completed.status = "completed"
    old_completed.ended_at = time.time() - 3600  # 1h ago, well past 60s retention

    still_running = registry.create("turn")  # never completes, no ended_at

    recent_completed = registry.create("stop_everything")
    recent_completed.status = "completed"
    recent_completed.ended_at = time.time()  # just now, within retention

    # _gc_locked() runs as part of create() — trigger one more create to run it
    # after the above mutations.
    registry.create("walk_velocity")

    remaining_ids = {t.task_id for t in registry.list_recent(limit=10)}
    assert old_completed.task_id not in remaining_ids
    assert still_running.task_id in remaining_ids
    assert recent_completed.task_id in remaining_ids


def test_task_to_dict_computes_duration_for_running_task():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.started_at = time.time() - 5.0  # started 5s ago, still running (no ended_at)

    d = task.to_dict()

    assert d["ended_at"] is None
    assert d["duration_s"] >= 5.0


def test_task_to_dict_computes_duration_for_completed_task():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.started_at = 1000.0
    task.ended_at = 1007.5
    task.status = "completed"

    d = task.to_dict()

    assert d["duration_s"] == 7.5
    assert d["status"] == "completed"
