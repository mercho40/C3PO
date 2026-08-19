"""Connect to the camera relay's WebSocket for a few seconds; print frame stats.

Run this against a live `python -m bridge.camera_relay` (which must itself be
able to reach teleimager) to sanity-check the whole chain before trusting the
`/vr-control` web page's camera panel.

    CAMERA_RELAY_HOST=127.0.0.1 CAMERA_RELAY_PORT=8766 \
    uv run python scripts/peek_camera_relay.py
"""

import asyncio
import os
import time

from websockets.asyncio.client import connect

HOST = os.environ.get("CAMERA_RELAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("CAMERA_RELAY_PORT", "8766"))
DURATION_S = 5.0


async def main() -> None:
    url = f"ws://{HOST}:{PORT}"
    print(f"connecting to {url} ...")
    async with connect(url) as ws:
        print(f"connected. listening for {DURATION_S:.0f}s ...")
        deadline = time.time() + DURATION_S
        count = 0
        total_bytes = 0
        while time.time() < deadline:
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            count += 1
            total_bytes += len(frame)
            if count <= 3 or count % 30 == 0:
                print(f"  frame #{count}: {len(frame)} bytes")

        elapsed = DURATION_S - max(0.0, deadline - time.time())
        if count == 0:
            print(f"\n(no frames in {DURATION_S:.0f}s -- check teleimager and the relay's own logs)")
        else:
            print(
                f"\n{count} frames, {total_bytes / 1024:.1f} KiB total, "
                f"~{count / max(elapsed, 0.001):.1f} fps"
            )


if __name__ == "__main__":
    asyncio.run(main())
