"""Task lifecycle for long-running skills.

Defines a `Task` record (id, status, progress, phase, result, cancel signal)
and a thread-safe `TaskRegistry` singleton. Long-running skills create a Task,
update its fields as they progress, and check `task.cancel_event` between
iterations so external callers can interrupt them.

Two ways to run a long skill, chosen per call by the tool's `wait` argument:

- **Blocking** (`wait=true`, the default): the tool call returns when the skill
  ends, with the final task dict. Progress reaches the caller as MCP progress
  notifications. Over stdio the caller cannot cancel it mid-flight — it is busy
  waiting — though `stop_everything` from another connection still can.
- **Background** (`wait=false`): `start_in_background` creates the run on the
  event loop and the tool returns the `task_id` at once, with status
  `running`. The caller polls `get_task` / `wait_task`, can observe the world
  while the robot moves, and can `cancel_task` from the same session. This is
  what lets an agent perceive while acting instead of being blind for the
  length of every walk.

Either way the Task in the registry is the same record and ends the same way,
so a caller never needs to know which mode produced it.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

TaskStatus = Literal["running", "completed", "cancelled", "failed"]


@dataclass
class Task:
    """Mutable record for one skill invocation."""

    task_id: str
    skill_name: str
    started_at: float
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    status: TaskStatus = "running"
    progress: float = 0.0
    phase: str = "starting"
    result: dict[str, Any] | None = None
    error: str | None = None
    ended_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "skill_name": self.skill_name,
            "status": self.status,
            "progress": round(self.progress, 3),
            "phase": self.phase,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": round((self.ended_at or time.time()) - self.started_at, 3),
        }


class TaskRegistry:
    """Thread-safe in-memory registry of active and recent tasks."""

    def __init__(self, retention_seconds: float = 300.0) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._retention_seconds = retention_seconds

    def create(self, skill_name: str) -> Task:
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        task = Task(task_id=task_id, skill_name=skill_name, started_at=time.time())
        with self._lock:
            self._tasks[task_id] = task
            self._gc_locked()
        log.info("task.create", task_id=task_id, skill_name=skill_name)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """Signal cancellation.

        Returns True only on the first successful cancel request — subsequent
        calls for the same task return False so callers can tell whether they
        were the originator.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status != "running":
                return False
            if task.cancel_event.is_set():
                return False  # already cancel-requested
            task.cancel_event.set()
        log.info("task.cancel.requested", task_id=task_id)
        return True

    def list_active(self) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if t.status == "running"]

    def list_recent(self, limit: int = 20) -> list[Task]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.started_at, reverse=True)[:limit]

    def _gc_locked(self) -> None:
        """Drop completed tasks older than the retention window. Caller holds _lock."""
        now = time.time()
        cutoff = now - self._retention_seconds
        stale = [
            tid
            for tid, t in self._tasks.items()
            if t.status != "running" and (t.ended_at or now) < cutoff
        ]
        for tid in stale:
            del self._tasks[tid]


_registry_singleton: TaskRegistry | None = None


def get_registry() -> TaskRegistry:
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = TaskRegistry()
    return _registry_singleton


# ---------------------------------------------------------------------------
# Background execution
# ---------------------------------------------------------------------------

# Strong references to in-flight background runs. The event loop keeps only a
# weak reference to a task made by `create_task`, so a run nobody holds can be
# garbage-collected mid-walk — and a walk loop that silently vanishes stops
# sending velocity without ever running its own stop sequence.
_background: set[asyncio.Task[None]] = set()


def _finish_abnormally(task: Task, phase: str, error: str) -> None:
    if task.status != "running":
        return
    task.status = "failed"
    task.phase = phase
    task.error = error
    task.ended_at = time.time()


def start_in_background(task: Task, body: Callable[[], Awaitable[Any]]) -> asyncio.Task[None]:
    """Run `body` as `task`'s execution on the running event loop; return at once.

    `task` must already be in the registry — callers create it first so the
    `task_id` exists before anything runs — and `body` is expected to drive it
    to a terminal status, which every long skill already does on every path
    (the blocking call returns `task.to_dict()`).

    What this adds is a guarantee for the paths a skill does not plan for. An
    exception, or the run being torn down, still ends the Task as `failed`
    rather than leaving it `running` forever: a task stuck in `running` reads to
    an agent as "still walking", and keeps the link watchdog believing
    something is moving.
    """

    async def runner() -> None:
        try:
            await body()
        except asyncio.CancelledError:
            _finish_abnormally(
                task, "torn_down", "the background run was cancelled before the skill finished"
            )
            raise
        except Exception as exc:
            log.exception(
                "task.background.crashed", task_id=task.task_id, skill_name=task.skill_name
            )
            _finish_abnormally(task, "crashed", f"{type(exc).__name__}: {exc}")
        else:
            _finish_abnormally(task, "unfinished", "the skill returned without a terminal status")

    handle = asyncio.get_running_loop().create_task(
        runner(), name=f"{task.skill_name}:{task.task_id}"
    )
    _background.add(handle)
    handle.add_done_callback(_background.discard)
    log.info("task.background.started", task_id=task.task_id, skill_name=task.skill_name)
    return handle


async def wait_until_done(task: Task, timeout_s: float, poll_s: float = 0.05) -> bool:
    """Wait for `task` to leave `running`. True if it did within `timeout_s`.

    Polls rather than waiting on an event: skills set `status` directly on the
    record from many places, and a poll cannot miss a transition that a
    notification path forgot to signal.
    """
    deadline = time.monotonic() + timeout_s
    while task.status == "running":
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_s)
    return True
