"""Drive the Go2 forward via the community WebRTC driver.

Sends `rt/api/sport/request` Move (api_id=1008) with body-frame velocity
`{x: 0.3, y: 0, z: 0}` for `MOVE_DURATION_S`, then `{0, 0, 0}` for 0.5 s
to bring it to rest. At ~0.3 m/s the robot covers ~0.5 m in 1.7 s; tune
the time if you need a different distance.

Usage:
    GO2_HOST=192.168.12.1 uv run python scripts/go2_move_forward.py
    GO2_HOST=192.168.12.1 DISTANCE_M=0.5 uv run python scripts/go2_move_forward.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from go2_webrtc_driver.constants import WebRTCConnectionMethod
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection


HOST = os.environ.get("GO2_HOST", "192.168.12.1")
DISTANCE_M = float(os.environ.get("DISTANCE_M", "0.5"))
SPEED_MPS = float(os.environ.get("SPEED_MPS", "0.3"))
MOVE_DURATION_S = DISTANCE_M / SPEED_MPS
STOP_HOLD_S = 0.5


async def main() -> int:
    conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP, ip=HOST)
    print(f"Connecting to Go2 at {HOST}…")
    try:
        await conn.connect()
        print("  ✓ connected")
    except Exception as exc:
        print(f"  ✗ connect failed: {type(exc).__name__}: {exc}")
        return 1

    # Ensure the FSM is in BalanceStand so Move is accepted.
    print("Requesting BalanceStand…")
    try:
        await conn.datachannel.pub_sub.publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1002, "parameter": "{}"},
        )
    except Exception as exc:
        print(f"  (BalanceStand request error, continuing: {exc})")
    await asyncio.sleep(0.5)

    print(f"Moving forward: vx={SPEED_MPS} m/s for {MOVE_DURATION_S:.2f} s (~{DISTANCE_M} m)")
    started = time.time()
    last_log = 0.0
    while time.time() - started < MOVE_DURATION_S:
        # Move is api_id=1008; parameter is JSON-encoded {x, y, z} where z is yaw rate.
        await conn.datachannel.pub_sub.publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1008, "parameter": json.dumps({"x": SPEED_MPS, "y": 0.0, "z": 0.0})},
        )
        await asyncio.sleep(0.05)  # 20 Hz publish; firmware expects continuous velocity
        elapsed = time.time() - started
        if elapsed - last_log >= 0.5:
            print(f"  t={elapsed:.1f}s")
            last_log = elapsed

    print(f"Stopping (zero velocity for {STOP_HOLD_S}s)…")
    stop_started = time.time()
    while time.time() - stop_started < STOP_HOLD_S:
        await conn.datachannel.pub_sub.publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1008, "parameter": json.dumps({"x": 0.0, "y": 0.0, "z": 0.0})},
        )
        await asyncio.sleep(0.05)

    print("Disconnecting…")
    await conn.disconnect()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
