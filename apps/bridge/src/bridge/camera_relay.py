"""Real-hardware camera relay: teleimager's ZeroMQ JPEG feed -> WebSocket.

The G1's only live camera feed today is `teleimager.image_server`, a
human-launched `xr_teleoperate` process already running onboard the Jetson
(`docs/ROBOT-HARDWARE.md`). It publishes JPEG frames over a ZeroMQ
`PUB` socket at `tcp://0.0.0.0:55555` — monocular, 540x960 @ 30fps, one JPEG
blob per message, no envelope. That's a different transport than the sim's
per-camera aiortc/WebRTC servers (`apps/web`'s `live-camera` page), which is
why real hardware needs its own viewer instead of reusing that page.

This module does the minimum needed to get those frames to a browser:
subscribe as a ZMQ `SUB` client (passive — it never opens `/dev/video4`
itself, so it can't contend for the camera device the way a second
`videohub_pc4`/`realsense2_camera_node`/teleimager instance would, see
`docs/ROBOT-HARDWARE.md`/§3.1's "only one can win" warning) and re-publish
each frame as a binary WebSocket message to every connected client.

Deliberately NOT the teleimager config REQ/REP socket (`:60000`) — this is a
read-only mirror, not a camera controller; whatever camera teleimager is
already configured for is what gets relayed.

Run as its own process, separate from the MCP server:
    uv run python -m bridge.camera_relay

Binds loopback by default, same "no auth of its own, reach it over an SSH
tunnel" posture as the MCP HTTP transport (`apps/bridge` README, SPEC §10.1)
— this has even less authentication than that, being a raw frame firehose.

NOT YET LIVE-TESTED against the real teleimager process (the Jetson wasn't
reachable from the dev machine while this was written) — same "verify
before trusting it" posture as `walk_velocity`/`dance`. Verify with
`scripts/peek_camera_relay.py` (or a browser once `apps/web` consumes this)
before relying on it.
"""

from __future__ import annotations

import asyncio
import os
import time

import structlog
import zmq
import zmq.asyncio
from websockets.asyncio.server import ServerConnection, serve

log = structlog.get_logger(__name__)

# teleimager itself -- passive SUB, never touches the camera device.
TELEIMAGER_HOST = os.environ.get("TELEIMAGER_HOST", "127.0.0.1")
TELEIMAGER_PORT = int(os.environ.get("TELEIMAGER_PORT", "55555"))

# This relay's own WebSocket server. Loopback by default -- reach it over an
# SSH tunnel on real hardware, same as BRIDGE_PORT (8001). Picked 8766 to
# avoid every other port already spoken for on the Jetson: 8000
# (gemm-ai.service), 8001 (this bridge's MCP), 8765 (the colleague's
# gemm-bringup foxglove_bridge), 55555/60000 (teleimager itself), 7077
# (planned, unused bridge WS) -- see `docs/ROBOT-HARDWARE.md`
CAMERA_RELAY_HOST = os.environ.get("CAMERA_RELAY_HOST", "127.0.0.1")
CAMERA_RELAY_PORT = int(os.environ.get("CAMERA_RELAY_PORT", "8766"))

# How long without a frame from teleimager before we log a warning -- lets an
# operator tell "no camera" apart from "relay silently died".
STALE_WARNING_S = 5.0

_clients: set[ServerConnection] = set()


async def _handle_client(ws: ServerConnection) -> None:
    _clients.add(ws)
    log.info("camera_relay.client_connected", remote=ws.remote_address, total=len(_clients))
    try:
        # This is a one-way feed; the client sends nothing we act on. Just
        # hold the connection open until it closes.
        await ws.wait_closed()
    finally:
        _clients.discard(ws)
        log.info("camera_relay.client_disconnected", total=len(_clients))


async def _broadcast(frame: bytes) -> None:
    if not _clients:
        return
    # A slow/hung client shouldn't block delivery to everyone else -- give
    # each send a short budget and drop whoever can't keep up.
    async def send_one(ws: ServerConnection) -> None:
        try:
            await asyncio.wait_for(ws.send(frame), timeout=0.5)
        except Exception:
            log.warning("camera_relay.client_send_failed", remote=ws.remote_address)
            _clients.discard(ws)
            # Forgetting the reference is not enough, for two reasons: the
            # socket would stay open until the peer happened to disconnect
            # (an fd leak on a process meant to run for days), and a send
            # cancelled by `wait_for` may have written a partial frame,
            # leaving this connection's WebSocket framing corrupted. Either
            # way the connection is finished -- close it explicitly.
            try:
                await ws.close(code=1011, reason="relay send timeout")
            except Exception:
                pass

    await asyncio.gather(*(send_one(ws) for ws in list(_clients)))


async def _pump_frames() -> None:
    """Subscribe to teleimager and broadcast every frame it publishes."""
    ctx = zmq.asyncio.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.RCVHWM, 2)  # drop backlog rather than buffer stale frames
    addr = f"tcp://{TELEIMAGER_HOST}:{TELEIMAGER_PORT}"
    sock.connect(addr)
    log.info("camera_relay.subscribed", addr=addr)

    last_frame_at = time.time()
    try:
        while True:
            try:
                frame = await asyncio.wait_for(sock.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                if time.time() - last_frame_at > STALE_WARNING_S:
                    log.warning(
                        "camera_relay.no_frames",
                        addr=addr,
                        seconds_since_last=round(time.time() - last_frame_at, 1),
                    )
                continue
            last_frame_at = time.time()
            await _broadcast(frame)
    finally:
        sock.close(linger=0)


async def _main() -> None:
    async with serve(_handle_client, CAMERA_RELAY_HOST, CAMERA_RELAY_PORT):
        log.info(
            "camera_relay.listening",
            host=CAMERA_RELAY_HOST,
            port=CAMERA_RELAY_PORT,
            teleimager=f"{TELEIMAGER_HOST}:{TELEIMAGER_PORT}",
        )
        await _pump_frames()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
