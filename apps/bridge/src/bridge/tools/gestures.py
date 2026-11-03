"""Preset gesture tools: one-shot arm-service actions and the dance catalogue."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.tools import _background


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def wave(ctx: Context) -> dict:
    """Wave the upper arm — friendly greeting gesture.

    G1 firmware `wave_above_head` (api_id=7106, data=26). The robot's own
    GetActionList reports this action as UNGATED — no FSM and no mode_machine
    requirement — and it executed on hardware 2026-08-15 from fsm_id 802,
    taking 7.3 s because the arm service acks on completion of the motion, not
    on receipt. Only one action in the whole table (`turn_back_wave`, id 1) is
    FSM-gated.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/arm/request`).
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("wave", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        # Was True while this tool's own docstring said the gesture had never
        # worked on hardware — the precise contradiction `skill_meta.py` warns
        # about, since `works_real` is supposed to mean a human watched it run.
        # Nobody has. Corrected to False rather than to a hoped-for True.
        works_real=False,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def point_at(ctx: Context) -> dict:
    """Extend the arm forward — the closest thing to pointing.

    Dispatches vendor action **36** (`forward_push`, arm service api_id=7106),
    per `g1_protocol.SKILL_REQUESTS["point_at"]`. There is no "point" in
    Unitree's action table; this is the nearest real action.

    History, because this id has moved twice and the reasoning matters: 36 was
    briefly replaced by 23 on the belief that 36 "appears in no vendor
    artifact". That was wrong — the ROBOT's own `GetActionList` (read live
    2026-08-15) does list 36, gated on `mode_machine` [5, 6], and this robot
    reports 5. The published table is simply incomplete for this build, so 36
    is correct and is what ships. See `g1_protocol.py`'s Gesture docstring.

    **Never executed on this hardware** — neither id has been dispatched here,
    which is why `works_real` is False.

    Like `wave`, needs an FSM state that permits arm actions.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("point_at", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def shake_hand(ctx: Context) -> dict:
    """Extend the right hand for a handshake.

    G1 firmware "shake hands" (api_id=7106, data=27). Requires a locomotion-
    active FSM state (Walk / Walk(waist) / Run) on real hardware.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("shake_hand", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def hug(ctx: Context) -> dict:
    """Open arms wide for a hug.

    G1 firmware "hug" (api_id=7106, data=19). Requires a locomotion-active
    FSM state. The hug gesture is one of the four that the firmware hides
    while in Run mode — prefer Walk or Walk(waist) for this one.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("hug", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=2.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def clap(ctx: Context) -> dict:
    """Bring hands together to clap.

    G1 firmware "clap" (api_id=7106, data=17). Requires a locomotion-active
    FSM state.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("clap", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def release_arm(ctx: Context) -> dict:
    """Return arms to a neutral / idle position.

    G1 firmware "release arm" (api_id=7106, data=99). Call this after any
    other gesture to settle the arms back to their default hanging pose.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("release_arm", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=True,
        expected_duration_s=20.0,
        works_sim=False,
        works_real=False,  # NOT YET LIVE-TESTED -- new sequence, see the skill's own docstring.
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=[
            "fsm_not_locomotion_state",
            "arm_latched_7401",
            "transport_unsupported",
        ],
    )
)
async def dance(
    ctx: Context,
    wait: Annotated[bool, Field(description=_background.WAIT_DESCRIPTION)] = True,
) -> dict:
    """Run a short choreographed gesture sequence.

    Not a single firmware mode -- `Mode.DANCE` (503) is unverified and
    unwired. Instead sequences several already-verified `Gesture` ids
    (BOTH_HANDS_UP, HIGH_FIVE, WAVE_UNDER_HEAD) through the same
    `call_arm()` primitive `wave`/`clap`/`hug` already use, interleaved with
    RELEASE_ARM to respect the arm's per-gesture latch (error 7401
    otherwise). Requires a locomotion-active FSM state, same as every other
    arm gesture. Cancellable between steps.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/arm/request`).

    NOT YET LIVE-TESTED against real hardware -- the first live call should
    be watched closely, same caution as `walk_velocity`.

    With `wait=false` it returns the task_id immediately and dances in the
    background — poll `get_task` / `wait_task`, `cancel_task` between steps.
    """
    from bridge.skills.dance import run as run_dance

    if not wait:
        task = _background.create("dance")
        return _background.start(task, lambda: run_dance(task=task))

    return {**await run_dance(ctx), "env": SIM_MODE}


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
        "env": SIM_MODE,
    }
