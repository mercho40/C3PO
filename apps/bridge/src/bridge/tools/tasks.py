"""Task-registry tools: `cancel_task`, `list_active_tasks`, `get_task`, `wait_task`."""

from __future__ import annotations

import time
from typing import Annotated

from pydantic import Field

from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.skills.task_runtime import Task, get_registry, wait_until_done
from bridge.watchdog import get_watchdog


# ---------------------------------------------------------------------------
# Tool: cancel_task
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="task",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.3,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["unknown_task_id", "cancel_already_requested", "task_not_running"],
    )
)
def cancel_task(
    task_id: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Task ID returned by a long-running tool (e.g. `walk_to`'s "
                "result.task_id). Sets the task's cancel signal; the running "
                "skill will observe it between iterations and stop motion cleanly."
            ),
        ),
    ],
) -> dict:
    """Request graceful cancellation of an in-flight task.

    Works from the same session for a skill started with `wait=false` — the
    call that started it has already returned. A skill started with the
    default `wait=true` is still occupying its caller, so cancelling it needs
    another connection (over stdio, Claude Code's default, there is none);
    `stop_everything` from anywhere always works.
    """
    from bridge.skills.task_runtime import get_registry

    registry = get_registry()
    ok = registry.cancel(task_id)
    if not ok:
        existing = registry.get(task_id)
        if existing is None:
            return {"task_id": task_id, "ok": False, "reason": "unknown_task_id"}
        if existing.cancel_event.is_set():
            return {"task_id": task_id, "ok": False, "reason": "cancel_already_requested"}
        return {
            "task_id": task_id,
            "ok": False,
            "reason": f"task_not_running (status={existing.status})",
        }
    return {"task_id": task_id, "ok": True}


# ---------------------------------------------------------------------------
# Tool: list_active_tasks
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="task",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
    )
)
def list_active_tasks(
    include_recent: Annotated[
        bool,
        Field(
            description=(
                "If true, also include recently-completed tasks (up to 5 minutes "
                "old) so you can inspect the last walk's result."
            ),
        ),
    ] = False,
) -> dict:
    """List running tasks (and optionally recently-completed ones)."""
    from bridge.skills.task_runtime import get_registry

    # The other operator-liveness poll -- see the note in `get_state`. This is
    # the call a fire-and-forget client makes while waiting on a long skill,
    # which is precisely the case watchdog.py exists to cover.
    get_watchdog().touch()

    registry = get_registry()
    active = [t.to_dict() for t in registry.list_active()]
    payload: dict = {"active": active, "active_count": len(active)}
    if include_recent:
        recent = [t.to_dict() for t in registry.list_recent(limit=10)]
        payload["recent"] = recent
    return payload


# ---------------------------------------------------------------------------
# Tools: get_task / wait_task — the other half of `wait=false`
# ---------------------------------------------------------------------------

_TASK_ID_FIELD = Field(
    min_length=1,
    description="Task ID returned by a long-running tool (e.g. walk_to with wait=false).",
)


def _unknown(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "ok": False,
        "reason": "unknown_task_id",
        "note": "Tasks are kept for 5 minutes after they end; older ones are gone.",
    }


def _payload(task: Task) -> dict:
    return {**task.to_dict(), "ok": True, "done": task.status != "running"}


@mcp.tool(
    meta=skill_meta(
        classification="task",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["unknown_task_id"],
    )
)
def get_task(task_id: Annotated[str, _TASK_ID_FIELD]) -> dict:
    """Return one task's status, progress and phase now — and its result once done.

    Returns immediately. `done: false` (status `running`) means the robot is
    still executing it; any other status is final. Use this between other
    observations; use `wait_task` when you have nothing to do until it ends.
    """
    # A poll is operator contact, like `get_state` and `list_active_tasks`.
    get_watchdog().touch()
    task = get_registry().get(task_id)
    return _unknown(task_id) if task is None else _payload(task)


@mcp.tool(
    meta=skill_meta(
        classification="task",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=10.0,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["unknown_task_id"],
    )
)
async def wait_task(
    task_id: Annotated[str, _TASK_ID_FIELD],
    timeout_s: Annotated[
        float,
        Field(
            ge=0.5,
            le=30.0,
            description=(
                "Longest to wait. Returns the moment the task ends; if it is "
                "still running at the timeout, `done` is false — wait again, or "
                "look around first."
            ),
        ),
    ] = 10.0,
) -> dict:
    """Wait for a task to finish (up to `timeout_s`), then return it as `get_task` does.

    The robot keeps executing while you wait, and `stop_everything` still
    halts it immediately from any connection.
    """
    # Contact on entry and exit only. A wait is not a sign of life while it
    # waits: the link can drop mid-wait and nothing here would notice, so it
    # must not keep the watchdog quiet. With LINK_WATCHDOG=on, a wait longer
    # than LINK_TIMEOUT_MS is silence — and that is the conservative answer.
    get_watchdog().touch()
    task = get_registry().get(task_id)
    if task is None:
        return _unknown(task_id)
    started = time.monotonic()
    await wait_until_done(task, timeout_s)
    get_watchdog().touch()
    return {**_payload(task), "waited_s": round(time.monotonic() - started, 2)}
