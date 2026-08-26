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


async def run_set(side: Side, closure: float, ctx: Any | None = None) -> dict[str, Any]:
    """Set hand closure: 0.0 fully open .. 1.0 fully closed. Returns a result dict."""
    closure = max(0.0, min(1.0, float(closure)))
    requested = _requested_sides(side)

    log.info("set_hand.called", side=side, closure=closure, sim_mode=SIM_MODE)

    if SIM_MODE == "stub":
        return {
            "status": "ok",
            "side": side,
            "closure": closure,
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

    driven = tuple(s for s in requested if s in driver.sides)
    skipped = tuple(s for s in requested if s not in driver.sides)
    for s in driven:
        # DDS publish — blocking, so off the event loop like every other write.
        await asyncio.to_thread(driver.send, s, closure)

    result: dict[str, Any] = {
        "status": "ok" if driven else "unavailable",
        "side": side,
        "closure": closure,
        "driver": driver.name,
        "driven_sides": list(driven),
        "stub": False,
    }
    if skipped:
        result["skipped_sides"] = list(skipped)
        result["message"] = (
            f"{', '.join(skipped)} not driven — not in TELEOP_HAND_SIDES "
            f"(configured: {', '.join(driver.sides)})."
        )
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

    await asyncio.to_thread(driver.relax)
    return {
        "status": "ok",
        "driver": driver.name,
        "opened_sides": list(driver.sides),
        "stub": False,
    }
