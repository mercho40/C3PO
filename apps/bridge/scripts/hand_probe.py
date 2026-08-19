"""Settle which hands are fitted to this robot. Subscribes only; writes nothing.

`docs/ROBOT-HARDWARE.md` has been unable to conclude whether this G1
carries **Dex3-1** (7 DoF, three fingers, radians) or **BrainCo Revo2** (6 DoF,
five fingers, [0,1]) — there is real evidence on both sides, and the two have
incompatible topics, types, motor counts *and units*. Until it is settled,
`bridge.teleop.hands` refuses to publish anything at all.

That doc calls this probe "the highest-value action in the section", and it is
also the cheapest: **a single received message decides the argument**, and this
script opens no publisher, so there is no way for it to move a finger.

    ssh c3po                      # must run onboard: these are DDS topics
    cd ~/c3po/apps/bridge
    SIM_MODE=real ROBOT_HOST=127.0.0.1 DDS_INTERFACE=eth0 \
        uv run python scripts/hand_probe.py

Note the two things it cannot tell you. Silence is not proof of absence: a
`brainco_hand_server` is started by hand with an explicit `--serial` per hand,
so a hand that exists but whose server was never launched stays quiet. And a
Dex3's state would come from a resident service on the *control board*, which
we cannot log into — Unitree states they deploy no services on the Jetson — so
its silence says nothing about the Jetson at all. **A positive result is
conclusive; a negative one is not.** If everything is silent, the answer is
still one glance at the wrists: three fingers is Dex3-1, five is BrainCo.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

DEFAULT_SECONDS = 15.0


def _candidates() -> list[tuple[str, str, str]]:
    """(label, topic, type-name) for every hand this robot might carry."""
    return [
        # Dex3-1. `rt/lf/...` is the variant this robot's topic list shows;
        # the plain name is what the official docs and the sim both use.
        ("dex3-left", "rt/lf/dex3/left/state", "HandState_"),
        ("dex3-right", "rt/lf/dex3/right/state", "HandState_"),
        ("dex3-left (unprefixed)", "rt/dex3/left/state", "HandState_"),
        ("dex3-right (unprefixed)", "rt/dex3/right/state", "HandState_"),
        # BrainCo Revo2, from the running brainco_hand_server.
        ("brainco-left", "rt/brainco/left/state", "MotorStates_"),
        ("brainco-right", "rt/brainco/right/state", "MotorStates_"),
        # Inspire, for completeness -- a G1-EDU Ultimate C/D would have these.
        ("inspire", "rt/inspire/state", "MotorStates_"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    args = parser.parse_args()

    from bridge.sdk.connection import init_dds

    init_dds(
        robot_host=os.environ.get("ROBOT_HOST", "127.0.0.1"),
        domain_id=int(os.environ.get("DDS_DOMAIN_ID", "0")),
        interface=os.environ.get("DDS_INTERFACE") or None,
    )

    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import MotorStates_
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import HandState_

    types: dict[str, Any] = {"HandState_": HandState_, "MotorStates_": MotorStates_}
    seen: dict[str, dict[str, Any]] = {}
    subscribers = []

    def make_handler(label: str, topic: str):
        def handler(msg: Any) -> None:
            if label in seen:
                return
            motors = getattr(msg, "motor_state", None) or getattr(msg, "states", None) or []
            seen[label] = {
                "topic": topic,
                "motors": len(motors),
                "q": [round(float(m.q), 4) for m in motors],
            }
            print(f"  ANSWERED  {label:<26} {topic}  ({len(motors)} motors)")

        return handler

    print(f"Listening for {args.seconds:.0f}s. Nothing is published by this script.\n")
    for label, topic, type_name in _candidates():
        sub = ChannelSubscriber(topic, types[type_name])
        sub.Init(make_handler(label, topic), 10)
        subscribers.append(sub)
        print(f"  subscribed  {label:<26} {topic}")
    print()

    deadline = time.time() + args.seconds
    while time.time() < deadline and len(seen) < len(subscribers):
        time.sleep(0.2)

    print("\n--- result ---")
    if not seen:
        print(
            "Nothing answered. That is NOT evidence the robot has no hands — see this\n"
            "script's docstring. Next step is a physical look at the wrists:\n"
            "  three fingers, 7 DoF  -> Dex3-1     (radians, rt/dex3/{side}/cmd)\n"
            "  five fingers, 6 DoF   -> BrainCo    ([0,1],  rt/brainco/{side}/cmd)"
        )
        return 1

    for label, info in seen.items():
        print(f"{label:<26} {info['topic']}  motors={info['motors']}")
        print(f"{'':<26} q = {info['q']}")

    dex3 = any(k.startswith("dex3") for k in seen)
    brainco = any(k.startswith("brainco") for k in seen)
    print()
    if dex3 and not brainco:
        print("=> Dex3-1.  Set TELEOP_HAND_TYPE=dex3")
    elif brainco and not dex3:
        print(
            "=> BrainCo Revo2.  Set TELEOP_HAND_TYPE=brainco\n"
            "   You STILL need TELEOP_BRAINCO_OPEN_AT: BrainCo never documents which end\n"
            "   of [0,1] is an open hand. Read it off the `q` values above with the hand\n"
            "   physically open, then set 0 or 1 to match."
        )
    else:
        print("=> Both answered. Read the wrists; do not guess which one teleop should drive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
