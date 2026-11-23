"""The head camera over the bridge's own HTTP surface: status, one frame, MJPEG."""

from __future__ import annotations

import asyncio
import time

import structlog

from bridge import config
from bridge.routes.common import origin_of
from bridge.server import mcp
from bridge.tools.vision import grab_frame

log = structlog.get_logger(__name__)


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
# 8001 costs nothing extra: apps/back already reaches the bridge there (over
# the SSH tunnel — the bridge binds loopback), and relays /camera/* to the
# browser behind its login (apps/back/src/routes/camera.ts). The console picks
# a feed with one variable, because `endpoint()` in
# `apps/web/src/lib/robot/mjpeg-camera.ts` is a plain base + path join:
#
#     PUBLIC_ROBOT_CAM_URL=http://localhost:3000/camera  <- apps/back's relay
#     PUBLIC_ROBOT_CAM_URL=http://127.0.0.1:8081         <- detector only, debug,
#                                                           over its own tunnel
#
# The paths and the `/status` field names are identical to the vision
# container's on purpose: one client in the console, two possible servers, no
# branch anywhere in the browser.
#
# READ-ONLY, AND STRUCTURALLY SO — like the costmap, gate and surroundings
# routes (routes/telemetry.py). It reads a buffer a polling thread filled from a vendor RPC
# whose only verb is "give me a picture". Nothing here can arm the gate,
# publish, or reach anything that actuates.

# How often the streaming generator looks for a new frame. Well under the
# camera's own period so the poll adds no meaningful latency, and it is a sleep
# rather than a condition wait because blocking a thread inside an async
# generator would stall every other route on the event loop.
_CAMERA_WAKE_S = 0.02


def _camera_unavailable(origin: str | None = None):  # noqa: ANN202 - starlette types
    """The 503 body for "there is no camera here", or None if there is one."""
    from starlette.responses import JSONResponse

    from bridge.sdk.camera_relay import camera_headers

    if config.SIM_MODE != "real":
        return JSONResponse(
            {
                "error": "the head camera is real-hardware only",
                "sim_mode": config.SIM_MODE,
                "hint": (
                    "this feed reads the G1's videohub RPC; the sim's cameras are "
                    "teleimager WebRTC servers on 60001-60003 (apps/web/README.md)"
                ),
            },
            status_code=503,
            # The 503 needs the CORS header too. Without it the browser refuses
            # to let the console READ this body — so the page could not show the
            # reason, which is the only thing this response exists to carry.
            headers=camera_headers(origin),
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

    origin = origin_of(request)
    unavailable = _camera_unavailable(origin)
    if unavailable is not None:
        return unavailable

    from bridge.sdk import camera_relay
    from bridge.sdk.videohub import get_camera

    videohub = get_camera().status()
    # The probe can block 1.5 s, and this loop also serves stop_everything.
    vision = await asyncio.to_thread(camera_relay.upstream_status)
    source = camera_relay.choose_source(videohub, vision)
    return JSONResponse(
        camera_relay.merged_status(videohub, vision, source),
        headers=camera_relay.camera_headers(origin),
    )


@mcp.custom_route("/camera/frame.jpg", methods=["GET"])
async def camera_frame(request):  # noqa: ANN001, ANN201 - starlette types
    """The newest frame as one JPEG, for a poll or a screenshot.

    503 rather than a placeholder when nothing has arrived: "no picture yet" and
    "a picture of an empty room" are different facts, and the whole reason this
    module exists is that an operator could not tell them apart.
    """
    from starlette.responses import JSONResponse, Response

    origin = origin_of(request)
    unavailable = _camera_unavailable(origin)
    if unavailable is not None:
        return unavailable

    from bridge.sdk import camera_relay
    from bridge.sdk.videohub import get_camera

    camera = get_camera()
    videohub = camera.status()
    # The probe can block 1.5 s, and this loop also serves stop_everything.
    vision = await asyncio.to_thread(camera_relay.upstream_status)
    source = camera_relay.choose_source(videohub, vision)

    if source == "videohub":
        _seq, jpeg, _stamp = camera.snapshot()
        if jpeg is not None:
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={
                    **camera_relay.camera_headers(origin),
                    "Content-Length": str(len(jpeg)),
                },
            )

    if source == "vision":
        try:
            body = await asyncio.to_thread(_relay_bytes, camera_relay.vision_url("frame.jpg"))
        except Exception:
            log.exception("camera.relay_frame_failed")
        else:
            return Response(content=body, media_type="image/jpeg",
                            headers=camera_relay.camera_headers(origin))

    status = camera_relay.merged_status(videohub, vision, source)
    return JSONResponse(
        {"error": "no frame yet", "hint": status.get("hint"), "status": status},
        status_code=503,
        headers=camera_relay.camera_headers(origin),
    )


async def _relay_stream(resp, request):  # noqa: ANN001 - urllib response, starlette Request
    """Pipe an already-open upstream MJPEG response straight through to the client.

    Opened by the route, not here: a failed open must be a 503 the client backs off
    from, and inside the generator the 200 is already sent, so it was an empty 200
    the console reconnected to at once (~20/min, 2026-11-12).

    Reads in a thread: `urllib` is blocking, and blocking the event loop here
    would stall every other route — `stop_everything` among them, which is the
    one that must never wait behind a video frame.

    ASKS WHETHER THE VIEWER IS STILL THERE, every chunk. Nothing else ends this
    loop when they leave: the upstream never stops sending, and the generator is
    only finalized when the server cancels it or the GC gets to it. Until then it
    holds one of the vision server's four stream slots — four abandoned viewers
    (a reload, a dropped tunnel) and it refuses everyone with 503, the bridge's
    own next relay included. Seen 2026-10-27: a live camera, a black headset.
    """
    try:
        while True:
            if await request.is_disconnected():
                return
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

    origin = origin_of(request)
    unavailable = _camera_unavailable(origin)
    if unavailable is not None:
        return unavailable

    from bridge.sdk import camera_relay
    from bridge.sdk.videohub import STALE_AFTER_S, get_camera

    camera = get_camera()
    videohub = camera.status()
    # The probe can block 1.5 s, and this loop also serves stop_everything.
    vision = await asyncio.to_thread(camera_relay.upstream_status)
    source = camera_relay.choose_source(videohub, vision)

    if source is None:
        # Refuse before opening the response. A stream that opens and
        # immediately closes is indistinguishable at the client from a network
        # fault; a 503 with a reason is not.
        status = camera_relay.merged_status(videohub, vision, source)
        return JSONResponse(
            {"error": "no frame yet", "hint": status.get("hint"), "status": status},
            status_code=503,
            headers=camera_relay.camera_headers(origin),
        )

    if source == "vision":
        # RELAY, byte for byte. The vision container already emits the same
        # multipart format with the same boundary, so re-framing it here would
        # be re-encoding a stream we have no reason to touch — and would put a
        # JPEG decode per frame on the bridge, which owns stop_everything.
        import urllib.request

        url = camera_relay.vision_url("stream.mjpg")
        try:
            resp = await asyncio.to_thread(urllib.request.urlopen, url, None, 10.0)
        except Exception as exc:
            # e.g. the vision server's "too many stream clients (max 4)".
            log.warning("camera.relay_open_failed", url=url, error=str(exc))
            return JSONResponse(
                {"error": "camera_unavailable", "detail": str(exc)},
                status_code=503,
                headers={**camera_relay.camera_headers(origin), "Retry-After": "2"},
            )
        media_type, headers = camera_relay.relay_headers("stream", origin)
        return StreamingResponse(
            _relay_stream(resp, request),
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
        headers=camera_relay.camera_headers(origin),
    )


@mcp.custom_route("/camera/cloud_frame.jpg", methods=["GET"])
async def camera_cloud_frame(request):  # noqa: ANN001, ANN201 - starlette types
    """The newest frame with faces blurred, for a caller that uploads it to a cloud model.

    apps/back's `look` and `verify` read this, never /camera/frame.jpg (the
    operator's own view stays sharp). Blurred here because OpenCV is here.
    Fails closed like `vlm.blur_faces`: 503 with the reason, never a raw frame.
    """
    from starlette.responses import JSONResponse, Response

    from bridge import vlm

    jpeg, _source, err = await grab_frame()
    if err or jpeg is None:
        return JSONResponse(err or {"error": "no_frame"}, status_code=503)
    try:
        jpeg = await asyncio.to_thread(vlm.blur_faces, jpeg)
    except vlm.VlmError as exc:
        return JSONResponse({"error": "face_blur_failed", "detail": str(exc)}, status_code=503)
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
