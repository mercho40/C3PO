"""End-to-end connection test for the Go2 WebRTC transport.

Usage (with a Go2 on the LAN):
    GO2_HOST=192.168.123.161 uv run python scripts/go2_connect_test.py
    GO2_HOST=192.168.123.161 GO2_PORT=8081 uv run python scripts/go2_connect_test.py

What it does:
  1. Negotiate SDP with the robot's `/offer` endpoint
  2. Wait for the validation challenge + reply with the MD5 dance
  3. Subscribe to `rt/lf/lowstate` + `rt/lf/sportmodestate`
  4. Log message rate, decoded posture, and any fault events for 10 seconds
  5. Issue a `BalanceStand` request (api_id=1002) — safe; no motion
  6. Tear down cleanly

If anything goes wrong, you get a clear error pointing at which step failed.
Use this as the canary when wiring a new Go2 into the bridge.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from bridge.sdk.go2_protocol import GO2_TOPICS, SportCmd, sport_cmd_label
from bridge.sdk.transport.webrtc_go2 import (
    Go2ConnectionConfig,
    Go2WebRTCClient,
)

HOST = os.environ.get("GO2_HOST")
# Default to V3 port (9991) since current firmware uses it. Set GO2_PORT=8081
# explicitly to force the legacy /offer path on older robots.
PORT = int(os.environ.get("GO2_PORT", "9991"))


async def main() -> int:
    if not HOST:
        print("error: set GO2_HOST=<robot_ip>", file=sys.stderr)
        return 2

    config = Go2ConnectionConfig(host=HOST, port=PORT)
    client = Go2WebRTCClient(config)

    msg_counts: dict[str, int] = {}
    last_pose_summary = {"received": False}

    def on_topic(topic: str, data):
        msg_counts[topic] = msg_counts.get(topic, 0) + 1
        if topic == GO2_TOPICS.sportmodestate and not last_pose_summary["received"]:
            last_pose_summary["received"] = True
            # Robot's nav_msgs/Odometry-style payload
            print(f"  first sportmodestate sample: {json.dumps(data, default=str)[:200]}…")

    def on_errors(kind: str, data):
        print(f"  error stream [{kind}]: {data}")

    client.on_message(on_topic)
    client.on_error_message(on_errors)

    print(f"Connecting to Go2 at {HOST}:{PORT} (legacy /offer SDP)…")
    try:
        await client.connect()
    except Exception as exc:
        print(f"  ✗ connect failed: {type(exc).__name__}: {exc}")
        return 1
    print("  ✓ connected + validated")

    client.subscribe(GO2_TOPICS.lowstate)
    client.subscribe(GO2_TOPICS.sportmodestate)
    client.subscribe(GO2_TOPICS.multiple_state)

    print("Sampling state for 10 s…")
    started = time.time()
    while time.time() - started < 10.0:
        await asyncio.sleep(1.0)
        print(f"  t={time.time() - started:.1f}s  msgs={msg_counts}")

    # Read a safe API: BalanceStand request — confirms we can publish + correlate.
    print("Issuing BalanceStand request (api_id=1002) and awaiting response…")
    try:
        response = await client.publish_request(
            GO2_TOPICS.sport_request,
            SportCmd.BALANCE_STAND,
            parameter="{}",
            wait_for_response=True,
            response_timeout_s=3.0,
        )
        header = (response or {}).get("header") or {}
        code = ((header.get("status") or {}).get("code"))
        api_id = ((header.get("identity") or {}).get("api_id"))
        print(
            f"  ✓ response: api_id={api_id} ({sport_cmd_label(api_id) if api_id else '?'}) "
            f"code={code}"
        )
    except Exception as exc:
        print(f"  ✗ request failed: {type(exc).__name__}: {exc}")

    print("Closing…")
    await client.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
