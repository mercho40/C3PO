#!/usr/bin/env python3
"""Is the vendor lidar_driver running, and restart it so the Mid-360 republishes.

Run on the Jetson with the bridge's venv (it has unitree_sdk2py):

    cd ~/c3po/apps/bridge && uv run python ../../scripts/robot/lidar_service.py [list|restart]

Why this exists: Nav2 reads the LiDAR through the vendor republish
(`rt/utlidar/cloud_livox_mid360`, ROBOT-HARDWARE.md §4.5 Route 1). That topic
only exists while the control board's `lidar_driver` service is running AND
owns the sensor. Any other driver that connected to the Mid-360 re-points its
unicast at itself (§4.3) — gemm's left it aimed at the Jetson — and the vendor
republish goes silent. Restarting `lidar_driver` is how the vendor takes the
sensor back. Our own livox_ros_driver2 (Route 2) never produced points on this
robot (§4.5, 2026-08-21 and again 2026-11-03).

`list` is a read (robot_state 1003). `restart` writes ONE thing: robot_state
1001 ServiceSwitch for `lidar_driver` only, off then on. It refuses any other
service name, so it cannot be turned into a switch for ai_sport (locomotion).

The request is `{"name": ..., "switch": 0|1}` at API version 1.0.0.2, as in
Unitree's own unitree_sdk2py robot_state_client.ServiceSwitch (ROBOT-API.md §8's
`swit` was a transcription slip).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

SERVICE = "lidar_driver"
API_SERVICE_SWITCH = 1001
API_SERVICE_LIST = 1003


def _client():
    import os

    from bridge.sdk.connection import init_dds
    from bridge.sdk.g1_rpc import _G1Client

    # The bridge's own settings, read the way mcp_server.py reads them (source .env first).
    init_dds(
        robot_host=os.environ.get("ROBOT_HOST", "127.0.0.1"),
        interface=os.environ.get("DDS_INTERFACE", "").strip() or None,
        sim_mode="real",
    )
    c = _G1Client("robot_state", (API_SERVICE_SWITCH, API_SERVICE_LIST), timeout_s=5.0)
    c._SetApiVerson("1.0.0.2")  # unitree_sdk2py robot_state_api.ROBOT_STATE_API_VERSION
    c.Init()
    return c


def services(c) -> list[dict]:
    code, data = c.call_raw(API_SERVICE_LIST, "{}")
    if code != 0 or not data:
        raise SystemExit(f"ServiceList failed: rpc_code={code}")
    parsed = json.loads(data)
    # A bare array on this firmware, an envelope in the docs (ROBOT-API.md §8).
    return parsed if isinstance(parsed, list) else (parsed.get("data") or [])


def lidar_state(c) -> str:
    for s in services(c):
        if s.get("name") == SERVICE:
            return "running" if s.get("status") == 0 else "stopped"  # 0 = running here
    return "absent"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="list", choices=("list", "restart"))
    args = ap.parse_args()
    c = _client()
    if args.action == "list":
        for s in services(c):
            print(f"{s.get('name'):<28} {'running' if s.get('status') == 0 else 'stopped'}"
                  f"{'  (protected)' if s.get('protect') else ''}")  # fmt: skip
        return 0
    print(f"{SERVICE}: {lidar_state(c)} -> restarting")
    for value in (0, 1):
        code, data = c.call_raw(API_SERVICE_SWITCH, json.dumps({"name": SERVICE, "switch": value}))
        print(f"  ServiceSwitch switch={value}: rpc_code={code} {data or ''}")
        if code != 0:
            return 1
        time.sleep(3.0)
    print(f"{SERVICE}: {lidar_state(c)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
