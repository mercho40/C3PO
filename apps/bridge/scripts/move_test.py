"""First-move test: publish to `rt/reset_pose/cmd` and watch `rt/sim_state` change.

Tries a few likely JSON shapes since unitree_sim_isaaclab's expected schema
isn't documented locally. Reports whichever payload the sim accepts.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

# Use the bridge's unicast peer config (macOS multicast is flaky).
ROBOT_HOST = os.environ.get("ROBOT_HOST", "10.20.10.126")
DOMAIN = int(os.environ.get("DDS_DOMAIN_ID", "1"))

from bridge.sdk.connection import init_dds  # noqa: E402

init_dds(robot_host=ROBOT_HOST, domain_id=DOMAIN)

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber  # noqa: E402
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_  # noqa: E402

RESET_TOPIC = "rt/reset_pose/cmd"
SIM_STATE_TOPIC = "rt/sim_state"

# Use the SDK's channel factory (the one that ChannelFactoryInitialize set up
# inside init_dds). Going through cyclonedds.pub directly didn't see remote
# topics — likely because the bare DomainParticipant didn't pick up the peer XML.
publisher = ChannelPublisher(RESET_TOPIC, String_)
publisher.Init()

_latest_sim_msg: list[Any] = [None]


def _on_sim(msg: Any) -> None:
    _latest_sim_msg[0] = msg


sim_subscriber = ChannelSubscriber(SIM_STATE_TOPIC, String_)
sim_subscriber.Init(_on_sim, 10)


def latest_pose() -> tuple[float, float, float, float] | None:
    msg = _latest_sim_msg[0]
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


# Wait for first sim_state so we know the bridge is alive.
print("Waiting for first sim_state sample...")
deadline = time.time() + 5
while time.time() < deadline:
    if latest_pose() is not None:
        break
    time.sleep(0.1)
before = latest_pose()
print(f"Initial pose: {before}")
if before is None:
    raise SystemExit("No sim_state received — is Isaac Sim publishing?")

# Per unitree_sim_isaaclab/reset_pose_test.py and dds/reset_pose_dds.py, the
# expected payload is a plain integer-as-string (a "reset category"), not JSON.
# Try a few category numbers and see which produces a pose change.
for category in ("1", "2", "3", "0"):
    print(f"\n--- reset_category={category} ---")
    publisher.Write(String_(data=category))
    time.sleep(1.5)
    after = latest_pose()
    print(f"  pose: {after}")
    if after and before and (abs(after[0] - before[0]) > 0.05 or abs(after[1] - before[1]) > 0.05):
        print(f"  ✅ pose changed! reset_category={category} produced a move.")
        break
    before = after
else:
    print("\nNo reset category produced a pose change in 6 seconds.")
