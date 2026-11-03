"""The tool-side half of `wait=false`: start a long skill and answer at once.

Shared by every long tool (`walk_to`, `turn`, `dance`) so they describe the
flag identically and hand back the same shape. The execution half — strong
references, crash capture, the terminal-status guarantee — is
`bridge.skills.task_runtime.start_in_background`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bridge.config import SIM_MODE
from bridge.skills.task_runtime import Task, get_registry, start_in_background

# Read by the model, so it says what to do next rather than how it works.
WAIT_DESCRIPTION = (
    "true (default): return when the motion has finished, with the final "
    "result. false: start the motion and return immediately with a task_id "
    "while the robot keeps moving — then call wait_task or get_task until "
    "status is no longer 'running', and cancel_task to stop it early. Use "
    "false when you want to keep observing (describe_surroundings) or be able "
    "to stop the motion while it happens."
)

# How long a stub background run pretends to move. Long enough that a client
# can observe `running`, poll and cancel; short enough for a test suite.
STUB_DURATION_S = 0.5


def start(task: Task, body: Callable[[], Awaitable[Any]]) -> dict:
    """Run `body` in the background as `task`; return the immediate answer."""
    start_in_background(task, body)
    return {
        **task.to_dict(),
        "env": SIM_MODE,
        "background": True,
        "next": (
            "The robot is executing this now — it is NOT finished. Call "
            f"wait_task('{task.task_id}') or get_task('{task.task_id}') until "
            "status is no longer 'running'; cancel_task stops it."
        ),
    }


def create(skill_name: str) -> Task:
    return get_registry().create(skill_name)


async def stub_run(task: Task, message: str) -> None:
    """A stub run with a real lifecycle: running, then completed or cancelled.

    The blocking stub paths return a fake dict with no registry entry, which
    was enough when nothing polled. A background caller polls, so the stub has
    to produce a task that `get_task`, `wait_task`, `cancel_task` and
    `stop_everything` all genuinely see.
    """
    task.phase = "stub"
    try:
        await asyncio.wait_for(task.cancel_event.wait(), timeout=STUB_DURATION_S)
    except TimeoutError:
        task.status = "completed"
        task.progress = 1.0
        task.result = {"message": message, "stub": True}
    else:
        task.status = "cancelled"
        task.result = {"message": "[STUB] cancelled before finishing.", "stub": True}
    task.ended_at = time.time()
