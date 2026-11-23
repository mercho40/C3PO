"""Arm manipulation planned from the camera: pick, place, handover, reach, arm_state."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field

from bridge import config
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ===========================================================================
# BEGIN auto/manipulation — reach / pick / place / handover / arm_state
# ===========================================================================
#
# PLAN Phase 5, classical (no learned policy): IK over the vendored URDF
# (bridge/kinematics.py) and the same rt/arm_sdk driver move_arm uses. All but
# arm_state are T2 (gesture + high danger): an operator confirms each call, and
# safety.tool_refusal blocks them with a person in reach, except `handover`,
# whose point is a person. skills/manipulation.py documents the rest.

_ARM_META: dict[str, Any] = dict(
    classification="gesture",
    danger_level="high",
    status="real",
    cancellable=True,
    works_sim=False,
    # NEVER LIVE-TESTED. docs/autonomy/test-day/manipulation.md first.
    works_real=False,
    preconditions=[
        "real_hardware_only",
        "TELEOP_ARM_ENABLED=1",
        "fsm_state_in_{preparation,walk,walk_waist}",
        "standing_still",
        "fresh_rt_lowstate",
        "no_gesture_task_running",
    ],
    typical_failure_modes=[
        "out_of_reach",
        "outside_workspace",
        "person_in_reach",
        "base_moving",
        "settle_timeout",
        "hand_unavailable",
        "teleop_active",
    ],
)


#: pick and place also refuse on the robot until C3PO_ALLOW_PICK=1 (skills/manipulation.py).
_PICK_META = {
    **_ARM_META,
    "typical_failure_modes": [*_ARM_META["typical_failure_modes"], "pick_disabled_until_gantry_test"],
}


_SideAuto = Annotated[
    Literal["auto", "left", "right"],
    Field(description="Which arm. auto = the arm on the object's side whose hand is configured."),
]


_Side = Annotated[Literal["left", "right"], Field(description="Which arm.")]


@mcp.tool(meta=skill_meta(**{**_PICK_META, "expected_duration_s": 15.0}))
async def pick(
    ctx: Context,
    label: Annotated[
        str | None,
        Field(
            description=(
                "What to pick: the detector's label exactly ('cup', 'bottle'), or its plain Spanish "
                "word ('taza'). No partial matches: 'Dixie cup' does not pick a 'cup'."
            )
        ),
    ] = None,
    side: _SideAuto = "auto",
    shape: Annotated[
        Literal["power", "pinch", "hook"],
        Field(description="power = wrap (cup, bottle, remote); pinch = small/thin (keys, watch, wallet); hook = handles."),
    ] = "power",
    track_id: Annotated[
        int | None,
        Field(
            description=(
                "Pick this exact detection; wins over label. A pick that misses lists the "
                "graspable detections with their track_id, range and bearing."
            )
        ),
    ] = None,
) -> dict:
    """Pick up an object the head camera sees right now — a physical arm and hand move.

    Needs a FRESH detection of it with depth (turn toward it / look first). Plans the
    whole motion before moving: curl the hand, raise it beside the body (shoulder back,
    elbow up) to 8 cm above the object, forward over the surface, straight down to
    pre-grasp 10 cm short, open the hand, grasp, close, lift 8 cm, then HOLD. Refuses without moving
    if any pose is out of reach — the G1 arm is short: ~30 cm in front of the
    shoulder, at counter height — and then says how far to walk (walk_velocity) to
    make it reachable. There is no touch sensing: call verify('¿tenés X en la mano?')
    afterwards. Then place, handover, or open_hands.
    """
    from bridge.skills import manipulation

    return {**await manipulation.pick(label, side, shape, track_id, ctx=ctx), "env": config.SIM_MODE}


@mcp.tool(meta=skill_meta(**{**_PICK_META, "expected_duration_s": 12.0}))
async def place(
    ctx: Context,
    side: _Side = "right",
    onto: Annotated[
        str | None,
        Field(description="A detected surface/object to put it on, by its exact label ('dining table', 'plate'). Omit to set it down right below the hand."),
    ] = None,
    drop_m: Annotated[
        float, Field(ge=0.0, le=0.15, description="Without `onto`: how far to lower before opening.")
    ] = 0.05,
) -> dict:
    """Put down what the hand holds: lower, open, lift clear, pull back toward the body, and
    hand the arm back (it lowers beside the body)."""
    from bridge.skills import manipulation

    return {**await manipulation.place(side, onto, drop_m, ctx=ctx), "env": config.SIM_MODE}


@mcp.tool(meta=skill_meta(**{**_ARM_META, "expected_duration_s": 8.0}))
async def handover(ctx: Context, side: _Side = "right") -> dict:
    """Hold the hand out at chest height so a person can take what it holds.

    The only arm skill allowed with a person in reach. It does NOT open the hand: when
    the person says they have it, call set_hand(side, shape='open'), then
    release_arm_control.
    """
    from bridge.skills import manipulation

    return {**await manipulation.handover(side, ctx=ctx), "env": config.SIM_MODE}


@mcp.tool(meta=skill_meta(**{**_ARM_META, "expected_duration_s": 8.0}))
async def reach(
    ctx: Context,
    x_m: Annotated[float, Field(ge=-1.0, le=1.5, description="Forward (torso frame) or optical z (camera frame).")],
    y_m: Annotated[float, Field(ge=-1.0, le=1.0)],
    z_m: Annotated[float, Field(ge=-1.0, le=1.5)],
    side: _Side = "right",
    frame: Annotated[
        Literal["torso", "camera"],
        Field(description="torso: +x forward, +y left, +z up from torso_link. camera: D435 optical (x right, y down, z ahead)."),
    ] = "torso",
    fingers: Annotated[Literal["forward", "down", "free"], Field(description="Which way the fingers point.")] = "forward",
    hold: bool = True,
) -> dict:
    """Move one hand to a 3-D point by inverse kinematics — a physical arm moves.

    For pointing at / touching things and for calibration. Refuses without moving if
    the point is out of reach or outside the workspace box in front of the chest.
    """
    from bridge.skills import manipulation

    return {
        **await manipulation.reach(side, (x_m, y_m, z_m), frame, fingers, hold, ctx=ctx),
        "env": config.SIM_MODE,
    }


@mcp.tool(
    meta=skill_meta(
        classification="introspection",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=False,
    )
)
async def arm_state() -> dict:
    """Where each hand is (forward kinematics of the measured joints, torso frame). Moves nothing."""
    from bridge.skills import manipulation

    return {**manipulation.arm_state(), "env": config.SIM_MODE}


# ===========================================================================
# END auto/manipulation
# ===========================================================================
