"""Read-only operator telemetry over plain HTTP: costmap, gate, surroundings."""

from __future__ import annotations

import structlog

from bridge.server import mcp
from bridge.tools.surroundings import surroundings_snapshot

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Telemetry over plain HTTP — NOT MCP
# ---------------------------------------------------------------------------
#
# WHY THIS IS NOT AN MCP TOOL. Two independent reasons, either sufficient.
#
# 1. It is not for the model. A 240x240 PNG is meaningless to an LLM and would
#    be a catastrophe for the token budget — D7 sizes the model's view of the
#    world at a few hundred tokens on purpose. This is for the OPERATOR's eyes,
#    and the operator's console is a browser.
#
# 2. It is the one path that works today. `apps/back` cannot reach this server
#    over MCP at all: the SDK's StreamableHTTPClientTransport dies under Bun
#    with "socket connection was closed unexpectedly", while the identical code
#    under Node connects fine (docs/OPERATIONS.md). That break is specific to
#    the SDK transport holding a long-lived stream — a plain `fetch()` in Bun to
#    this same server already works. So a plain GET is not a workaround, it is
#    the correct shape for read-only telemetry AND it happens to be the first
#    robot data that can reach the web while that blocker stands.
#
# READ-ONLY, AND STRUCTURALLY SO. This route reads a cached payload the link
# already received. It cannot arm the gate, cannot publish, and cannot reach
# anything that actuates — the whole cmd_vel path is untouched by it. The bridge
# has no auth of its own and the onboard unit exposes it directly on the school
# LAN, alongside the MCP and camera routes.


@mcp.custom_route("/telemetry/costmap.png", methods=["GET"])
async def costmap_png(request):  # noqa: ANN001, ANN201 - starlette types
    """Nav2's global costmap as an indexed PNG, or 503 when there is none.

    503 rather than a blank image, deliberately: a blank map and "no map yet"
    are different facts, and an operator who cannot tell them apart is the map
    equivalent of `objects: []` from an offline detector. The metadata a
    renderer needs to place the image — resolution, origin, size, age — rides on
    headers so the body stays a plain PNG the browser can put in an <img>.
    """
    from starlette.responses import JSONResponse, Response

    from bridge.sdk.perception_link import COSTMAP_STALE_AFTER_S, get_link

    payload, age = get_link().latest_costmap()
    if payload is None:
        return JSONResponse(
            {
                "error": "no costmap received",
                "hint": (
                    "the costmap comes from Nav2's global_costmap, so it exists only "
                    "while a nav2 stage is up: `perception_up nav2-fake` (no sensors) "
                    "or `perception_up nav2` (claims both sensors)"
                ),
            },
            status_code=503,
        )

    import base64

    try:
        png = base64.b64decode(payload["png_base64"])
    except Exception:
        return JSONResponse({"error": "costmap payload is not decodable"}, status_code=502)

    stale = age is not None and age > COSTMAP_STALE_AFTER_S
    return Response(
        content=png,
        media_type="image/png",
        headers={
            # Placement, so the console can put the map under the robot marker.
            # origin is the pose of the BOTTOM-LEFT cell in `frame`; the PNG is
            # top-down, so draw from (origin_x, origin_y + height*res) downward.
            "X-C3PO-Frame": str(payload.get("frame_id", "")),
            "X-C3PO-Width": str(payload.get("width", "")),
            "X-C3PO-Height": str(payload.get("height", "")),
            "X-C3PO-Resolution-M": str(payload.get("resolution_m", "")),
            "X-C3PO-Origin-X-M": str(payload.get("origin_x_m", "")),
            "X-C3PO-Origin-Y-M": str(payload.get("origin_y_m", "")),
            # Age travels with the image so a stale map can be SHOWN AS stale
            # rather than shown as current. Never cache: at 1 Hz a cached map is
            # a lie within a second.
            "X-C3PO-Age-S": "" if age is None else f"{age:.2f}",
            "X-C3PO-Stale": "true" if stale else "false",
            "Cache-Control": "no-store",
        },
    )


@mcp.custom_route("/telemetry/gate", methods=["GET"])
async def gate_status(request):  # noqa: ANN001, ANN201 - starlette types
    """The cmd_vel gate's own account of itself: what it got, what it refused.

    perception_link.status() has always described itself as "what Stage 4 reads
    while Nav2 plans against a shut gate", but nothing exposed it — no tool, no
    route — so the one property Stage 4 exists to prove was the one property
    that could not be observed from outside the process.

    The interesting reading is the CONJUNCTION, and it is worth stating because
    either half alone is misleading:

        cmd_vel_received      climbing   - Nav2 is planning and publishing
        dropped_while_disabled climbing  - every one of them was refused
        last_sent             null       - nothing ever reached the robot

    `dropped_while_disabled` rising on its own could just mean nothing is
    listening. `last_sent: null` on its own could just mean nothing was ever
    sent. Together they say the gate is doing its job under live load, which is
    a different and much stronger claim than "the robot did not move".

    READ-ONLY, AND STRUCTURALLY SO — same as the costmap route above. It reads
    counters. There is no arm/disarm here and there must never be: arming is a
    deliberate, audited, expiring action (`arm_navigation`), not a GET.
    """
    from starlette.responses import JSONResponse

    from bridge.sdk.perception_link import get_link

    # diagnostics() already embeds status() as its "gate" key, so it is the only
    # call made here — asking for both would evaluate the gate twice and could
    # report two different instants under one timestamp.
    diag = get_link().diagnostics()
    body = dict(diag.get("gate") or {})
    # Whether anything is publishing at all — otherwise a quiet gate and a quiet
    # DOMAIN look identical, and someone spends an afternoon debugging a gate
    # that never had a single message to refuse.
    body["link"] = {
        "started": diag.get("started"),
        "domain_id": diag.get("domain_id"),
        "reports_received": diag.get("reports_received"),
    }
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@mcp.custom_route("/telemetry/surroundings", methods=["GET"])
async def surroundings_json(request):  # noqa: ANN001, ANN201 - starlette types
    """The D7 snapshot the agent sees, for the operator console.

    Deliberately the SAME `surroundings_snapshot()` the `describe_surroundings`
    tool returns, not a parallel implementation. If the console built its own
    view of the scene, an operator and the agent could be looking at subtly
    different worlds while debugging the same incident — which is the exact
    failure D7's "one contract, interpreted once" exists to prevent.

    Read-only and structurally so, like the costmap and gate routes: it composes
    a snapshot from data already received and can actuate nothing.
    """
    from starlette.responses import JSONResponse

    try:
        return JSONResponse(surroundings_snapshot(), headers={"Cache-Control": "no-store"})
    except Exception:
        log.exception("telemetry.surroundings_failed")
        # 503 rather than a partial scene: an operator must never be shown an
        # empty world that is actually a broken endpoint.
        return JSONResponse({"error": "could not build a snapshot"}, status_code=503)
