"""Settle the G1 arm joint sign conventions, one joint at a time.

`docs/ROBOT-API.md` §9.3 gives the arm joint *order* (15-21 left, 22-28 right:
shoulder pitch/roll/yaw, elbow, wrist roll/pitch/yaw) and that is solid. **No
source we have gives the positive direction of any of them.** So every entry in
`bridge.teleop.retarget.JOINT_SIGNS` is currently an assumption, and until they
are settled the whole arm path stays disabled — an unverified sign on a
shoulder is an arm that swings the opposite way from the one the operator
expects, which is exactly the failure you do not want to discover while wearing
a headset that blocks your view of the robot.

This is the cheapest way to settle them. It engages `rt/arm_sdk` from the
robot's *measured* pose, then moves **one joint** by a few degrees, holds, and
asks you which way it went. Nothing else moves; the legs stay under the
built-in controller throughout, because `rt/arm_sdk` blends rather than
bypasses (`executed = motion*(1-w) + ours*w`).

    RUN THIS STANDING NEXT TO THE ROBOT, WITH THE PHYSICAL E-STOP IN REACH.
    The robot must be STANDING, not walking: unitree_sdk2_python #146 reports
    a G1 driven through arm_sdk while walking bent forward at the waist and
    lost balance. Bring this up in FSM 4 / 500 / 501.

    ssh c3po
    cd ~/c3po/apps/bridge
    SIM_MODE=real ROBOT_HOST=127.0.0.1 DDS_INTERFACE=eth0 TELEOP_ARM_ENABLED=1 \
        uv run python scripts/arm_sign_check.py --side right

Every prompt defaults to abort. Anything other than an explicit answer stops
the run and ramps the weight back down.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys

from bridge.teleop.arm_sdk import ArmSdkUnavailable, get_driver
from bridge.teleop.retarget import NEUTRAL, ArmAngles

# Deliberately small. Large enough to see across a room, small enough that a
# joint driven the wrong way has nowhere to get to before you let go of it.
PROBE_RAD = math.radians(12)
HOLD_S = 2.0

JOINTS = (
    "shoulder_pitch",
    "shoulder_roll",
    "shoulder_yaw",
    "elbow",
    "wrist_roll",
    "wrist_pitch",
    "wrist_yaw",
)

#: What you should see if the sign in `JOINT_SIGNS` is +1, phrased from the
#: robot's point of view so you can stand in front of it and answer.
EXPECTED = {
    "shoulder_pitch": "the arm swings FORWARD (out in front of the robot)",
    "shoulder_roll": "the arm lifts AWAY from the body, out to the side",
    "shoulder_yaw": "the upper arm rotates so the elbow points BACKWARD",
    "elbow": "the forearm folds UP toward the shoulder",
    "wrist_roll": "the palm rotates to face UP",
    "wrist_pitch": "the hand tilts UP at the wrist",
    "wrist_yaw": "the hand turns toward the THUMB side",
}


def ask(question: str) -> str:
    try:
        return input(f"{question} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


def _command_side(driver, side: str, angles: ArmAngles) -> None:
    """Drive one arm and leave the other alone (`None` means "hold")."""
    driver.command(angles if side == "left" else None, angles if side == "right" else None)


async def probe_joint(driver, side: str, index: int, name: str) -> str | None:
    """Move one joint by PROBE_RAD, hold, return to zero, and ask what happened."""
    base = [0.0] * 7
    base[index] = PROBE_RAD
    angles = ArmAngles(*base)

    print(f"\n  moving {side} {name} by +{math.degrees(PROBE_RAD):.0f} deg ...")
    _command_side(driver, side, angles)
    await asyncio.sleep(HOLD_S)

    print(f"  expected for sign +1: {EXPECTED[name]}")
    answer = ask("  did that happen?  [y]es / [n]o, it went the other way / [s]kip / anything else aborts:")

    print("  returning to neutral ...")
    _command_side(driver, side, NEUTRAL)
    await asyncio.sleep(HOLD_S)

    if answer == "y":
        return "+1.0"
    if answer == "n":
        return "-1.0"
    if answer == "s":
        return None
    raise KeyboardInterrupt


async def main_async(args) -> int:
    from bridge.sdk.connection import init_dds

    init_dds(
        robot_host=os.environ.get("ROBOT_HOST", "127.0.0.1"),
        domain_id=int(os.environ.get("DDS_DOMAIN_ID", "0")),
        interface=os.environ.get("DDS_INTERFACE") or None,
    )
    from bridge.sdk.state import get_sampler

    get_sampler().start()
    print("waiting for rt/lowstate ...")
    for _ in range(50):
        if get_sampler().get_arm_state()["lowstate_age_s"] is not None:
            break
        await asyncio.sleep(0.1)

    print(
        "\n!! The robot's arms are about to move.\n"
        "!! It must be STANDING (FSM 4 / 500 / 501), with the e-stop in your hand.\n"
    )
    if ask("type 'ready' to engage rt/arm_sdk:") != "ready":
        print("aborted.")
        return 1

    driver = get_driver()
    try:
        await driver.engage()
    except ArmSdkUnavailable as exc:
        print(f"\nrefused to engage: {exc}")
        return 1

    results: dict[str, str] = {}
    try:
        # Let the blend weight finish ramping in before moving anything, so
        # the first probe is a joint move and not a weight change.
        await asyncio.sleep(2.5)
        for index, name in enumerate(JOINTS):
            if args.joints and name not in args.joints:
                continue
            sign = await probe_joint(driver, args.side, index, name)
            if sign is not None:
                results[name] = sign
    except KeyboardInterrupt:
        print("\naborting — ramping the weight down.")
    finally:
        await driver.release()

    if not results:
        print("\nnothing recorded.")
        return 1

    print(f"\n--- put this in bridge/teleop/retarget.py JOINT_SIGNS[{args.side!r}] ---")
    for name in JOINTS:
        if name in results:
            print(f'    "{name}": {results[name]},')
    print(
        "\nThe other side is usually the mirror for roll and yaw (shared body-frame axes)\n"
        "and identical for pitch and elbow — but check it rather than assuming, since\n"
        "that pattern is an inference from bilateral symmetry, not a vendor statement."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side", choices=("left", "right"), default="right")
    parser.add_argument(
        "--joints",
        nargs="*",
        default=None,
        help="only probe these joints (default: all seven, in order)",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
