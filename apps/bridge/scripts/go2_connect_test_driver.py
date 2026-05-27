"""Connection canary using go2-webrtc-connect (legion1581's tested driver).

Our handrolled aiortc client completes ICE and the V3 SDP exchange but the
DataChannel never opens — DTLS / SCTP interop with Unitree firmware is the
brittle part. The community driver already solved that, so use it directly.

Usage:
    GO2_HOST=192.168.12.1 uv run python scripts/go2_connect_test_driver.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from go2_webrtc_driver.constants import WebRTCConnectionMethod
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection


HOST = os.environ.get("GO2_HOST", "192.168.12.1")
# AP mode is what you get when the laptop is on the Go2's own Wi-Fi.
METHOD = WebRTCConnectionMethod.LocalAP


async def main() -> int:
    conn = Go2WebRTCConnection(METHOD, ip=HOST)
    print(f"Connecting to Go2 at {HOST} via {METHOD.name}…")
    try:
        await conn.connect()
        print("  ✓ connected (DataChannel open + validation OK)")
    except Exception as exc:
        print(f"  ✗ connect failed: {type(exc).__name__}: {exc}")
        return 1

    msg_counts: dict[str, int] = {}

    def make_handler(topic: str):
        def _handler(message, _msg_data):  # driver passes (full envelope, data)
            msg_counts[topic] = msg_counts.get(topic, 0) + 1
        return _handler

    # Subscribe to the standard Go2 state topics
    for t in ("rt/lf/lowstate", "rt/lf/sportmodestate", "rt/multiplestate"):
        conn.datachannel.pub_sub.subscribe(t, make_handler(t))
        print(f"  subscribed: {t}")

    print("Sampling state for 5 s…")
    started = time.time()
    while time.time() - started < 5.0:
        await asyncio.sleep(1.0)
        print(f"  t={time.time() - started:.1f}s  msgs={msg_counts}")

    # Issue a safe one-shot request: BalanceStand (api_id 1002)
    print("Issuing BalanceStand request…")
    try:
        response = await conn.datachannel.pub_sub.publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1002, "parameter": "{}"},
        )
        print(f"  ✓ response: {response}")
    except Exception as exc:
        print(f"  ✗ request failed: {type(exc).__name__}: {exc}")

    print("Disconnecting…")
    await conn.disconnect()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
