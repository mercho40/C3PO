"""Shared dispatcher for G1 high-level RPC skills.

Posture changes (damp / prepare / zero_torque / sit_g1 / lie_up / squat / …)
and arm gestures (wave / point_at / shake_hand / hug / clap / …) all hit the
same two topics on the real G1:

    rt/api/sport/request   (api_id=7101, param={"data": <mode_index>})
    rt/api/arm/request     (api_id=7106, param={"data": <gesture_index>})

`g1_protocol.SKILL_REQUESTS` already holds every (topic_kind, api_id, data)
triple. This module is the one place that turns a skill name into a Task,
checks the transport mode, and dispatches.

Today:
- Isaac Sim's `unitree_sim_isaaclab` scene does NOT subscribe to either
  request topic, so for `SIM_MODE=isaac` we log the intended dispatch and
  return `status=completed phase=logged_only`. Honest stub — no false
  motion claims.
- For `SIM_MODE=real`, we dispatch a live RPC via `bridge.sdk.g1_rpc`
  (`call_sport_api`/`call_arm`) on the resolved topic — this actually
  moves the robot. No client-side FSM precondition check runs here: the
  transition-rule data in `g1_protocol.py` is reference material, not a
  gate — a client-side rule built on partly-unverified sources could refuse
  a transition the firmware would have accepted, turning a bridge bug into
  a false "the robot can't do that". The firmware already rejects illegal
  transitions itself and says so (error 7302, "Invalid fsm id"), which is
  the answer we actually want.
- For `SIM_MODE=stub`, we return a clean stub result.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog

from bridge.sdk import g1_protocol
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

#: How long to wait for a posture change to actually land before reporting that
#: it did not. Measured on hardware: damp -> prepare and prepare -> 501 both
#: settle within ~7 s, so this is generous rather than tight — the cost of
#: waiting is a slower skill, the cost of not waiting is reporting success for
#: something that never happened.
FSM_SETTLE_TIMEOUT_S = 9.0


async def _await_fsm(target: int, timeout_s: float) -> int | None:
    """Poll the FSM until it reaches `target`, or the timeout expires.

    Returns whatever the FSM ended up as — including None when the poller has
    nothing, which is itself the answer that no controller is loaded.
    """
    import asyncio

    from bridge.sdk.state import get_sampler

    deadline = time.time() + timeout_s
    latest: int | None = None
    while time.time() < deadline:
        try:
            latest = get_sampler().get_arm_state().get("fsm_id")
        except Exception as exc:
            # Verification is diagnostic, never load-bearing. If the state
            # sampler cannot be read — no DDS participant, a test harness, a
            # bridge whose subscriptions have died — the dispatch already
            # succeeded and must not be reported as a failure because the
            # CHECK failed. Return None, which the caller reads as
            # "unverified" rather than "did not transition".
            log.debug("g1_request.fsm_check_unavailable", error=str(exc))
            return None
        if latest == target:
            return latest
        await asyncio.sleep(0.25)
    return latest


SIM_MODE = os.environ.get("SIM_MODE", "stub")


async def run_g1_request(
    skill_name: g1_protocol.SkillName,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Dispatch a high-level G1 RPC skill via the catalogue.

    Returns the full task dict (see `Task.to_dict`). Honest stub on sim:
    `phase=logged_only` indicates the request was constructed but no
    motion was attempted (Isaac Sim doesn't subscribe to the request
    topics in the current scene).
    """
    request = g1_protocol.SKILL_REQUESTS[skill_name]
    topics = g1_protocol.topics_for(SIM_MODE)
    topic = topics.sport_request if request.topic_kind == "sport_request" else topics.arm_request

    task = get_registry().create(skill_name)

    try:
        log.info(
            "g1_request.dispatch",
            task_id=task.task_id,
            skill_name=skill_name,
            sim_mode=SIM_MODE,
            topic=topic,
            api_id=request.api_id,
            data=request.data,
        )

        if ctx is not None:
            try:
                await ctx.report_progress(
                    progress=0.5,
                    total=1.0,
                    message=f"dispatching {skill_name}",
                )
            except Exception:
                pass  # progress is best-effort

        if SIM_MODE == "stub":
            task.status = "completed"
            task.phase = "stub"
            task.progress = 1.0
            task.result = {
                "topic_kind": request.topic_kind,
                "api_id": request.api_id,
                "param": request.param_json(),
                "note": "Stub mode — no dispatch.",
            }
            task.ended_at = time.time()
            return task.to_dict()

        if topic is None:
            # Isaac Sim: no sport/arm request topics. Honest no-op.
            task.status = "completed"
            task.phase = "logged_only"
            task.progress = 1.0
            task.result = {
                "topic_kind": request.topic_kind,
                "api_id": request.api_id,
                "param": request.param_json(),
                "note": (
                    f"SIM_MODE={SIM_MODE} doesn't subscribe to "
                    f"{request.topic_kind} — request logged but not dispatched."
                ),
            }
            task.ended_at = time.time()
            return task.to_dict()

        # SIM_MODE=real and topic resolved → dispatch via direct DDS RPC
        # (unitree_sdk2py.rpc.client.Client — same base Go2's SportClient
        # uses; no WebRTC involved, see bridge.sdk.g1_rpc). _Call blocks
        # synchronously for up to the client's timeout (1s default) waiting
        # on the DDS response, so run it off the event loop — otherwise one
        # in-flight request (e.g. a slow ack) would stall every other tool
        # call, including stop_everything.
        import asyncio

        from bridge.sdk import g1_rpc

        # Dispatch on the api_id the catalogue names, not on a per-service
        # constant: the sport service carries 7101 (posture), 7102 (balance
        # mode) and 7105 (velocity), so assuming 7101 here would turn
        # `balance_stand` into a posture change with the balance value as its
        # mode index.
        if request.topic_kind == "sport_request":
            code, data = await asyncio.to_thread(
                g1_rpc.call_sport_api, request.api_id, request.data
            )
        else:
            code, data = await asyncio.to_thread(g1_rpc.call_arm, request.data)

        task.status = "completed" if code == 0 else "failed"
        task.phase = "dispatched" if code == 0 else "rpc_error"
        task.progress = 1.0
        task.result = {
            "topic_kind": request.topic_kind,
            "api_id": request.api_id,
            "param": request.param_json(),
            "rpc_code": code,
            "rpc_data": data,
        }

        # An ack is not a transition, and on this firmware the difference is
        # invisible. `SetFsmId` answers rpc_code 0 and does NOTHING in at least
        # two situations we hit on 2026-08-20: asking for FSM 4 while in 501
        # (the transition is not permitted from a walk program), and asking for
        # anything at all while no motion controller is loaded. Both reported
        # "completed", which sent us looking at cables and DDS config.
        #
        # So when the request names a target FSM, check whether the robot got
        # there and say so. `status` stays "completed" — the RPC genuinely
        # succeeded, and callers that only look at status keep working — but
        # `phase` and `transitioned` carry the truth for anyone who reads them.
        if code == 0 and request.api_id == g1_protocol.API_ID_G1_STATE:
            reached = await _await_fsm(int(request.data), FSM_SETTLE_TIMEOUT_S)
            task.result["fsm_target"] = int(request.data)
            task.result["fsm_after"] = reached
            # None means we could not read the FSM at all, which is NOT the
            # same as "it did not move" — claiming the latter on missing
            # evidence is exactly the overreach this block exists to prevent.
            task.result["transitioned"] = None if reached is None else reached == int(request.data)
            if reached is not None and reached != int(request.data):
                task.phase = "acked_no_transition"
                task.result["note"] = (
                    f"the sport service acked (code 0) but the robot is in FSM {reached}, "
                    f"not {request.data}. This firmware acks impossible transitions: "
                    "check that a motion controller is loaded "
                    "(scripts/select_motion_mode.py --check-only), and note that some "
                    "transitions are refused from a walk program — damp first."
                )
                log.warning(
                    "g1_request.acked_no_transition",
                    skill_name=skill_name,
                    requested=int(request.data),
                    actual=reached,
                )
        if code != 0:
            task.error = f"rpc_error_code_{code}"
        task.ended_at = time.time()
        return task.to_dict()

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("g1_request.failed", task_id=task.task_id, skill_name=skill_name)
        return task.to_dict()
