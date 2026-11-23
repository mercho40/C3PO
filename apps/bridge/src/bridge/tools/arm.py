"""The arms and hands under the bridge's own control: gesture, move_arm, set_hand and their releases."""

from __future__ import annotations

from typing import Annotated, Literal, cast

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field, WithJsonSchema

from bridge import config
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=6.0,
        works_sim=False,
        works_real=True,  # The 7106-by-id path is verified live (wave, 2026-08-15);
        # most individual ids in the catalogue have not been watched yet.
        preconditions=["arm_action_service_available", "rt_arm_sdk_not_engaged"],
        typical_failure_modes=[
            "unknown_gesture",
            "arm_latched_7401",
            "fsm_gated_7404",
            "transport_unsupported",
        ],
    )
)
async def gesture(
    ctx: Context,
    name: Annotated[
        str,
        Field(
            description=(
                "Firmware action name, one of: blow_kiss_both_hands, "
                "blow_kiss_left_hand, blow_kiss_right_hand, both_hands_up, "
                "both_hands_up_deviate_right, box_both_hand_win, box_left_hand_win, "
                "box_right_hand_win, clamp, forward_push, heart_both_hands, "
                "heart_right_hand, high_five, hug, refuse, release_arm, "
                "right_hand_on_heart, right_hand_up, shake_hand, turn_back_wave, "
                "ultraman_ray, wave_above_head, wave_under_head. Case-insensitive."
            ),
        ),
    ],
    auto_release: Annotated[
        bool,
        Field(
            description=(
                "True (default): after the gesture completes, hold the pose "
                "hold_s seconds and then send release_arm (99) automatically, "
                "so the arm is never left latched under motor load. False: "
                "leave the latch standing — you then OWN sending release_arm."
            ),
        ),
    ] = True,
    hold_s: Annotated[
        float,
        Field(
            ge=0.0,
            le=15.0,
            description="Seconds to hold the final pose before the automatic release.",
        ),
    ] = 2.0,
) -> dict:
    """Perform any preset arm gesture from the robot's own action catalogue — the arms move.

    The full table the G1's firmware reports via GetActionList (23 preset
    actions), not just the handful with dedicated tools. Same verified RPC
    path as `wave` (arm service, api_id=7106); the call blocks until the
    motion completes (the service acks on completion, up to ~15 s), then
    holds and auto-releases per the parameters above.

    Gating is per action: `turn_back_wave` needs a walk program (FSM 500/501);
    six actions need a 29/27-DoF body, which this robot is. A sustained
    gesture LATCHES the arm holding its final pose — the auto-release exists
    because a latched arm left standing is servos under load with nothing
    scheduled to let go (learned live 2026-08-27). Refused while
    move_arm/teleop holds the arms (rt/arm_sdk contention, error 7400).

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/arm/request`).
    """
    from bridge.skills.gesture import run as run_gesture

    return {
        **await run_gesture(name, ctx, auto_release=auto_release, hold_s=hold_s),
        "env": config.SIM_MODE,
    }


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
            # Labels, not raw ids: 4/500/501 are preparation/walk/walk_waist, and
            # every other tool in this file states them by name. Two vocabularies
            # for one concept is a thing the agent reading this has to guess at.
            "fsm_state_in_{preparation,walk,walk_waist}",
            "fresh_rt_lowstate",
            "no_gesture_task_running",
        ],
        typical_failure_modes=[
            "arm_teleop_disabled",
            "fsm_not_allowed",
            "stale_lowstate",
            "gesture_contention",
            "settle_timeout",
            "teleop_active",
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
        Field(
            ge=0,
            le=110,
            description=(
                "Elbow, degrees, URDF convention: 0 = bent about 90° (forearm square to the "
                "upper arm); larger straightens it, about 90 is nearly straight."
            ),
        ),
    ] = None,
    wrist_roll_deg: Annotated[
        float | None,
        Field(
            ge=-90,
            le=90,
            description=(
                "Wrist roll (turns the forearm), degrees. Sign convention UNVERIFIED. "
                "±90 turns the palm a quarter turn, e.g. to face outward for the saludo peronista."
            ),
        ),
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
    return {**await run_move_arm(side, joints_deg, hold=hold, ctx=ctx), "env": config.SIM_MODE}


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

    return {**await run_release(), "env": config.SIM_MODE}


_FINGER_NAMES = ("thumb", "thumb_rotation", "index", "middle", "ring", "pinky")


# Published inline as explicit numeric properties (no $ref, no propertyNames): given a
# free-form dict, the ia-tic model sent {"index": "0"}, a string, and apps/back refused it.
_FINGERS_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                f: {"type": "number", "minimum": 0.0, "maximum": 1.0} for f in _FINGER_NAMES
            },
            "additionalProperties": False,
        },
        {"type": "null"},
    ]
}


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
    shape: Annotated[
        Literal["open", "power", "pinch", "hook", "point", "v_sign", "saludo_peronista"] | None,
        Field(
            description=(
                "A named grasp instead of `closure`: power = wrap, pinch = thumb+index, hook = handles, "
                "point = index up (ONE finger, for pointing), v_sign = index and middle up, "
                "the rest closed («la V», «la V de la victoria»); saludo_peronista = the same "
                "as v_sign: use it for «saludo peronista». A «saludo peronista» is the WHOLE "
                "gesture, never the hand alone: run_sequence with move_arm {side, "
                "shoulder_pitch_deg: 80, elbow_deg: 0, wrist_roll_deg: -90} (forearm up, palm "
                "to the audience), then this shape with with_previous: true."
            )
        ),
    ] = None,
    fingers: Annotated[
        dict[
            Literal["thumb", "thumb_rotation", "index", "middle", "ring", "pinky"],
            Annotated[float, Field(ge=0.0, le=1.0)],
        ]
        | None,
        WithJsonSchema(_FINGERS_SCHEMA),
        Field(
            description=(
                "Single fingers, 0.0 open .. 1.0 closed, on top of `shape` (or `closure`). "
                "Fingers not named keep that base. thumb_rotation swings the thumb across the palm."
            )
        ),
    ] = None,
) -> dict:
    """Set a dexterous hand's grip — physical fingers move (BrainCo Revo2).

    One closure per hand, 0.0 open to 1.0 closed, published to the hand's
    command topic. `fingers` moves single fingers: «cerrá solo el índice» is
    fingers={"index": 1.0}; «abrí el pulgar» is fingers={"thumb": 0.0};
    «cerrá todo menos el índice» is closure=1.0, fingers={"index": 0.0};
    «cerrá un poco el meñique» is fingers={"pinky": 0.3}. Fingers you do not
    name keep `shape`'s value, or `closure` (default 0.0 = open). IMPORTANT: these hands have NO firmware dead-man —
    a closed hand stays closed until open_hands (or stop_everything, which now
    relaxes them) is called, even if the bridge dies. Never leave a grip
    closed on a person.

    Requires TELEOP_HAND_ENABLED=1 plus the hand type/polarity env config
    (bridge/teleop/hands.py documents every knob); reports honestly when
    unconfigured instead of guessing. Real hardware only — nothing in sim
    subscribes the hand topics.
    """
    from bridge.skills.hand import run_set

    # dict keys are invariant: the Literal-keyed schema dict is a plain str-keyed one to run_set.
    return {**await run_set(side, closure, shape=shape, fingers=cast("dict[str, float] | None", fingers)), "env": config.SIM_MODE}


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

    return {**await run_open(), "env": config.SIM_MODE}
