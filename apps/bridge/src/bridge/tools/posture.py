"""Posture and motion-mode tools: one-shot FSM requests over the sport service."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta


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
        preconditions=["fsm_state_in_{preparation,walk,walk_waist,run,squat,zero_torque}"],
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

    return {**await run_g1_request("damp", ctx), "env": SIM_MODE}


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
    """Enter Preparation mode — required gateway to Walk / Walk(waist) / Run.

    On the G1 FSM: legal only from Damp. From Preparation, you can transition
    to Walk, Walk(waist), Run, or back to Damp.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("prepare", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
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

    return {**await run_g1_request("zero_torque", ctx), "env": SIM_MODE}


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

    return {**await run_g1_request("start_walking_waist", ctx), "env": SIM_MODE}


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

    return {**await run_g1_request("balance_stand", ctx), "env": SIM_MODE}


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

    return {**await run_g1_request("start_walking", ctx), "env": SIM_MODE}


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

    return {**await run_g1_request("sit_g1", ctx), "env": SIM_MODE}


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

    return {**await run_g1_request("lie_up", ctx), "env": SIM_MODE}


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

    return {**await run_g1_request("squat", ctx), "env": SIM_MODE}
