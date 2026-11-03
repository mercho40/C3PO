"""Free-form arm and hand tools (`rt/arm_sdk`, `rt/brainco` | `rt/dex3`)."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import Field

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta


# ---------------------------------------------------------------------------
# Tools: custom arm + hand control (rt/arm_sdk, rt/brainco|dex3)
# ---------------------------------------------------------------------------
# Free-form counterparts to the preset gestures above. move_arm blends
# joint-space setpoints into the running controller through the same driver VR
# teleop uses (bridge/teleop/arm_sdk.py — every safety measure lives there);
# set_hand/open_hands drive the BrainCo grippers (bridge/teleop/hands.py).


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="high",
        status="real",
        cancellable=True,
        expected_duration_s=5.0,
        works_sim=False,
        works_real=False,  # NEVER LIVE-TESTED, and the wrist/left-arm joint
        # signs are unverified — see bridge/teleop/arm_sdk.py's enablement note.
        preconditions=[
            "real_hardware_only",
            "TELEOP_ARM_ENABLED=1",
            "fsm_state_in_{4,500,501}",
            "fresh_rt_lowstate",
            "no_gesture_task_running",
        ],
        typical_failure_modes=[
            "arm_teleop_disabled",
            "fsm_not_allowed",
            "stale_lowstate",
            "gesture_contention",
            "settle_timeout",
        ],
    )
)
async def move_arm(
    ctx: Context,
    side: Annotated[
        Literal["left", "right", "both"],
        Field(description="Which arm(s) to pose. 'both' mirrors the angles onto each arm."),
    ] = "right",
    shoulder_pitch_deg: Annotated[
        float | None,
        Field(ge=-90, le=90, description="Shoulder pitch, degrees. Positive = arm forward/up."),
    ] = None,
    shoulder_roll_deg: Annotated[
        float | None,
        Field(ge=-10, le=90, description="Shoulder roll, degrees. Positive = away from the body."),
    ] = None,
    shoulder_yaw_deg: Annotated[
        float | None,
        Field(ge=-45, le=45, description="Shoulder yaw, degrees."),
    ] = None,
    elbow_deg: Annotated[
        float | None,
        Field(ge=0, le=110, description="Elbow flexion, degrees. 0 = straight arm."),
    ] = None,
    wrist_roll_deg: Annotated[
        float | None,
        Field(ge=-45, le=45, description="Wrist roll, degrees. Sign convention UNVERIFIED."),
    ] = None,
    wrist_pitch_deg: Annotated[
        float | None,
        Field(ge=-30, le=30, description="Wrist pitch, degrees. Sign convention UNVERIFIED."),
    ] = None,
    wrist_yaw_deg: Annotated[
        float | None,
        Field(ge=-30, le=30, description="Wrist yaw, degrees. Sign convention UNVERIFIED."),
    ] = None,
    hold: Annotated[
        bool,
        Field(
            description=(
                "True (default): keep holding the pose under software control "
                "until release_arm_control / another move_arm / stop_everything. "
                "False: hand the arm back to the built-in controller once posed."
            ),
        ),
    ] = True,
) -> dict:
    """Move individual arm joints to explicit angles — free-form posing, a physical arm moves.

    Blends joint setpoints into the running motion controller over rt/arm_sdk
    (the robot keeps its balance; legs stay under firmware control; works
    while merely standing at FSM 4/500/501). Joints you omit stay where they
    are, so partial commands compose. Motion is slew-limited to ~34°/s with a
    2 s authority ramp on engage.

    Disabled unless TELEOP_ARM_ENABLED=1 and SIM_MODE=real — joint sign
    conventions are only partly verified (right shoulder/elbow measured
    2026-08-20; wrists and left arm inferred). NEVER LIVE-TESTED end to end:
    first use should be one small single-joint move with a person watching.
    Preset gestures cannot run while this holds the arms (error 7400) — call
    release_arm_control first.
    """
    from bridge.skills.arm_pose import run as run_move_arm

    joints_deg = {
        name: value
        for name, value in {
            "shoulder_pitch": shoulder_pitch_deg,
            "shoulder_roll": shoulder_roll_deg,
            "shoulder_yaw": shoulder_yaw_deg,
            "elbow": elbow_deg,
            "wrist_roll": wrist_roll_deg,
            "wrist_pitch": wrist_pitch_deg,
            "wrist_yaw": wrist_yaw_deg,
        }.items()
        if value is not None
    }
    return {**await run_move_arm(side, joints_deg, hold=hold, ctx=ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=False,  # Same untested path as move_arm.
        typical_failure_modes=["release_timeout"],
    )
)
async def release_arm_control() -> dict:
    """Hand the arms back to the built-in controller after move_arm.

    Ramps the rt/arm_sdk blend weight to zero over ~2 s while holding the last
    commanded pose, then the firmware's own controller owns the arms again.
    Safe no-op when nothing is engaged. This is about the move_arm/teleop
    path — to un-latch a preset gesture (error 7401), use `release_arm`
    (firmware action 99) instead.
    """
    from bridge.skills.arm_pose import release as run_release

    return {**await run_release(), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=False,  # NEVER LIVE-TESTED — and BrainCo's open/closed
        # polarity is unconfirmed until TELEOP_BRAINCO_OPEN_AT is settled.
        preconditions=[
            "real_hardware_only",
            "TELEOP_HAND_ENABLED=1",
            "TELEOP_HAND_TYPE_configured",
        ],
        typical_failure_modes=["hands_not_configured", "side_not_configured"],
    )
)
async def set_hand(
    side: Annotated[
        Literal["left", "right", "both"],
        Field(description="Which hand. Only sides in TELEOP_HAND_SIDES actually move."),
    ] = "right",
    closure: Annotated[
        float,
        Field(
            ge=0.0,
            le=1.0,
            description="0.0 = fully open, 1.0 = fully closed. Intermediate values are partial grips.",
        ),
    ] = 0.0,
) -> dict:
    """Set a dexterous hand's grip — physical fingers move (BrainCo Revo2).

    One closure scalar per hand, 0.0 open to 1.0 closed, published to the
    hand's command topic. IMPORTANT: these hands have NO firmware dead-man —
    a closed hand stays closed until open_hands (or stop_everything, which now
    relaxes them) is called, even if the bridge dies. Never leave a grip
    closed on a person.

    Requires TELEOP_HAND_ENABLED=1 plus the hand type/polarity env config
    (bridge/teleop/hands.py documents every knob); reports honestly when
    unconfigured instead of guessing. Real hardware only — nothing in sim
    subscribes the hand topics.
    """
    from bridge.skills.hand import run_set

    return {**await run_set(side, closure), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=False,  # Same untested path as set_hand.
        typical_failure_modes=["hands_not_configured"],
    )
)
async def open_hands() -> dict:
    """Open every configured hand fully — the release for set_hand.

    Publishes closure 0.0 to each hand in TELEOP_HAND_SIDES. Call this after
    any grip: the BrainCo hands have no firmware dead-man, so nothing else
    (except stop_everything) will open them.
    """
    from bridge.skills.hand import run_open

    return {**await run_open(), "env": SIM_MODE}
