"""The G1's own high-level posture and gesture skills: one request each to the firmware."""

from __future__ import annotations

import asyncio

import structlog
from mcp.server.fastmcp import Context

from bridge import config
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tools: G1 high-level posture + gesture skills
# ---------------------------------------------------------------------------
# Each of these wraps a request to either `rt/api/sport/request` (api_id=7101,
# full-body modes) or `rt/api/arm/request` (api_id=7106, upper-limb gestures).
# Isaac Sim's `unitree_sim_isaaclab` scene doesn't subscribe to those topics,
# so under SIM_MODE=isaac the dispatcher returns `phase=logged_only` — the
# request was constructed but not delivered. Under SIM_MODE=real these
# dispatch for real over DDS RPC (`bridge.sdk.g1_rpc`); no WebRTC involved.
#
# Adding more skills (zero_torque, sit_g1, lie_up, squat, shake_hand, hug,
# clap, release_arm, …) is a one-liner — see g1_protocol.SKILL_REQUESTS for
# the full catalogue.


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=True,
        # `squat_up`, NOT `squat`. The `squat` SKILL sends SQUAT_UP (706) —
        # deliberately, see its docstring — so the robot reports `squat_up`,
        # and mode 2 (`squat`) is an index this bridge never sends. Naming it
        # here described a state nothing can produce, which is the sort of
        # thing free text in a metadata dict gets away with until somebody
        # wires it up. `test_fsm_preconditions.py` now checks it.
        preconditions=["fsm_state_in_{preparation,walk,walk_waist,run,squat_up,zero_torque}"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def damp(ctx: Context) -> dict:
    """Engage damping mode — set all joints to zero stiffness. Safety transition.

    On the G1 FSM: only legal from Preparation, Walk, Walk(waist), Run, Squat,
    ZeroTorque. From Damp you can transition to ZeroTorque, Preparation,
    SquatUp, or LieUp. This is the canonical "come to rest" target.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/sport/request`).
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("damp", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=2.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def prepare(ctx: Context) -> dict:
    """Enter Preparation mode — the gateway from Damp to Walk / Walk(waist) / Run.

    On the G1 FSM: legal only from Damp. From Preparation, you can transition
    to Walk, Walk(waist), Run, or back to Damp. Already walking (FSM 500/501)?
    Do not call it: gestures and shake_hand work from there, and it returns
    phase already_in_locomotion without sending anything.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("prepare", ctx), "env": config.SIM_MODE}


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

    return {**await run_g1_request("wave", ctx), "env": config.SIM_MODE}


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

    return {**await run_g1_request("point_at", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        # High so needs_confirmation gates it: a limp robot that is not on the gantry falls.
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=False,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def zero_torque(ctx: Context) -> dict:
    """Enter Zero Torque mode — actuators receive no command. Limp robot.

    On the G1 FSM: legal only from Damp. From Zero Torque you can only go
    back to Damp. Use as the "fully off" terminal state when storing the
    robot or stepping away.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("zero_torque", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="introspection",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=False,
        typical_failure_modes=["bridge_disconnected", "motion_switcher_no_answer"],
    )
)
async def check_motion_mode() -> dict:
    """Which motion controller currently owns the robot. Read-only.

    **Run this first whenever the robot accepts commands and does nothing.**

    The sport service answers `code 0` both when an FSM transition is refused
    and when there is no controller loaded to perform it, so those two very
    different situations are indistinguishable from the reply alone. This tool
    tells them apart in one call.

    Returns `mode_name`. Empty means **no controller is loaded** — the robot is
    in debug mode, nothing will move, and `get_state` will report `fsm_id=null`
    and `posture="unknown"`. That is normal after a teleoperation session:
    `xr_teleoperate` releases the mode on the way in and does not restore it.

    Recovering from that means `SelectMode('ai')`, which this bridge
    deliberately cannot send — loading a controller onto a robot someone else
    may be driving is an operator decision, not a tool call. See
    `docs/ROBOT-API.md` §3.
    """
    log.info("check_motion_mode.called", sim_mode=config.SIM_MODE)

    if config.SIM_MODE != "real":
        return {
            "mode_name": None,
            "note": f"SIM_MODE={config.SIM_MODE} has no motion_switcher service.",
            "env": config.SIM_MODE,
        }


    from bridge.sdk import g1_rpc

    code, result = await asyncio.to_thread(g1_rpc.check_motion_mode)
    if result is None:
        return {"mode_name": None, "rpc_code": code, "error": "no_answer", "env": config.SIM_MODE}

    name = result["name"]
    return {
        "mode_name": name,
        "form": result["form"],
        "rpc_code": code,
        "controller_loaded": bool(name),
        "note": (
            "A controller is loaded; FSM commands should be acted on."
            if name
            else "NO controller loaded — nothing will move, and the sport "
            "service will still answer code 0. Needs SelectMode('ai'), which "
            "an operator must send."
        ),
        "env": config.SIM_MODE,
    }


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_preparation", "operator_present"],
        typical_failure_modes=["fsm_transition_rejected", "no_motion_controller_loaded"],
    )
)
async def start_walking_waist(ctx: Context) -> dict:
    """Enter Walk Motion-3Dof-waist (FSM 501) — the 29-DoF walk program.

    500 and 501 are two different walk programs chosen by how many degrees of
    freedom the waist has, not a generic start and a variant. Unitree documents
    `mode_machine` as `4:23-Dof; 5:29-Dof; 6:27-Dof`, and this robot reports
    **5** — so 501 is the program it actually implements, and 500 the
    other variant's.

    That matters because `start_walking` (500) has been observed returning rpc
    code 0 while never leaving StandUp. A recognised-but-not-implemented id
    looks exactly like that: accepted, then declined by the controller.

    **Solved: 501 IS this robot's walk program — it walked under it on
    2026-08-15.** Still enter it from a supervised window with the operator
    ready to damp, not from an autonomous plan. See
    `docs/ROBOT-API.md` §12.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("start_walking_waist", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=False,
        preconditions=["robot_upright"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def balance_stand(ctx: Context) -> dict:
    """Engage the stand-and-balance controller (api_id=7102, balance_mode=0).

    The vendor's `BalanceStand()`. Distinct from the FSM postures above: it
    sets the *balance controller* mode rather than requesting a state change,
    so it goes to api_id 7102, not 7101.

    Why it exists as a tool: on 2026-08-12, with the robot standing and
    bearing its own weight, `start_walking` (the vendor's `Start()`,
    SetFsmId 500) returned code 0 and did **not** leave StandUp — and arm
    gestures then failed 7404 FSM_UNAVAILABLE. This is the one call in
    `g1_loco_client.hpp` between StandUp and Start that we had never sent.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("balance_stand", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=2.0,
        works_sim=False,
        works_real=False,
        preconditions=["fsm_state_is_preparation"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def start_walking(ctx: Context) -> dict:
    """Enter Walk mode — locomotion FSM activates; arm gestures become available.

    On the G1 FSM: legal only from Preparation. Until you call this, walk_to /
    turn / wave / point_at won't get a meaningful response on real hardware.
    Typical sequence: damp → prepare → start_walking → walk_to / wave.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("start_walking", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=False,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def sit_g1(ctx: Context) -> dict:
    """Enter Seating mode — robot adopts a seated posture.

    G1 firmware mode index 3 on api_id=7101. Typically reached from Damp;
    the exact accepted-from-set depends on firmware. Use for a calm,
    low-energy presentation state.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("sit_g1", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=4.0,
        works_sim=False,
        works_real=False,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def lie_up(ctx: Context) -> dict:
    """Enter Lie-Up mode — robot transitions to a face-up lying pose.

    G1 firmware mode index 702 on api_id=7101. Legal target from Damp.
    Useful pre-storage state and starting pose for recovery sequences.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("lie_up", ctx), "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=False,
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def squat(ctx: Context) -> dict:
    """Enter Squat mode — robot crouches to a lowered stance.

    G1 firmware Squat (mode 2) and SquatUp (706) collapse to the same
    physical pose at different control gains. From Squat you can only go to
    Damp on the FSM, so this is a terminal posture until you reset.

    ⚠️ Observed 2026-08-15: sending this from Damp returns rpc code 0 and does
    NOT transition — `fsm_id` stays 1. That contradicts Unitree's own G1
    example, which bring-ups via Damp -> Squat2StandUp(706) -> Move. On this
    robot the route that works is `prepare` (4), then 501. Do not build a
    bring-up on 706 here.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("squat", ctx), "env": config.SIM_MODE}


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

    return {**await run_g1_request("shake_hand", ctx), "env": config.SIM_MODE}


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

    return {**await run_g1_request("hug", ctx), "env": config.SIM_MODE}


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

    return {**await run_g1_request("clap", ctx), "env": config.SIM_MODE}


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

    return {**await run_g1_request("release_arm", ctx), "env": config.SIM_MODE}


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
async def dance(ctx: Context) -> dict:
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
    """
    from bridge.skills.dance import run as run_dance

    return {**await run_dance(ctx), "env": config.SIM_MODE}
