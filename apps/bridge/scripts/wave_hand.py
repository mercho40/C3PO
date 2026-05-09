"""Wave the right gripper: cycle open/close a few times.

Per `unitree_sim_isaaclab/tools/data_convert.py`, the gripper command `q`
is in [0.0, 5.4] (PyTorch convention from Unitree's hand control library):
    0.0 = fully closed
    5.4 = fully open
The sim maps this internally to Isaac Lab joint angle.
"""

from __future__ import annotations

import os
import time

ROBOT_HOST = os.environ.get("ROBOT_HOST", "10.20.10.126")
DOMAIN = int(os.environ.get("DDS_DOMAIN_ID", "1"))

from bridge.sdk.connection import init_dds  # noqa: E402

init_dds(robot_host=ROBOT_HOST, domain_id=DOMAIN)

from typing import Any  # noqa: E402

from unitree_sdk2py.core.channel import ChannelPublisher, ChannelSubscriber  # noqa: E402
from unitree_sdk2py.idl.unitree_go.msg.dds_ import (  # noqa: E402
    MotorCmd_,
    MotorCmds_,
    MotorStates_,
)

RIGHT_CMD = "rt/dex1/right/cmd"
RIGHT_STATE = "rt/dex1/right/state"

publisher = ChannelPublisher(RIGHT_CMD, MotorCmds_)
publisher.Init()

_latest_state: list[Any] = [None]


def _on_state(msg: Any) -> None:
    _latest_state[0] = msg


state_sub = ChannelSubscriber(RIGHT_STATE, MotorStates_)
state_sub.Init(_on_state, 10)


def send(q: float) -> None:
    # kp/kd > 0 — without gains, Isaac Lab's PD controller won't move the joint.
    cmd = MotorCmd_(mode=1, q=q, dq=0.0, tau=0.0, kp=10.0, kd=1.0, reserve=[0, 0, 0])
    publisher.Write(MotorCmds_(cmds=[cmd]))


_state_counter = [0]


def _on_state(msg: Any) -> None:  # type: ignore[no-redef]
    _latest_state[0] = msg
    _state_counter[0] += 1


# Re-register with the counting handler.
state_sub.Init(_on_state, 10)


def state_q() -> float | None:
    msg = _latest_state[0]
    if msg is None or not msg.states:
        return None
    return float(msg.states[0].q)


print("Waiting for gripper state...")
for _ in range(50):
    if state_q() is not None:
        break
    time.sleep(0.1)

initial = state_q()
initial_count = _state_counter[0]
print(f"Initial gripper q: {initial} (after {initial_count} state msgs)")

# Stream commands at 50 Hz. Open for 1.5s, close for 1.5s, x3.
print("Waving right gripper at 50Hz (3 cycles open/close)...")
for cycle in range(3):
    target = 5.4
    deadline = time.time() + 1.5
    while time.time() < deadline:
        send(target)
        time.sleep(0.02)
    print(f"  cycle {cycle + 1} after open  (1.5s @ 50Hz): state q={state_q()} (msgs={_state_counter[0]})")
    target = 0.0
    deadline = time.time() + 1.5
    while time.time() < deadline:
        send(target)
        time.sleep(0.02)
    print(f"  cycle {cycle + 1} after close (1.5s @ 50Hz): state q={state_q()} (msgs={_state_counter[0]})")

print(f"\nState messages received: {_state_counter[0] - initial_count} during the wave")
print(f"State q: initial={initial} → final={state_q()}")
if abs((state_q() or 0) - (initial or 0)) > 0.1:
    print("✅ Gripper moved!")
else:
    print("⚠️  Gripper state did not change — Isaac Sim may be ignoring our cmds")
    print("    (or the current task scene overrides external gripper commands)")
