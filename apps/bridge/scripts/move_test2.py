"""Send velocity commands to rt/run_command/cmd and watch the robot move.

Format per unitree_sim_isaaclab/send_commands_8bit.py:
    str([x_vel, y_vel, yaw_vel, height])  # m/s, m/s, rad/s, m
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

ROBOT_HOST = os.environ.get("ROBOT_HOST", "10.20.10.126")
DOMAIN = int(os.environ.get("DDS_DOMAIN_ID", "1"))

from bridge.sdk.connection import init_dds  # noqa: E402

init_dds(robot_host=ROBOT_HOST, domain_id=DOMAIN)

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber  # noqa: E402
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_  # noqa: E402

CMD_TOPIC = "rt/run_command/cmd"
SIM_STATE_TOPIC = "rt/sim_state"

publisher = ChannelPublisher(CMD_TOPIC, String_)
publisher.Init()

_latest: list[Any] = [None]


def _on_sim(msg: Any) -> None:
    _latest[0] = msg


sim_subscriber = ChannelSubscriber(SIM_STATE_TOPIC, String_)
sim_subscriber.Init(_on_sim, 10)


def latest_pose() -> tuple[float, float, float, float] | None:
    msg = _latest[0]
    if msg is None:
        return None
    try:
        outer = json.loads(msg.data)
        inner = json.loads(outer["init_state"])
        x, y, z, qw, _, _, qz = (float(v) for v in inner["articulation"]["robot"]["root_pose"][0][:7])
        yaw = math.atan2(2 * (qw * qz), 1 - 2 * qz * qz)
        return (x, y, z, yaw)
    except Exception as exc:
        print(f"  parse error: {exc}")
        return None


print("Waiting for first sim_state sample...")
for _ in range(50):
    if latest_pose() is not None:
        break
    time.sleep(0.1)

before = latest_pose()
print(f"Initial pose: {before}")

# Match height to current pose so the policy isn't fighting a height-change
# command at the same time as the walk command.
height = round(before[2], 2) if before else 0.78

# Warmup: 1s of zero velocity at current height (let the controller settle).
print(f"Warmup (zero velocity, height={height}, 1s)...")
deadline = time.time() + 1.0
while time.time() < deadline:
    publisher.Write(String_(data=str([0.0, 0.0, 0.0, height])))
    time.sleep(0.02)

# Walk forward at 0.5 m/s for 5 seconds — should travel ~2.5m on a working policy.
print(f"\nForward command [0.5, 0, 0, {height}] for 5s @ 50 Hz...")
deadline = time.time() + 5.0
while time.time() < deadline:
    publisher.Write(String_(data=str([0.5, 0.0, 0.0, height])))
    time.sleep(0.02)
    mid = latest_pose()
    if mid and abs(mid[0] - before[0]) > 0.5:
        print(f"  ✅ pose progressing: {mid}")

print("Stopping (zero velocity for 1.5s)...")
deadline = time.time() + 1.5
while time.time() < deadline:
    publisher.Write(String_(data=str([0.0, 0.0, 0.0, height])))
    time.sleep(0.02)

after = latest_pose()
print(f"\nFinal pose: {after}")
if before and after:
    dx = after[0] - before[0]
    dy = after[1] - before[1]
    print(f"Displacement: dx={dx:+.3f} m, dy={dy:+.3f} m, total={math.hypot(dx, dy):.3f} m")
    if math.hypot(dx, dy) > 0.1:
        print("✅ ROBOT MOVED")
    else:
        print("⚠️  No significant movement — sim may not have a run_command subscriber")
