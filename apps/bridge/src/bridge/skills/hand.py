"""set_hand / open_hands: dexterous hand (gripper) control.

Thin skill layer over `bridge.teleop.hands`, which owns everything worth
knowing about the hardware: this robot has two BrainCo Revo2 hands (settled by
inspection 2026-08-19), commanded as a single closure scalar per hand on
`rt/brainco/{side}/cmd`, with the polarity of [0,1] deliberately left
unconfigured until someone watches a hand move (`TELEOP_BRAINCO_OPEN_AT`).
The driver is the same one VR teleop uses; a misconfiguration yields a
`NullHandDriver` that explains itself rather than a guess that clenches.

The safety fact that shapes this module's API (from the hands docstring):
**a BrainCo has no firmware dead-man.** A closed hand stays closed until
something opens it. Teleop covers that with `relax()` on its falling edge;
here the matching releases are `open_hands`, `stop_everything` (which now
relaxes the hands), and nothing else — so every result of a closing command
says so out loud, and `set_hand` refuses nothing else on the caller's behalf.

Instant one-shot publishes — no task lifecycle, same shape as `say`.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Literal

import structlog

from bridge.teleop import hands

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

Side = Literal["left", "right", "both"]


def _requested_sides(side: Side) -> tuple[hands.Side, ...]:
    return ("left", "right") if side == "both" else (side,)


#: Finger names for `set_hand(fingers=...)`, in `hands.SHAPES` channel order
#: (BrainCo: Thumb, Thumb_aux, Index, Middle, Ring, Pinky).
FINGERS = ("thumb", "thumb_rotation", "index", "middle", "ring", "pinky")

_NO_SERVER = (
    "brainco_hand_server not running: no hand state from {sides} in the last second, so "
    "nothing moved that hand. It is started by hand after a reboot; start it, then retry."
)


async def run_set(
    side: Side,
    closure: float,
    ctx: Any | None = None,
    shape: str | None = None,
    fingers: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Set hand closure: 0.0 fully open .. 1.0 fully closed, or a named `hands.SHAPES` grasp
    (which then overrides `closure`). `fingers` overrides single fingers on top of that base.
    Returns a result dict; a bad finger name or value moves nothing."""
    if shape is not None and shape not in hands.SHAPES:
        return {"status": "failed", "error": f"unknown_shape_{shape}", "shapes": sorted(hands.SHAPES)}
    closure = max(0.0, min(1.0, float(closure)))
    closures = hands.SHAPES[shape] if shape is not None else None
    if fingers:
        unknown = sorted(set(fingers) - set(FINGERS))
        if unknown:
            return {"status": "failed", "error": "unknown_finger", "unknown": unknown, "fingers": list(FINGERS)}
        out = sorted(n for n, v in fingers.items() if not 0.0 <= float(v) <= 1.0)
        if out:
            return {"status": "failed", "error": "finger_closure_out_of_range", "out_of_range": out}
        base = closures if closures is not None else (closure,) * len(FINGERS)
        closures = tuple(float(fingers.get(n, b)) for n, b in zip(FINGERS, base, strict=True))
    if closures is not None:
        closure = max(closures)
    requested = _requested_sides(side)

    log.info(
        "set_hand.called", side=side, closure=closure, shape=shape, fingers=fingers, sim_mode=SIM_MODE
    )

    if SIM_MODE == "stub":
        return {
            "status": "ok",
            "side": side,
            "closure": closure,
            "shape": shape,
            "driver": "stub",
            "stub": True,
        }

    if SIM_MODE != "real":
        # No simulator we run subscribes the hand command topics; publishing
        # would be a silent no-op that reads as success — same honesty rule as
        # rt/arm_sdk.
        return {
            "status": "not_applicable",
            "message": (
                f"hand control is real-hardware-only (SIM_MODE={SIM_MODE}); "
                "nothing in sim subscribes rt/brainco/*/cmd or rt/dex3/*/cmd."
            ),
            "env": SIM_MODE,
        }

    driver = hands.get_driver()
    if not driver.sides:
        reason = getattr(driver, "reason", "no hand driver configured")
        return {
            "status": "unavailable",
            "message": f"hands are not configured: {reason}",
            "driver": driver.name,
        }

    configured = tuple(s for s in requested if s in driver.sides)
    skipped = tuple(s for s in requested if s not in driver.sides)
    # A publish with no hand server is a silent no-op that read as ok (2026-11-12).
    silent = [s for s in configured if not await asyncio.to_thread(driver.server_heard, s)]
    driven = tuple(s for s in configured if s not in silent)
    for s in driven:
        # DDS publish — blocking, so off the event loop like every other write.
        if closures is not None:
            await asyncio.to_thread(driver.send_shape, s, closures)
        else:
            await asyncio.to_thread(driver.send, s, closure)

    result: dict[str, Any] = {
        "status": "ok" if driven else "unavailable",
        "side": side,
        "closure": closure,
        "shape": shape,
        "driver": driver.name,
        "driven_sides": list(driven),
        "stub": False,
    }
    if closures is not None:
        result["fingers"] = dict(zip(FINGERS, closures, strict=True))
    if skipped:
        result["skipped_sides"] = list(skipped)
        result["message"] = (
            f"{', '.join(skipped)} not driven — not in TELEOP_HAND_SIDES "
            f"(configured: {', '.join(driver.sides)})."
        )
    if silent:
        result["silent_sides"] = list(silent)
        no_server = _NO_SERVER.format(sides=", ".join(silent))
        result["message"] = f"{result['message']} {no_server}" if skipped else no_server
    if driven and closure > 0.0:
        result["warning"] = (
            "This hand has no firmware dead-man: it stays gripped until "
            "open_hands (or stop_everything) is called."
        )
    return result


async def run_open(ctx: Any | None = None) -> dict[str, Any]:
    """Open every configured hand. The counterpart every grip relies on."""
    log.info("open_hands.called", sim_mode=SIM_MODE)

    if SIM_MODE == "stub":
        return {"status": "ok", "driver": "stub", "stub": True}

    if SIM_MODE != "real":
        return {
            "status": "not_applicable",
            "message": f"hand control is real-hardware-only (SIM_MODE={SIM_MODE}).",
            "env": SIM_MODE,
        }

    driver = hands.get_driver()
    if not driver.sides:
        reason = getattr(driver, "reason", "no hand driver configured")
        return {
            "status": "unavailable",
            "message": f"hands are not configured: {reason}",
            "driver": driver.name,
        }

    # Always sent (opening is the safe direction); only the report depends on who answered.
    await asyncio.to_thread(driver.relax)
    silent = [s for s in driver.sides if not await asyncio.to_thread(driver.server_heard, s)]
    opened = [s for s in driver.sides if s not in silent]
    result: dict[str, Any] = {
        "status": "ok" if opened else "unavailable",
        "driver": driver.name,
        "opened_sides": opened,
        "stub": False,
    }
    if silent:
        result["silent_sides"] = silent
        result["message"] = _NO_SERVER.format(sides=", ".join(silent))
    return result
