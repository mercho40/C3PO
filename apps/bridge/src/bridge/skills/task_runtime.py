"""Task lifecycle for long-running skills.

Defines a `Task` record (id, status, progress, phase, result, cancel signal)
and a thread-safe `TaskRegistry` singleton. Long-running skills create a Task,
update its fields as they progress, and check `task.cancel_event` between
iterations so external callers can interrupt them.

The current MCP transport is stdio (single-request-at-a-time), so mid-flight
cancel via a sibling MCP tool isn't reachable from the same client session
while the original tool is blocked. The registry is still useful for:

- Diagnostic visibility (`list_active`, `list_recent`)
- Direct-Python and HTTP-MCP use (Phase 5+ — cancel becomes truly external)
- A uniform result shape across every long-running skill

When we move to HTTP MCP, the same shape supports true fire-and-forget
execution: `walk_to` returns the task_id immediately, the client polls or
subscribes to progress, and a separate `cancel_task` request actually
interrupts the in-flight task from another connection.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
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
