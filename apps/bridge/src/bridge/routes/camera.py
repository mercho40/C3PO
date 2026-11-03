"""The head-camera relay: `/camera/status`, `/camera/frame.jpg`, `/camera/stream.mjpg`."""

from __future__ import annotations

import asyncio
import time

import structlog

from bridge.config import SIM_MODE
from bridge.server import mcp

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# The head camera, over the bridge's own HTTP surface
# ---------------------------------------------------------------------------
#
# WHY THE BRIDGE SERVES THIS AND NOT THE VISION CONTAINER. The container's
# MJPEG server (`c3po_vision.stream`) is fed by the detector, and the detector
# cannot start unless it owns `/dev/video4`. This feed exists for exactly the
# case where it does NOT own it — `videohub_pc4` has the device — so putting it
# in the vision container would put it in the one process guaranteed to be dead
# whenever it is needed. The bridge is already a host process, already on DDS
# domain 0 over eth0, and already serving read-only telemetry here.
#
# WHY NOT PORT 8081. That port belongs to the vision container, and two servers
# racing for one bind is a coin-flip failure with a confusing symptom. Riding
# 8001 costs nothing extra: the bridge is already directly reachable there,
# and unlike `:8081` this needs no second exposed service.
# The console picks a feed with one variable, because `endpoint()` in
# `apps/web/src/lib/robot/mjpeg-camera.ts` is a plain base + path join:
#
#     PUBLIC_ROBOT_CAM_URL=http://g1-orin.local:8001/camera <- relay/picker
#     PUBLIC_ROBOT_CAM_URL=http://g1-orin.local:8081        <- detector only
#
# The paths and the `/status` field names are identical to the vision
# container's on purpose: one client in the console, two possible servers, no
# branch anywhere in the browser.
#
# READ-ONLY, AND STRUCTURALLY SO — like the costmap, gate and surroundings
# routes above. It reads a buffer a polling thread filled from a vendor RPC
# whose only verb is "give me a picture". Nothing here can arm the gate,
# publish, or reach anything that actuates.

# How often the streaming generator looks for a new frame. Well under the
# camera's own period so the poll adds no meaningful latency, and it is a sleep
# rather than a condition wait because blocking a thread inside an async
# generator would stall every other route on the event loop.
_CAMERA_WAKE_S = 0.02


def _camera_unavailable():  # noqa: ANN202 - starlette types
    """The 503 body for "there is no camera here", or None if there is one."""
    from starlette.responses import JSONResponse

    if SIM_MODE != "real":
        return JSONResponse(
            {
                "error": "the head camera is real-hardware only",
                "sim_mode": SIM_MODE,
                "hint": (
                    "this feed reads the G1's videohub RPC; the sim's cameras are "
                    "teleimager WebRTC servers on 60001-60003 (apps/web/README.md)"
                ),
            },
            status_code=503,
        )
    return None


@mcp.custom_route("/camera/status", methods=["GET"])
async def camera_status(request):  # noqa: ANN001, ANN201 - starlette types
    """Whether the picture is live, and if not, why — in one object.

    `live` is the whole point: an `<img>` cannot tell the console the difference
    between a feed and a photograph of one. `frames` is monotonic so a client
    can catch a stall that both ends of an age comparison straddle, and `hint`
    carries the ordinary cause of a dark feed — somebody took the device — so
    nobody goes hunting for broken hardware.
    """
    from starlette.responses import JSONResponse

    unavailable = _camera_unavailable()
    if unavailable is not None:
        return unavailable

    from bridge.sdk import camera_relay
    from bridge.sdk.videohub import get_camera

    videohub = get_camera().status()
    vision = camera_relay.upstream_status()
    source = camera_relay.choose_source(videohub, vision)
    return JSONResponse(
        camera_relay.merged_status(videohub, vision, source),
        headers={"Cache-Control": "no-store"},
    )


@mcp.custom_route("/camera/frame.jpg", methods=["GET"])
async def camera_frame(request):  # noqa: ANN001, ANN201 - starlette types
    """The newest frame as one JPEG, for a poll or a screenshot.

    503 rather than a placeholder when nothing has arrived: "no picture yet" and
    "a picture of an empty room" are different facts, and the whole reason this
    module exists is that an operator could not tell them apart.
    """
    from starlette.responses import JSONResponse, Response

    unavailable = _camera_unavailable()
    if unavailable is not None:
        return unavailable

    from bridge.sdk import camera_relay
    from bridge.sdk.videohub import get_camera

    camera = get_camera()
    videohub = camera.status()
    vision = camera_relay.upstream_status()
    source = camera_relay.choose_source(videohub, vision)

    if source == "videohub":
        _seq, jpeg, _stamp = camera.snapshot()
        if jpeg is not None:
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store", "Content-Length": str(len(jpeg))},
            )

    if source == "vision":
        try:
            body = await asyncio.to_thread(_relay_bytes, camera_relay.vision_url("frame.jpg"))
        except Exception:
            log.exception("camera.relay_frame_failed")
        else:
            return Response(
                content=body, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
            )

    status = camera_relay.merged_status(videohub, vision, source)
    return JSONResponse(
        {"error": "no frame yet", "hint": status.get("hint"), "status": status},
        status_code=503,
    )


async def _relay_stream(url: str):
    """Pipe an upstream MJPEG response straight through to the client.

    Reads in a thread: `urllib` is blocking, and blocking the event loop here
    would stall every other route — `stop_everything` among them, which is the
    one that must never wait behind a video frame.
    """
    import urllib.request

    try:
        resp = await asyncio.to_thread(urllib.request.urlopen, url, None, 10.0)
    except Exception:
        log.exception("camera.relay_open_failed", url=url)
        return
    try:
        while True:
            chunk = await asyncio.to_thread(resp.read, 8192)
            if not chunk:
                return
            yield chunk
    except Exception:
        # The operator closed the tab, or the upstream went away mid-stream.
        # Ending the response IS how MJPEG says "no longer live".
        return
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _relay_bytes(url: str, timeout: float = 5.0) -> bytes:
    """Fetch a whole body. Blocking on purpose — callers use asyncio.to_thread."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return bytes(resp.read())


@mcp.custom_route("/camera/stream.mjpg", methods=["GET"])
async def camera_stream(request):  # noqa: ANN001, ANN201 - starlette types
    """MJPEG — what the console's `<img>` and the VR camera layer render.

    ENDING THE RESPONSE IS THE MESSAGE. multipart/x-mixed-replace has no
    in-band way to say "this is no longer live", so going quiet would leave the
    browser showing its last frame at full brightness forever. Closing once no
    new frame has arrived for `STALE_AFTER_S` is the signal, and it is the same
    contract `c3po_vision.stream` already has with the same client.
    """
    from starlette.responses import JSONResponse, StreamingResponse

    unavailable = _camera_unavailable()
    if unavailable is not None:
        return unavailable

    from bridge.sdk import camera_relay
    from bridge.sdk.videohub import STALE_AFTER_S, get_camera

    camera = get_camera()
    videohub = camera.status()
    vision = camera_relay.upstream_status()
    source = camera_relay.choose_source(videohub, vision)

    if source is None:
        # Refuse before opening the response. A stream that opens and
        # immediately closes is indistinguishable at the client from a network
        # fault; a 503 with a reason is not.
        status = camera_relay.merged_status(videohub, vision, source)
        return JSONResponse(
            {"error": "no frame yet", "hint": status.get("hint"), "status": status},
            status_code=503,
        )

    if source == "vision":
        # RELAY, byte for byte. The vision container already emits the same
        # multipart format with the same boundary, so re-framing it here would
        # be re-encoding a stream we have no reason to touch — and would put a
        # JPEG decode per frame on the bridge, which owns stop_everything.
        media_type, headers = camera_relay.relay_headers("stream")
        return StreamingResponse(
            _relay_stream(camera_relay.vision_url("stream.mjpg")),
            media_type=media_type,
            headers=headers,
        )

    boundary = "c3poframe"

    async def frames():
        last_seq = 0
        last_new = time.monotonic()
        while True:
            current, data, _ = camera.snapshot()
            if current != last_seq and data is not None:
                last_seq = current
                last_new = time.monotonic()
                yield (
                    "--{}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n".format(
                        boundary, len(data)
                    ).encode("ascii")
                    + data
                    + b"\r\n"
                )
            elif time.monotonic() - last_new > STALE_AFTER_S:
                return
            await asyncio.sleep(_CAMERA_WAKE_S)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=" + boundary,
        headers={"Cache-Control": "no-store"},
    )
