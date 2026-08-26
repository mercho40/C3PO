"""gesture: run any preset upper-limb action from the firmware's own catalogue.

`wave` / `clap` / `hug` / … each wrap one hand-picked id from
`g1_protocol.Gesture`, but the robot's own `GetActionList` (arm service,
api_id 7107, read live 2026-08-15) reports 23 preset actions. This skill
exposes the whole table by name — blow kisses, hearts, boxing wins,
`ultraman_ray`, `refuse`, the lot — through the exact `g1_rpc.call_arm()`
primitive the verified-live `wave` already uses. Zero new wire format.

What it deliberately does NOT cover: the robot's taught (App-recorded) actions
(`g1_protocol.TAUGHT_ACTIONS` — "Waist_Drum_Dance" etc.). Those execute by
*name* through the arm service's string overload, whose wire shape is unproven
on this firmware (`docs/ROBOT-API.md` §6.3: 7108 is not in the firmware-matched
tree). Same refusal `dance.py` already records: inventing a wire format for a
humanoid's arms and trusting it blind is the mistake this codebase exists to
not make.

Two contention rules this must respect (docs/ROBOT-API.md §6.4):

- The arm action service is implemented ON `rt/arm_sdk`, so dispatching a
  gesture while `bridge.teleop.arm_sdk`'s driver is engaged is the documented
  cause of error 7400. The driver already refuses to engage while a gesture
  task runs (`_GESTURE_SKILLS` includes this skill's name); this module is the
  other half of that handshake and refuses to dispatch while the driver holds
  the arms.
- A sustained gesture LATCHES the arm on completion: the next different id is
  refused with 7401 until RELEASE_ARM (99) — exposed here as `release_arm`,
  and as its own tool — or the same id repeats.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Final

import structlog

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

#: Every preset action the robot's own GetActionList reports, by its firmware
#: name (lowercased `Gesture` member names — which ARE the firmware's strings,
#: see g1_protocol.Gesture's docstring on trusting the robot over the docs).
GESTURE_CATALOGUE: Final[dict[str, g1_protocol.Gesture]] = {
    member.name.lower(): member for member in g1_protocol.Gesture
}


def available_gestures() -> list[str]:
    """Names accepted by `run`, sorted for stable error messages."""
    return sorted(GESTURE_CATALOGUE)


def _arm_sdk_engaged() -> bool:
    """True when the teleop arm driver currently owns rt/arm_sdk.

    Import is local and failure-tolerant: the gesture path must not break if
    the teleop stack is absent or half-configured — absence of a driver means
    absence of contention.
    """
    try:
        from bridge.teleop.arm_sdk import get_driver

        return get_driver().engaged
    except Exception:
        return False


async def run(name: str, ctx: Any | None = None) -> dict[str, Any]:
    """Dispatch one preset gesture by firmware name. Returns the task dict.

    Mirrors `_g1_request.run_g1_request`'s three-way SIM_MODE branch — stub /
    logged-only sim / real dispatch — with the catalogue lookup and the
    rt/arm_sdk contention check in front.
    """
    normalized = name.strip().lower()
    gesture = GESTURE_CATALOGUE.get(normalized)

    task = get_registry().create("gesture")

    if gesture is None:
        task.status = "failed"
        task.phase = "unknown_gesture"
        task.error = f"unknown_gesture_{normalized!r}"
        task.result = {
            "requested": name,
            "available": available_gestures(),
            "note": (
                "Taught actions (Waist_Drum_Dance, Scratch_head, Spin_discs, "
                "Throw_money) are not dispatchable — their by-name wire format "
                "is unproven on this firmware."
            ),
        }
        task.ended_at = time.time()
        return task.to_dict()

    if _arm_sdk_engaged():
        task.status = "failed"
        task.phase = "arm_sdk_engaged"
        task.error = "arm_sdk_engaged"
        task.result = {
            "gesture": int(gesture),
            "name": normalized,
            "note": (
                "The rt/arm_sdk driver currently owns the arms (move_arm or "
                "teleop). The arm action service is built on the same topic, "
                "and two owners is the documented cause of error 7400. Call "
                "release_arm_control first."
            ),
        }
        task.ended_at = time.time()
        return task.to_dict()

    gating: dict[str, Any] = {}
    if int(gesture) in g1_protocol.ACTION_REQUIRES_FSM:
        gating["requires_fsm"] = list(g1_protocol.ACTION_REQUIRES_FSM[int(gesture)])
    if int(gesture) in g1_protocol.ACTION_REQUIRES_MODE_MACHINE:
        gating["requires_mode_machine"] = list(
            g1_protocol.ACTION_REQUIRES_MODE_MACHINE[int(gesture)]
        )

    log.info(
        "gesture.dispatch",
        task_id=task.task_id,
        gesture=int(gesture),
        name=normalized,
        sim_mode=SIM_MODE,
        **gating,
    )

    if SIM_MODE == "stub":
        task.status = "completed"
        task.phase = "stub"
        task.progress = 1.0
        task.result = {
            "gesture": int(gesture),
            "name": normalized,
            **gating,
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
            "gesture": int(gesture),
            "name": normalized,
            **gating,
            "note": (
                f"SIM_MODE={SIM_MODE} doesn't subscribe to arm_request — "
                "request logged but not dispatched."
            ),
        }
        task.ended_at = time.time()
        return task.to_dict()

    try:
        if ctx is not None:
            try:
                await ctx.report_progress(
                    progress=0.5, total=1.0, message=f"dispatching {normalized}"
                )
            except Exception:
                pass  # progress is best-effort

        # Off the event loop: call_arm blocks up to ARM_TIMEOUT_S because the
        # arm service acks on COMPLETION of the motion, not receipt.
        code, data = await asyncio.to_thread(g1_rpc.call_arm, int(gesture))

        task.status = "completed" if code == 0 else "failed"
        task.phase = "dispatched" if code == 0 else "rpc_error"
        task.progress = 1.0
        task.result = {
            "gesture": int(gesture),
            "name": normalized,
            **gating,
            "rpc_code": code,
            "rpc_data": data,
        }
        if code != 0:
            task.error = f"rpc_error_code_{code}"
            if code == 7401:
                task.result["note"] = (
                    "7401: the arm is latched holding the previous action — "
                    "send release_arm (id 99) or repeat the same gesture."
                )
        task.ended_at = time.time()
        return task.to_dict()

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("gesture.failed", task_id=task.task_id, name=normalized)
        return task.to_dict()
