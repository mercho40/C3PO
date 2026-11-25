"""The governor's status, and the two things only an operator may do: confirm one gated
call (/safety/confirm) and clear a standing PARAR (/safety/ack). Never MCP tools.
"""

from __future__ import annotations

import structlog
from mcp.server.fastmcp.tools import Tool
from pydantic import ValidationError

from bridge import face_greet, safety, server
from bridge.routes.common import no_store
from bridge.server import mcp

log = structlog.get_logger(__name__)


@mcp.custom_route("/safety/status", methods=["GET"])
async def safety_status(request):  # noqa: ANN001, ANN201 - starlette types
    """The governor's caps, flags, last command and the nearest person. Read-only."""
    body = {**safety.status(), "face_greet": face_greet.status()}
    return no_store(body)


def unproven(request):  # noqa: ANN001, ANN202 - starlette types
    """403 (503 with no secret set) unless the request carries apps/back's
    SAFETY_CONFIRM_SECRET in `X-C3PO-Confirm-Secret`; None when it does."""
    from starlette.responses import JSONResponse

    if safety.confirm_secret_ok(request.headers.get("x-c3po-confirm-secret")):
        return None
    configured = bool(safety.CONFIRM_SECRET)
    return JSONResponse(
        {"error": "forbidden" if configured else "confirmation_not_configured"},
        status_code=403 if configured else 503,
    )


def confirmable(name: str) -> Tool | None:
    """The tool called `name` if a call of it takes an operator's confirmation
    (safety.needs_confirmation), else None: unknown, or not gated."""
    listed = mcp.registered_tool(name)
    return listed if listed is not None and safety.needs_confirmation(listed.meta) else None


@mcp.custom_route("/safety/confirm", methods=["POST"])
async def safety_confirm(request):  # noqa: ANN001, ANN201 - starlette types
    """The operator's confirmation for ONE call of a gated tool with ONE argument set. Not an MCP tool.

    Gated = safety.needs_confirmation: T2, and high-danger T1 (arm_navigation,
    start_walking_waist, zero_torque). apps/back's /invoke grants those before dispatching,
    so the console and headset bring-up are confirmed by the admin's own click.

    Body `{"tool": name, "args": {...}}`; args default to {} (the tool's defaults).
    The grant is bound to the canonical form of those args (`server.canonical_args`),
    so a call with any other arguments is still refused.

    That is the whole point: a model only ever sees the tool list, and nothing
    on it can reach this. apps/back's admin-only `POST /skills/:name/confirm`
    forwards here with SAFETY_CONFIRM_SECRET in `X-C3PO-Confirm-Secret`; the
    secret also keeps out an agent that has a shell on a machine with the
    tunnel (Claude Code), as long as it cannot read the robot's .env, nor
    apps/back/.env on the Mac, which holds the same secret (a known limit).
    With no secret configured nothing can be confirmed, so gated tools stay refused.
    """
    from starlette.responses import JSONResponse

    if (refused := unproven(request)) is not None:
        return refused
    try:
        body = await request.json()
        tool, args = str(body.get("tool") or ""), body.get("args", {})
        # A label for the log only: back sends "face" after /face/confirm_check passed.
        via = "face" if body.get("via") == "face" else "console"
    except Exception:
        tool, args, via = "", {}, "console"
    listed = confirmable(tool)
    if listed is None:
        # Only gated tools take a grant; anything else is a typo or a probe.
        return JSONResponse({"error": "not_confirmable", "tool": tool}, status_code=400)
    try:
        args_key = server.canonical_args(listed, args) if isinstance(args, dict) else None
    except ValidationError:
        args_key = None
    if args_key is None:
        return JSONResponse({"error": "invalid_arguments", "tool": tool}, status_code=400)
    safety.grant_confirmation(tool, args_key, via=via)
    return no_store({"granted": True, "tool": tool, "expires_in_s": safety.CONFIRM_TTL_S})


@mcp.custom_route("/safety/ack", methods=["POST"])
async def safety_ack(request):  # noqa: ANN001, ANN201 - starlette types
    """The operator clears a standing PARAR («Reanudar»). Not an MCP tool.

    Same guard as /safety/confirm and for the same reason: a model must never
    be able to un-stop the robot it was stopped from driving. apps/back's
    admin-only `POST /safety/ack` forwards here with the secret (the console chip and
    the headset menu's «Reanudar»); with no secret configured nothing clears it.
    Body `{"stopped_at": <the stamp the operator saw>}` refuses (409) a newer stop.
    """
    from starlette.responses import JSONResponse

    from bridge import estop

    if (refused := unproven(request)) is not None:
        return refused
    try:
        shown = (await request.json()).get("stopped_at")
    except Exception:
        shown = None
    if isinstance(shown, (int, float)) and estop.last_stop_at() > float(shown) + 1e-3:
        # A newer PARAR landed after the operator read the latch: they have not seen it.
        return JSONResponse({"error": "newer_stop", **safety.stop_latch()}, status_code=409)
    was = safety.stop_latch()
    recorded = estop.record_ack()
    safety.clear_confirmations("ack")
    if not recorded and (latch := safety.stop_latch())["stop_standing"]:
        # The ack file did not land, so the latch still stands: never tell the console "resumed".
        log.error("safety.stop_ack_not_recorded", via="console")
        return JSONResponse({"error": "ack_not_recorded", **latch}, status_code=500)
    log.warning("safety.stop_acknowledged", via="console", was_standing=was["stop_standing"])
    return no_store(
        {"acknowledged": True, "was_standing": was["stop_standing"], **safety.stop_latch()}
    )
