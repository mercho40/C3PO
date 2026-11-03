"""Voice telemetry and PCM transport routes consumed by `apps/back`."""

from __future__ import annotations

import asyncio
import time

import structlog

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.tools.safety import stop_everything

log = structlog.get_logger(__name__)


_VOICE_KEEPALIVE_S = 15.0


_VOICE_DISCONNECT_POLL_S = 0.5


async def _voice_event_body(request, listener):  # noqa: ANN001, ANN201 - starlette types
    """Yield non-consuming voice events until the HTTP client disconnects."""
    import json

    sequence = listener.sequence()
    keepalive_at = time.monotonic() + _VOICE_KEEPALIVE_S
    try:
        while True:
            if await request.is_disconnected():
                return

            wait_s = min(
                _VOICE_DISCONNECT_POLL_S,
                max(0.0, keepalive_at - time.monotonic()),
            )
            item = await asyncio.to_thread(listener.wait_for_next, sequence, wait_s)

            # Starlette cancels the generator on most disconnects, but checking
            # explicitly also handles ASGI servers that only expose the state via
            # receive(), and avoids writing one last event to a closed socket.
            if await request.is_disconnected():
                return
            if item is not None:
                sequence = item["seq"]
                yield f"data: {json.dumps(item, separators=(',', ':'))}\n\n".encode()
                keepalive_at = time.monotonic() + _VOICE_KEEPALIVE_S
            elif time.monotonic() >= keepalive_at:
                yield b": keepalive\n\n"
                keepalive_at = time.monotonic() + _VOICE_KEEPALIVE_S
    except asyncio.CancelledError:
        # Client disconnects normally cancel StreamingResponse's producer task.
        # There is no upstream resource to close; ending the generator is enough.
        return


@mcp.custom_route("/telemetry/voice/events", methods=["GET"])
async def voice_events(request):  # noqa: ANN001, ANN201 - starlette types
    """Live speech/stop items as SSE without consuming the agent's poll queue."""
    from starlette.responses import StreamingResponse

    from bridge.skills.listen import get_mic_listener

    return StreamingResponse(
        _voice_event_body(request, get_mic_listener()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _voice_pcm_body(request, listener):  # noqa: ANN001, ANN201 - starlette types
    """Stream the listener's live 16 kHz PCM without blocking the MCP event loop."""
    import threading

    stop = threading.Event()
    frames = listener.audio_frames(stop)
    waiting_for_frame = False
    try:
        while not await request.is_disconnected():
            try:
                waiting_for_frame = True
                frame = await asyncio.to_thread(next, frames)
                waiting_for_frame = False
            except StopIteration:
                return
            if await request.is_disconnected():
                return
            yield frame
    except asyncio.CancelledError:
        return
    finally:
        stop.set()
        # `to_thread` cancellation does not stop its worker. Closing a generator
        # while that worker is inside `next()` raises "generator already executing";
        # the stop event lets it unwind itself on the next 500 ms queue timeout.
        if not waiting_for_frame:
            frames.close()
        # Disarm session-scoped auto-stop while leaving the always-on detector
        # and transcript listener running.
        listener.set_on_stop(None)


@mcp.custom_route("/telemetry/voice/audio/input", methods=["GET"])
async def voice_audio_input(request):  # noqa: ANN001, ANN201 - starlette types
    """Live 16 kHz mono PCM for the trusted backend's OpenAI Realtime socket."""
    from starlette.responses import StreamingResponse

    from bridge.skills.listen import get_mic_listener

    listener = get_mic_listener()
    event_loop = asyncio.get_running_loop()

    def stop_locally(_phrase: str) -> None:
        # The detector runs in its own thread. Schedule onto the bridge's event
        # loop; no network or model round-trip sits between the phrase and stop.
        asyncio.run_coroutine_threadsafe(stop_everything(), event_loop)

    listener.start(on_stop=stop_locally)
    return StreamingResponse(
        _voice_pcm_body(request, listener),
        media_type="audio/pcm;rate=16000;channels=1",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@mcp.custom_route("/telemetry/voice/audio/output", methods=["POST", "DELETE"])
async def voice_audio_output(request):  # noqa: ANN001, ANN201 - starlette types
    """Play one 16 kHz PCM chunk, or stop C3PO-owned playback on DELETE."""
    from starlette.responses import JSONResponse

    if SIM_MODE != "real":
        return JSONResponse({"status": "ok", "stub": True, "env": SIM_MODE})

    from bridge.sdk import g1_rpc

    if request.method == "DELETE":
        code, data = await asyncio.to_thread(g1_rpc.stop_play)
        return JSONResponse(
            {"status": "ok" if code == 0 else "failed", "rpc_code": code, "rpc_data": data}
        )

    stream_id = request.query_params.get("stream_id", "").strip()
    if not stream_id or len(stream_id) > 100:
        return JSONResponse({"error": "invalid_stream_id"}, status_code=400)
    pcm = await request.body()
    if not pcm or len(pcm) > 512_000 or len(pcm) % 2:
        return JSONResponse({"error": "invalid_pcm"}, status_code=400)

    code, data = await asyncio.to_thread(g1_rpc.play_pcm, pcm, stream_id)
    return JSONResponse(
        {
            "status": "ok" if code == 0 else "failed",
            "rpc_code": code,
            "rpc_data": data,
            "bytes": len(pcm),
        }
    )


@mcp.custom_route("/telemetry/voice", methods=["GET"])
async def voice_json(request):  # noqa: ANN001, ANN201 - starlette types
    """What the robot has heard recently, and whether it can hear at all.

    Uses `recent()` rather than `poll()` — READING THIS MUST NOT CONSUME. The
    agent's `listen` tool drains the buffer; if the console drained it too, an
    operator leaving the page open would silently eat the utterances the agent
    was supposed to act on. The two readers must not compete.
    """
    from starlette.responses import JSONResponse

    try:
        from bridge.skills import listen as listen_skill

        listener = listen_skill.get_mic_listener()
        diag = listener.diagnostics()
        source = listen_skill.describe_audio_source()
        items = listener.recent(120.0) if diag["running"] else []
        return JSONResponse(
            {
                "running": diag["running"],
                "error": diag["error"],
                # The distinction an operator needs to read silence correctly:
                # with a push-to-talk mic, nothing heard usually means nobody
                # held the button — not a quiet room.
                "always_listening": source["always_on"],
                "audio_source": source["source"],
                "mic_ever_open": diag["mic_ever_open"],
                "seconds_since_audio": diag["seconds_since_audio"],
                "utterances": diag["utterances"],
                "recent": [
                    {"text": i["text"], "kind": i["kind"], "age_s": i["age_s"]} for i in items
                ],
            },
            headers={"Cache-Control": "no-store"},
        )
    except Exception:
        log.exception("telemetry.voice_failed")
        return JSONResponse({"error": "voice telemetry unavailable"}, status_code=503)
