"""run_sequence: several arm, hand, gesture and speech steps as one action, confirmed once."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field, ValidationError, WithJsonSchema

from bridge import config, server
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


#: What run_sequence may chain: the arm, the hand, the gestures and speech. Not walking,
#: navigation or posture (long, or able to drop the robot), and not run_sequence itself.
SEQUENCE_TOOLS = (
    "move_arm",
    "set_hand",
    "open_hands",
    "release_arm_control",
    "wave",
    "point_at",
    "shake_hand",
    "hug",
    "clap",
    "release_arm",
    "dance",
    "gesture",
    "say",
)
SEQUENCE_MAX = 8
_SEQUENCE_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "maxItems": SEQUENCE_MAX,
    "items": {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": list(SEQUENCE_TOOLS)},
            "args": {"type": "object", "description": "That tool's own arguments."},
            "with_previous": {
                "type": "boolean",
                "description": (
                    "set_hand / open_hands only: start at the same time as the step before "
                    "(a move_arm or a gesture) instead of after it."
                ),
            },
        },
        "required": ["tool"],
    },
}


#: A with_previous step: the hand, alongside an arm move or a gesture (never speech or a release).
_SEQUENCE_PARTNERS = ("set_hand", "open_hands")
_SEQUENCE_LEADERS = tuple(
    t for t in SEQUENCE_TOOLS if t not in (*_SEQUENCE_PARTNERS, "say", "release_arm_control", "release_arm")
)


#: A sequence that leaves an arm raised lowers it after this long: on 2026-11-13 an arm held
#: forward made the standing robot wobble. 0 leaves it up until release_arm_control.
SEQUENCE_ARM_HOLD_S = float(os.environ.get("C3PO_SEQUENCE_ARM_HOLD_S", "8"))


def _step_failed(result: Any) -> bool:
    return isinstance(result, dict) and (
        result.get("status") in ("failed", "refused", "error", "cancelled")
        or bool(result.get("error"))
    )


def _leaves_arm_held(planned: list[tuple[str, dict[str, Any], dict[str, Any], Any, bool]]) -> bool:
    """A move_arm that holds (the default) with no release after it."""
    held = False
    for name, _args, checked, _tool, _with in planned:
        if name == "move_arm":
            held = checked.get("hold", True) is not False
        elif name in ("release_arm_control", "release_arm"):
            held = False
    return held


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=15.0,
        works_sim=True,
        works_real=False,  # each step's own status applies; the chain is new
        typical_failure_modes=["invalid_step", "step_refused", "step_failed"],
    )
)
async def run_sequence(
    ctx: Context,
    steps: Annotated[
        list[dict[str, Any]],
        WithJsonSchema(_SEQUENCE_SCHEMA),
        Field(
            description=(
                "Up to 8 steps run in order as ONE action with ONE operator confirmation, "
                "each {tool, args} with that tool's own arguments. Use it whenever the "
                "operator asks for two or more arm/hand/gesture/speech things together, "
                "e.g. raise the right arm and make the saludo peronista: "
                '[{"tool": "move_arm", "args": {"side": "right", "shoulder_pitch_deg": 80, '
                '"elbow_deg": 0, "wrist_roll_deg": -90}}, '
                '{"tool": "set_hand", "args": {"side": "right", "shape": "saludo_peronista"}, '
                '"with_previous": true}]. '
                "move_arm holds the pose, so a later hand step happens with the arm up; "
                "with_previous: true on a set_hand/open_hands step moves the hand at the same "
                "time as the arm move or gesture just before it. A raised arm comes down by "
                "itself about 8 s after the last step (balance): add a lowering step only if "
                "asked to lower it sooner."
            )
        ),
    ],
    keep_arm_up: Annotated[
        bool,
        Field(
            description=(
                "Only when the operator explicitly asks to keep the arm up: skip the "
                "automatic lowering. The arm then stays until release_arm_control."
            )
        ),
    ] = False,
) -> dict:
    """Several arm, hand, gesture and speech steps as one action, confirmed once.

    The operator's one «Confirmar» (or face) covers this exact list. Every step is
    checked before any moves (a known tool from SEQUENCE_TOOLS, arguments its own schema
    accepts, with_previous only on a hand step right after an arm move or gesture), and
    each still meets PARAR, the battery and the person rule as it starts
    (`safety.tool_refusal(..., confirmed=True)` skips only the per-step grant), with its
    own spoken warning. A hand step gets CLOSE_SETTLE_S to travel before the next step
    starts (one moving with_previous shares its leader's time instead).
    The chain stops at the first step refused or failed and says which.
    An arm it leaves raised comes down after SEQUENCE_ARM_HOLD_S unless keep_arm_up.
    """
    if not 1 <= len(steps) <= SEQUENCE_MAX:
        return {
            "status": "failed",
            "reason": "invalid_step",
            "detail": f"1 to {SEQUENCE_MAX} steps",
        }
    planned: list[tuple[str, dict[str, Any], dict[str, Any], Any, bool]] = []
    for i, step in enumerate(steps):
        name = str(step.get("tool") or "") if isinstance(step, dict) else ""
        args = (step.get("args") or {}) if isinstance(step, dict) else None
        tool = mcp.registered_tool(name) if name in SEQUENCE_TOOLS else None
        if tool is None or not isinstance(args, dict):
            return {"status": "failed", "reason": "invalid_step", "step": i, "tool": name}
        together = bool(step.get("with_previous"))
        if together and not (
            name in _SEQUENCE_PARTNERS
            and i > 0
            and planned[-1][0] in _SEQUENCE_LEADERS
            and not planned[-1][4]
        ):
            return {
                "status": "failed",
                "reason": "invalid_step",
                "step": i,
                "tool": name,
                "detail": "with_previous: only set_hand/open_hands, right after an arm move or a gesture",
            }
        try:
            checked = json.loads(server.canonical_args(tool, args))
        except ValidationError as exc:
            detail = str(exc)[:300]
            return {
                "status": "failed",
                "reason": "invalid_step",
                "step": i,
                "tool": name,
                "detail": detail,
            }
        planned.append((name, args, checked, tool, together))

    async def call(i: int) -> Any:
        name, args, _checked, _tool, _with = planned[i]
        try:
            return await mcp.call_tool_ungated(name, args, context=ctx)
        except Exception as exc:  # noqa: BLE001 - the chain reports which step, never half-silent
            return {"status": "failed", "error": str(exc)[:300], "_raised": True}

    done: list[dict[str, Any]] = []
    i = 0
    while i < len(planned):
        # A step and the hand steps marked to run with it: checked and announced together.
        group = [i] + ([i + 1] if i + 1 < len(planned) and planned[i + 1][4] else [])
        for k in group:
            name, _args, checked, tool, _with = planned[k]
            end = {"step": k, "tool": name, "done": done, "env": config.SIM_MODE}
            # PARAR, battery, the person rule, then the spoken warning: call_tool's gate for a
            # step the sequence's one confirmation already covers.
            if refusal := await server.confirmed_step_refusal(name, tool.meta, checked):
                return {"status": "refused", "reason": "step_refused", "detail": refusal, **end}
        if len(group) == 1:
            results = [await call(i)]
        else:
            lead = asyncio.create_task(call(i))
            await asyncio.sleep(0)  # the arm's first command goes out before the hand's
            partner = await call(group[1])
            results = [await lead, partner]
        for k, result in zip(group, results, strict=True):
            name = planned[k][0]
            end = {"step": k, "tool": name, "done": done, "env": config.SIM_MODE}
            if isinstance(result, dict) and result.pop("_raised", False):
                return {"status": "failed", "reason": "step_failed", "detail": result["error"], **end}
            done.append({"tool": name, "result": result})
            if _step_failed(result):
                return {"status": "failed", "reason": "step_failed", **end}
        i += len(group)
        if i < len(planned) and any(planned[k][0] in _SEQUENCE_PARTNERS for k in group):
            from bridge.skills.manipulation import CLOSE_SETTLE_S

            # set_hand only publishes a target: on 2026-11-16 a close then an open went out in
            # the same second and the fingers never visibly closed. with_previous is its group.
            await asyncio.sleep(CLOSE_SETTLE_S)
    out: dict[str, Any] = {"status": "completed", "done": done, "env": config.SIM_MODE}
    if not keep_arm_up and SEQUENCE_ARM_HOLD_S > 0 and _leaves_arm_held(planned):
        from bridge import estop

        stop_seen = estop.last_stop_at()
        waited = 0.0
        while waited < SEQUENCE_ARM_HOLD_S and estop.last_stop_at() == stop_seen:
            await asyncio.sleep(0.2)
            waited += 0.2
        if estop.last_stop_at() == stop_seen:  # a PARAR already released it
            out["arm_lowered"] = await call_release_after_sequence(ctx)
            out["arm_held_s"] = round(waited, 1)
    return out


async def call_release_after_sequence(ctx: Context) -> Any:
    try:
        return await mcp.call_tool_ungated("release_arm_control", {}, context=ctx)
    except Exception as exc:  # noqa: BLE001 - reported, the chain itself already completed
        return {"status": "failed", "error": str(exc)[:300]}
