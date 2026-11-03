"""The Nav2 velocity gate: `arm_navigation` / `disarm_navigation` (docs/DECISIONS.md D4)."""

from __future__ import annotations

from typing import Annotated

import structlog
from pydantic import Field

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tools: arm_navigation / disarm_navigation
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["perception_link_down", "no_planner_traffic"],
    )
)
def arm_navigation(
    reason: Annotated[
        str,
        Field(
            min_length=3,
            max_length=200,
            description=(
                "Why you are arming, in one line. This is written to the log "
                "that gets read after something goes wrong, so say what the "
                "robot is about to do and who asked for it."
            ),
        ),
    ],
    seconds: Annotated[
        float,
        Field(
            ge=1.0,
            le=120.0,
            description=(
                "How long the gate stays open. It closes itself after this "
                "whatever happens — keep it to the length of the manoeuvre you "
                "are actually supervising, not the length of your plan."
            ),
        ),
    ] = 30.0,
) -> dict:
    """Let Nav2 drive the robot. THE MOST DANGEROUS TOOL HERE — read this.

    Nav2 plans continuously and publishes velocities at 20 Hz whether or not
    anything is listening. Everything it produces is refused by a default-closed
    gate in this process; this tool opens that gate. Nothing else in the system
    does, and until it is called the robot cannot be driven by the planner at
    all, no matter what the planner decides.

    So this is the single point where a planning stack becomes a moving 1.3 m
    humanoid. Treat it as such:

    - **A person must be watching.** Not "nearby" — watching, with the remote in
      hand and a thumb on the e-stop. Nav2 obeys a costmap, and a costmap can be
      wrong in ways that look fine on a screen.
    - **Arm for the manoeuvre, not the plan.** The gate closes itself after
      `seconds`. That timeout is the safety property; renewing it is cheap and
      forgetting to close it is exactly the failure the timeout exists for.
    - **`stop_everything` closes it**, synchronously and before anything else it
      does. Without that, stopping would only pause: the burst halts the gait
      and Nav2's next tick would walk the robot on 50 ms later.

    Arming does not command motion by itself. If no planner is publishing, the
    gate opens onto silence and closes again — which is exactly what the returned
    `cmd_vel_age_s` tells you: `null` means nothing is planning, so arming was
    pointless rather than dangerous.
    """
    log.warning("arm_navigation.called", seconds=seconds, reason=reason, sim_mode=SIM_MODE)

    from bridge.sdk.perception_link import get_link

    link = get_link()
    status_before = link.status()
    if not status_before.get("cmd_vel_received"):
        # Not refused — armed anyway, and told. A planner that has not published
        # yet is normal during bring-up, and refusing here would make arming
        # depend on timing the operator cannot see.
        log.warning("arm_navigation.no_planner_traffic")

    link.enable(reason=reason, ttl_s=seconds)
    status = link.status()
    return {
        "status": "armed",
        "seconds": seconds,
        "reason": reason,
        "expires_in_s": status.get("arm_expires_in_s"),
        # None here means no planner is publishing: the gate is open onto
        # nothing, so nothing will move until Nav2 starts.
        "cmd_vel_age_s": status.get("cmd_vel_age_s"),
        "clamps": status.get("clamps"),
        "warning": (
            "The robot can now be driven by Nav2. A person must be watching "
            "with the e-stop in reach. The gate closes itself in "
            f"{seconds:g}s, or immediately on stop_everything."
        ),
        "env": SIM_MODE,
    }


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=True,
        works_real=True,
    )
)
def disarm_navigation(
    reason: Annotated[
        str, Field(max_length=200, description="Why, for the log.")
    ] = "operator disarmed",
) -> dict:
    """Close the gate. Nav2 keeps planning; nothing it plans reaches the legs.

    Always safe to call, including when already closed. Prefer this over waiting
    for the arm timeout once a manoeuvre is finished — the timeout is a backstop
    for the case where somebody forgot, not the normal way to end.

    This is NOT a stop: it prevents further motion rather than arresting motion
    already underway. `stop_everything` is what halts the robot, and it closes
    this gate as its first act.
    """
    log.warning("disarm_navigation.called", reason=reason, sim_mode=SIM_MODE)

    from bridge.sdk.perception_link import get_link

    link = get_link()
    was_open = link.is_enabled()
    link.disable(reason=reason)
    return {
        "status": "disarmed",
        "was_armed": was_open,
        "reason": reason,
        "note": (
            "Nav2 is still planning and still publishing; the gate now refuses "
            "all of it. This prevents motion — it does not arrest motion already "
            "in progress. Use stop_everything for that."
        ),
        "env": SIM_MODE,
    }
