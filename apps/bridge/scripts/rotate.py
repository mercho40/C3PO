"""Rotate the robot in place by a target yaw delta (radians).

Usage:
    uv run --project apps/bridge python scripts/rotate.py 1.5708   # ~90° CCW
    uv run --project apps/bridge python scripts/rotate.py -- -1.5708  # 90° CW
"""

from __future__ import annotations

import math
import os
import sys
import time

ROBOT_HOST = os.environ.get("ROBOT_HOST", "10.20.10.126")
DOMAIN = int(os.environ.get("DDS_DOMAIN_ID", "1"))

from bridge.sdk.connection import init_dds  # noqa: E402

init_dds(robot_host=ROBOT_HOST, domain_id=DOMAIN)

from bridge.sdk.state import get_sampler  # noqa: E402
from unitree_sdk2py.core.channel import ChannelPublisher  # noqa: E402
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_  # noqa: E402

CMD_TOPIC = "rt/run_command/cmd"
HEIGHT = 0.78
MAX_YAW_VEL = 1.4
KP = 1.5
LOOP_HZ = 50

publisher = ChannelPublisher(CMD_TOPIC, String_)
publisher.Init()


def send(vx: float, vy: float, vyaw: float, h: float) -> None:
    publisher.Write(String_(data=str([float(vx), float(vy), float(vyaw), float(h)])))


def rotate_by(delta_yaw: float, timeout_s: float = 90.0) -> None:
    sampler = get_sampler()
    # Wait for first pose.
    for _ in range(50):
        if sampler.get_state().get("pose"):
            break
        time.sleep(0.1)
    start_pose = sampler.get_state()["pose"]
    if not start_pose:
        print("No pose available — can't rotate")
        return
    start_yaw = float(start_pose["yaw_radians_world"])
    target_yaw = start_yaw + delta_yaw
    print(f"Start yaw: {math.degrees(start_yaw):+.1f}°  target: {math.degrees(target_yaw):+.1f}°")

    period = 1.0 / LOOP_HZ
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pose = sampler.get_state().get("pose")
        if not pose:
            time.sleep(period)
            continue
        yaw = float(pose["yaw_radians_world"])
        err = math.atan2(math.sin(target_yaw - yaw), math.cos(target_yaw - yaw))
        if abs(err) < 0.05:  # ~3° tolerance
            print(f"Reached: yaw={math.degrees(yaw):+.1f}° (err={math.degrees(err):+.2f}°)")
            break
        vyaw = max(-MAX_YAW_VEL, min(MAX_YAW_VEL, KP * err))
        send(0.0, 0.0, vyaw, HEIGHT)
        time.sleep(period)
    else:
        final_yaw = float(sampler.get_state()["pose"]["yaw_radians_world"])
        print(f"Timeout. Final yaw: {math.degrees(final_yaw):+.1f}°")

    # Always stop.
    print("Stopping...")
    for _ in range(int(LOOP_HZ * 0.5)):
        send(0.0, 0.0, 0.0, HEIGHT)
        time.sleep(period)


if __name__ == "__main__":
    delta = float(sys.argv[1]) if len(sys.argv) > 1 else math.pi / 2  # default 90° CCW
    print(f"Rotating by {math.degrees(delta):+.1f}° (positive = left/CCW)")
    rotate_by(delta)
    print("Done.")
