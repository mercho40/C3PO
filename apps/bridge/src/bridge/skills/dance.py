"""dance: a short choreographed sequence of upper-limb gestures.

Not a single firmware mode. `Mode.DANCE` (503, `g1_protocol.py`) exists in the
FSM enum but has never been sent to this robot and has no confirmed behaviour
-- sending it blind would be exactly the kind of unverified motion this
codebase's own conventions refuse to ship (see `skill_meta.py`'s `works_real`
docstring). The robot's own taught actions (`TAUGHT_ACTIONS`, e.g.
"Waist_Drum_Dance") are a literal named dance move, but they're triggered by
the arm service's *string*-addressed overload, which no code path here
implements or has ever exercised against this robot -- inventing that wire
format now and trusting it on a humanoid's arms is the same mistake in a more
dangerous place.

So `dance` instead sequences several already-verified `Gesture` ids (read live
from the robot's own `GetActionList`, see `g1_protocol.py`) through the exact
same `g1_rpc.call_arm()` primitive `wave`/`clap`/`hug` already use in
production. Zero new wire format, zero new RPC path.

One hardware detail this sequencing has to respect: per `g1_protocol.py`'s
RELEASE_ARM(99) note, a gesture's arm *latches* on completion -- the next
DIFFERENT gesture id is refused (error 7401) until either the same id repeats
or RELEASE_ARM(99) is sent first. So `SEQUENCE` interleaves RELEASE_ARM
between every distinct gesture, not just at the end.

Same task-lifecycle shape as `walk_velocity`: cancellable via `TaskRegistry`,
reports progress per step, honest stub/logged_only branches for sim. NOT YET
LIVE-TESTED against real hardware -- see `works_real=False` on the MCP tool.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

# Distinct from `wave`'s WAVE_ABOVE_HEAD(26) so the two skills read as
# different moves. Picked from `g1_protocol.Gesture` members this robot's own
# GetActionList already verified and that no other skill uses yet. Kept short
# (three distinct gestures) so a first live run stays a small blast radius --
# extend once this has actually been watched work.
SEQUENCE: tuple[g1_protocol.Gesture, ...] = (
    g1_protocol.Gesture.BOTH_HANDS_UP,
    g1_protocol.Gesture.RELEASE_ARM,
    g1_protocol.Gesture.HIGH_FIVE,
    g1_protocol.Gesture.RELEASE_ARM,
    g1_protocol.Gesture.WAVE_UNDER_HEAD,
    g1_protocol.Gesture.RELEASE_ARM,
)


async def run(ctx: Any | None = None) -> dict[str, Any]:
    """Run the dance gesture sequence. Cancellable between steps.

    Returns the full task dict (see `Task.to_dict`). Mirrors
    `_g1_request.run_g1_request`'s three-way SIM_MODE branch (stub / logged
    sim / real dispatch), generalised to a multi-step sequence.
    """
    task = get_registry().create("dance")

    if SIM_MODE == "stub":
        task.status = "completed"
        task.phase = "stub"
        task.progress = 1.0
        task.result = {
            "sequence": [int(g) for g in SEQUENCE],
            "note": "Stub mode — no dispatch.",
        }
        task.ended_at = time.time()
        return task.to_dict()

    topics = g1_protocol.topics_for(SIM_MODE)
    if topics.arm_request is None:
        task.status = "completed"
        task.phase = "logged_only"
        task.progress = 1.0
        task.result = {
            "sequence": [int(g) for g in SEQUENCE],
            "note": (
                f"SIM_MODE={SIM_MODE} doesn't subscribe to arm_request — "
                "sequence logged but not dispatched."
            ),
        }
        task.ended_at = time.time()
        return task.to_dict()

    # SIM_MODE == "real": dispatch each step for real, off the event loop
    # (call_arm blocks up to ARM_TIMEOUT_S waiting on the completion ack —
    # same reasoning as _g1_request.py's dispatch branch).
    steps: list[dict[str, Any]] = []
    try:
        for i, gesture in enumerate(SEQUENCE):
            if task.cancel_event.is_set():
                # Release the arm on the way out. `SEQUENCE` interleaves
                # RELEASE_ARM between gestures precisely because a gesture
                # LATCHES its final pose — but bailing out here skipped the
                # next one, so a cancelled dance left the arm holding whatever
                # keyframe it was on. Every gesture after that returns 7401
                # until somebody sends 99, and PARAR sends Damp, not this.
                try:
                    await asyncio.to_thread(
                        g1_rpc.call_arm, int(g1_protocol.Gesture.RELEASE_ARM)
                    )
                except Exception:
                    log.warning("dance.cancel_release_failed", exc_info=True)
                task.status = "cancelled"
                task.phase = "cancelled"
                task.result = {"steps": steps}
                task.ended_at = time.time()
                return task.to_dict()

            task.phase = f"step_{i}_{gesture.name.lower()}"
            task.progress = i / len(SEQUENCE)
            if ctx is not None:
                try:
                    await ctx.report_progress(progress=task.progress, total=1.0, message=task.phase)
                except Exception:
                    pass  # progress is best-effort

            code, data = await asyncio.to_thread(g1_rpc.call_arm, int(gesture))
            steps.append(
                {"gesture": int(gesture), "name": gesture.name, "rpc_code": code, "rpc_data": data}
            )
            if code != 0:
                task.status = "failed"
                task.phase = "rpc_error"
                task.error = f"rpc_error_code_{code}"
                task.result = {"steps": steps}
                task.ended_at = time.time()
                return task.to_dict()

        task.status = "completed"
        task.phase = "done"
        task.progress = 1.0
        task.result = {"steps": steps}
        task.ended_at = time.time()
        return task.to_dict()

    except asyncio.CancelledError:
        # CancelledError is a BaseException, so `except Exception` below does
        # NOT catch it. Without this branch an externally-cancelled dance (an
        # HTTP client disconnecting, FastMCP timing out) leaves the task at
        # status="running" forever: TaskRegistry._gc_locked only reaps tasks
        # that aren't running, so it never gets collected, and anything
        # reading "is the robot busy" off the registry sees perpetual motion.
        task.status = "cancelled"
        task.phase = "cancelled"
        task.result = {"steps": steps}
        task.ended_at = time.time()
        log.info("dance.cancelled", task_id=task.task_id)
        raise  # never swallow cancellation -- the caller is entitled to it

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.result = {"steps": steps}
        task.ended_at = time.time()
        log.exception("dance.failed", task_id=task.task_id)
        return task.to_dict()
