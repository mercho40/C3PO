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
- For `SIM_MODE=real`, we'd publish a `unitree_api::msg::dds_::Request_`
  on the resolved topic. That path waits for the Transport layer (Phase
  16 in spec); we raise `NotImplementedError` so we don't silently no-op
  against real hardware.
- For `SIM_MODE=stub`, we return a clean stub result.

When the Transport layer lands, this file is the only place to change.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

import structlog

from bridge.sdk import g1_protocol
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

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

        # SIM_MODE=real and topic resolved → dispatch through the Transport.
        # That code lands with the WebRTC transport (spec §16); raise loud
        # so we don't silently no-op against real hardware.
        raise NotImplementedError(
            "Real-G1 high-level request dispatch requires the WebRTC Transport "
            "(spec §16). Not yet implemented."
        )

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("g1_request.failed", task_id=task.task_id, skill_name=skill_name)
        return task.to_dict()


# Lookup helpers exposed for the catalogue / introspection.

PostureSkillName = Literal["damp", "zero_torque", "prepare", "sit_g1", "lie_up", "squat"]
GestureSkillName = Literal["wave", "point_at", "shake_hand", "hug", "clap", "release_arm"]


def skill_works_in(skill_name: g1_protocol.SkillName, sim_mode: str) -> bool:
    """True if this skill can actually produce motion in the given mode."""
    if sim_mode == "stub":
        return False
    request = g1_protocol.SKILL_REQUESTS[skill_name]
    topics = g1_protocol.topics_for(sim_mode)
    target = topics.sport_request if request.topic_kind == "sport_request" else topics.arm_request
    return target is not None
