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

    **FSM 4 ONLY.** This script now refuses to run in 500 or 501, even though
    `arm_sdk` itself permits them. Learned the hard way on 2026-08-20: started
    at 501, and the robot stepped continuously through the whole probe. 501 is
    a *gait program* — its policy is live and actively balancing, so blending
    arm commands into it perturbs that balance and the policy answers by
    stepping. It does not matter that no velocity was ever commanded.

    That made the readings worthless (a moving body cannot be attributed to
    the joint under test) and put us in exactly the condition
    unitree_sdk2_python #146 describes: a G1 driven through arm_sdk in a gait
    program, bending forward and losing balance. Ours was on a gantry.

    FSM 4 is locked stance: the legs are held and the robot physically cannot
    step, so any movement you see is the joint being probed. Get there with
    `damp` then `prepare` — note that `prepare` will NOT transition directly
    from 501, it acks with code 0 and does nothing.

    ssh c3po
    cd ~/c3po/apps/bridge
    SIM_MODE=real ROBOT_HOST=127.0.0.1 DDS_INTERFACE=eth0 TELEOP_ARM_ENABLED=1 \
        uv run python scripts/arm_sign_check.py --side right

Every prompt defaults to abort. Anything other than an explicit answer stops
the run and ramps the weight back down.

**Rehearse it first with `--dry`.** That walks the identical prompt sequence
with no DDS, no publisher and no robot, so you can read every question and
decide your answers at a desk rather than composing them while standing next to
a powered humanoid. The only difference is that nothing moves — which is why
the dry run cannot tell you a sign, only what it is going to ask.
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


class DryDriver:
    """Stands in for the real driver under `--dry`. Publishes nothing.

    Deliberately not a mock of the whole `ArmSdkDriver` surface — it implements
    exactly what this script calls, so if the script grows a call this class
    does not have, the dry run fails loudly instead of quietly diverging from
    what the real run would do.
    """

    engaged = False

    async def engage(self) -> None:
        print("  [dry] would engage rt/arm_sdk from the measured arm pose")
        print("  [dry] would ramp the blend weight 0 -> 1 over 2.0 s")

    def command(self, left, right) -> None:
        side = "left" if left is not None else "right"
        angles = left if left is not None else right
        moved = [
            f"{name}={math.degrees(v):+.1f}deg"
            for name, v in zip(JOINTS, angles.as_tuple(), strict=True)
            if abs(v) > 1e-9
        ]
        print(f"  [dry] would command {side} arm: {', '.join(moved) or 'neutral'}")

    async def release(self) -> None:
        print("  [dry] would ramp the blend weight 1 -> 0 over 2.0 s and let go")


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
    await asyncio.sleep(0 if isinstance(driver, DryDriver) else HOLD_S)

    print(f"  expected for sign +1: {EXPECTED[name]}")
    answer = ask(
        "  did that happen?  y = yes / n = no, it went the other way / "
        "s = skip / anything else ABORTS:"
    )

    print("  returning to neutral ...")
    _command_side(driver, side, NEUTRAL)
    await asyncio.sleep(0 if isinstance(driver, DryDriver) else HOLD_S)

    # Accept the whole word as well as the letter. The prompt reads
    # "[y]es / [n]o", which invites typing "yes" — and on the dry run that is
    # exactly what happened, aborting the sequence. Defaulting to abort is
    # right for a typo; it is wrong for the most natural possible answer to
    # the question being asked.
    if answer in ("y", "yes", "si", "sí"):
        return "+1.0"
    if answer in ("n", "no"):
        return "-1.0"
    if answer in ("s", "skip"):
        return None
    raise KeyboardInterrupt


async def main_async(args) -> int:
    if args.dry:
        print("\n*** DRY RUN — no DDS, no publisher, nothing moves. ***")
        print("*** Answers are not recorded as signs; this only rehearses the script. ***\n")
        driver = DryDriver()
        results: dict[str, str] = {}
        try:
            for index, name in enumerate(JOINTS):
                if args.joints and name not in args.joints:
                    continue
                sign = await probe_joint(driver, args.side, index, name)
                if sign is not None:
                    results[name] = sign
        except KeyboardInterrupt:
            print("\n[dry] aborted — on a real run this is where the weight would ramp down.")
            return 1
        print(f"\n[dry] {len(results)} joint(s) answered. Nothing was written anywhere.")
        print("[dry] Re-run without --dry, next to the robot, to record real signs.")
        return 0

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

    # Says FSM 4, and only 4, because that is what the gate below enforces.
    # It used to read "FSM 4 / 500 / 501" — the driver's range, not this
    # script's — so the operator was told a gait program was acceptable
    # immediately before being refused for being in one. Worse, 501 is exactly
    # what made the robot walk during a sign check once already: the prompt was
    # describing the hazard as a valid state.
    print(
        "\n!! The robot's arms are about to move.\n"
        "!! It must be in FSM 4 — LOCKED STANCE, not a gait program — with the\n"
        "!! e-stop in your hand. 500 and 501 will be refused: their policy\n"
        "!! balances actively, so blending arm commands makes the robot step.\n"
    )
    if ask("type 'ready' to engage rt/arm_sdk:") not in ("ready", "y", "yes"):
        print("aborted.")
        return 1

    # Refuse a gait program outright — see the module docstring. The driver
    # allows 4/500/501 because Unitree documents all three; this script is
    # stricter because it needs a still body to produce a readable answer, and
    # because a stepping robot is the one condition the arm path's only field
    # report of a balance loss describes.
    fsm = get_sampler().get_arm_state().get("fsm_id")
    if fsm != 4:
        print(f"\nrefused: the robot is in FSM {fsm}, not 4 (locked stance).")
        print("500 and 501 are gait programs — their policy balances actively, so")
        print("blending arm commands makes the robot step, which both ruins the")
        print("reading and is the documented balance hazard. Get to 4 first:")
        print("    damp, then prepare   (prepare will NOT transition from 501)")
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
    parser.add_argument(
        "--dry",
        action="store_true",
        help="rehearse the prompts with no DDS and no motion",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
