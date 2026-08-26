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

Because of that latch, `run` RELEASES BY DEFAULT: after a successful gesture
it holds the final pose briefly (`hold_s`) and then sends 99 itself. Learned
on hardware 2026-08-27: `heart_both_hands` was dispatched, the follow-up
release never landed (the robot power-cycled mid-session), and the arms were
left actively holding the pose under motor load with nothing scheduled to let
go. A latched arm must never depend on a second tool call arriving.
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


#: How long a successful gesture's final pose is held before the automatic
#: release, and the cap on what a caller may ask for. The cap matters more
#: than the default: an unbounded hold is exactly the latched-arm hazard the
#: auto-release exists to close.
DEFAULT_HOLD_S = 2.0
MAX_HOLD_S = 15.0


async def run(
    name: str,
    ctx: Any | None = None,
    auto_release: bool = True,
    hold_s: float = DEFAULT_HOLD_S,
) -> dict[str, Any]:
    """Dispatch one preset gesture by firmware name. Returns the task dict.

    Mirrors `_g1_request.run_g1_request`'s three-way SIM_MODE branch — stub /
    logged-only sim / real dispatch — with the catalogue lookup and the
    rt/arm_sdk contention check in front.

    On a successful real dispatch (and unless the gesture IS release_arm),
    the final pose is held for `hold_s` and then RELEASE_ARM (99) is sent
    automatically. `auto_release=False` opts out and leaves the latch to the
    caller — who then owns sending the release.
    """
    hold_s = max(0.0, min(MAX_HOLD_S, float(hold_s)))
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

        # The auto-release. Only after a SUCCESSFUL non-release gesture: a
        # failed dispatch latched nothing new, and releasing after somebody
        # else's failure would be this task exceeding its own footprint.
        if code == 0 and auto_release and gesture != g1_protocol.Gesture.RELEASE_ARM:
            if hold_s > 0:
                task.phase = "holding_pose"
                await asyncio.sleep(hold_s)
            release_code, _ = await asyncio.to_thread(
                g1_rpc.call_arm, int(g1_protocol.Gesture.RELEASE_ARM)
            )
            task.phase = "released" if release_code == 0 else "release_failed"
            task.result["held_s"] = hold_s
            task.result["release_rpc_code"] = release_code
            if release_code != 0:
                # The gesture itself succeeded, so status stays completed —
                # but a standing latch is exactly what must not pass silently.
                task.result["note"] = (
                    f"the gesture ran but the automatic release answered rpc "
                    f"{release_code}; the arm may still be latched holding the "
                    "pose — send gesture('release_arm')."
                )
                log.warning(
                    "gesture.auto_release_failed", name=normalized, rpc_code=release_code
                )
        elif code == 0 and not auto_release and gesture != g1_protocol.Gesture.RELEASE_ARM:
            task.result["note"] = (
                "auto_release=False: the arm is latched holding this pose "
                "until gesture('release_arm') is sent."
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
