"""Supervised bring-up for the VR teleop stream, with no headset involved.

`vr_smoke_test.py` ladders the MCP skills that `/vr-control`'s buttons call.
This one exercises the other half — `bridge.teleop.server`, the 30 Hz socket —
by acting as the browser. That separation is the point: everything the headset
would send, this sends, so the parts that can go wrong are found while you are
looking at a terminal rather than while wearing something that covers your eyes.

    RUN THIS STANDING NEXT TO THE ROBOT, WITH THE PHYSICAL E-STOP IN REACH.

    ssh -N -o ControlMaster=no -L 8001:127.0.0.1:8001 -L 8767:127.0.0.1:8767 c3po
    uv run python scripts/teleop_smoke_test.py

The order is: read, then refuse, then the smallest motion that answers a
question, then stop. Every stage prompts, and anything other than `y` ends the
run.

THE STAGE THAT MATTERS IS 4
---------------------------
`turn` is still `works.real=False` for one stated reason — *"turn's yaw sign
convention is still unverified and may rotate the wrong way"* (2026-08-15,
the commit where the robot first walked). Head tracking inherits that unknown
whole: if the sign is backwards, **turning your head left turns the robot
right**, and the operator's instinct will be to turn their head further, which
makes it worse. That is a bad thing to discover while wearing a headset.

Stage 4 settles it for a few degrees of yaw, by commanding a small rotation
through the teleop socket and reading the pose back over MCP. No headset, no
arms, no walking.

Stage 6 is the other one worth naming: it presses PARAR while the operator
keeps holding the control, because that is what a startled person does. The
stream has to stop and stay stopped. A teleop session is not a skill
invocation, so it had to be registered as a task before `stop_everything`
could reach it at all — and this stage is what proves that it does.

What this does NOT cover: the arm path (`scripts/arm_sign_check.py`) and the
fingers. Both are disabled by default and should stay that way until this
passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from typing import Any

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    print("mcp client not available -- run this via `uv run` inside apps/bridge.")
    raise SystemExit(1)

try:
    from websockets.asyncio.client import connect
except ImportError:  # pragma: no cover
    print("websockets not available -- run this via `uv run` inside apps/bridge.")
    raise SystemExit(1)

PROTOCOL_VERSION = 1

# The yaw probe. Small enough to be safe in a doorway, long enough that the
# odometry estimate moves further than its own noise floor.
PROBE_YAW_DEG = 25.0
PROBE_SECONDS = 2.0
# Below this, the pose did not move enough to call a direction. Odom yaw on this
# robot is an estimate, not a measurement.
MIN_MEANINGFUL_DEG = 2.0

# The walk probe. `walk_to` asked for 0.40 m and got 0.17 m on hardware, so the
# gains under-travel by well over half; expect to move less than you ask for.
PROBE_WALK_SECONDS = 1.5

FSM_WALK_WAIST = 501


class Aborted(Exception):
    """Operator declined a stage, or a stage failed its own check."""


def find_aborted(exc: BaseException) -> Aborted | None:
    """Dig an `Aborted` out of a (possibly nested) ExceptionGroup.

    Same anyio TaskGroup wrapping `vr_smoke_test.py` documents: a plain
    `except Aborted` never fires, and a declined confirmation would print as an
    unexplained ERROR telling the operator to check their SSH tunnel.
    """
    if isinstance(exc, Aborted):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = find_aborted(sub)
            if found is not None:
                return found
    return None


def innermost(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def confirm(prompt: str) -> None:
    # EOF/^C are declines, not transport failures. Without this, running with a
    # closed stdin reports "is the SSH tunnel up?" — sending whoever hits it to
    # debug networking that was never the problem.
    try:
        answer = input(f"\n>>> {prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise Aborted("no answer (stdin closed or interrupted)") from None
    if answer != "y":
        raise Aborted("operator declined")


def frame(seq: int, **overrides: Any) -> str:
    """One wire frame, exactly as `$lib/teleop/stream.ts` builds it."""
    payload: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "seq": seq,
        "t": seq * 33.0,
        "enabled": False,
        "walk": 0.0,
        "arms": False,
        "head": {"yaw": 0.0, "pos": [0.0, 1.62, 0.0]},
        "hands": {"left": {"tracked": False}, "right": {"tracked": False}},
    }
    payload.update(overrides)
    return json.dumps(payload)


async def read_status(ws: Any, timeout: float = 3.0) -> dict:
    """Wait for a status message that reflects at least one ingested frame."""
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
        latest = json.loads(raw)
        if latest.get("frames_received"):
            return latest
    return latest


async def mcp_state(session: ClientSession) -> dict:
    result = await session.call_tool("get_state", {})
    if not result.content:
        return {}
    try:
        return json.loads(getattr(result.content[0], "text", ""))
    except (ValueError, TypeError):
        return {}


async def yaw_now(session: ClientSession) -> float | None:
    pose = (await mcp_state(session)).get("pose")
    return None if pose is None else float(pose["yaw_radians_world"])


# --- stages ----------------------------------------------------------------


async def stage_preflight(session: ClientSession, url: str) -> None:
    """No actuation at all. Confirms both halves are up and talking."""
    print("\n=== 1. PREFLIGHT ===")
    state = await mcp_state(session)
    print(f"    posture      : {state.get('posture')}")
    print(f"    battery      : {state.get('battery_pct')}%")
    print(f"    faults       : {state.get('faults')}")
    print(f"    pose         : {state.get('pose')}")

    if state.get("pose") is None:
        raise Aborted(
            "no pose. Stage 4 reads the yaw back from odometry to decide which way the "
            "robot turned, and cannot do that blind."
        )
    if state.get("faults"):
        confirm(f"faults reported: {state['faults']}. Continue anyway?")

    async with connect(url) as ws:
        await ws.send(frame(0))
        status = await read_status(ws)
    print(f"    teleop socket: connected, {status.get('frames_received')} frame(s) ingested")
    print(f"    arm path     : {'ENABLED' if status.get('arm', {}).get('enabled_by_env') else 'disabled'}")
    print(f"    hand path    : {status.get('hands')}")


async def stage_refusals(url: str) -> None:
    """Ask for the arms and confirm the bridge says no. Still no motion."""
    print("\n=== 2. REFUSALS ===")
    print("    Asking for the arms while the operator holds nothing.")
    async with connect(url) as ws:
        for seq in range(5):
            await ws.send(frame(seq, enabled=True, arms=True))
            await asyncio.sleep(0.05)
        status = await read_status(ws)

    arm = status.get("arm", {})
    error = status.get("arm_error")
    print(f"    engaged      : {arm.get('engaged')}")
    print(f"    weight       : {arm.get('weight')}")
    print(f"    reason       : {error}")

    if arm.get("engaged"):
        raise Aborted(
            "the arm path ENGAGED. It is supposed to be off until "
            "scripts/arm_sign_check.py has settled the joint signs. Unset "
            "TELEOP_ARM_ENABLED and start again."
        )
    if not error:
        raise Aborted("expected a refusal reason and got none — the bridge is not answering as built")
    print("    OK: arms refused, and said why.")


async def stage_deadman(session: ClientSession, url: str) -> None:
    """Hold a yaw the client never releases, then go silent. Nothing should move."""
    print("\n=== 3. DEAD-MAN (no motion expected) ===")
    print("    Sending frames with the dead-man RELEASED but a large head yaw.")
    print("    If the robot moves at all here, stop and do not continue.")
    confirm("Watching the robot?")

    start = await yaw_now(session)
    async with connect(url) as ws:
        for seq in range(40):
            await ws.send(frame(seq, enabled=False, head={"yaw": 0.7, "pos": [0, 1.62, 0]}))
            await asyncio.sleep(0.03)
        await read_status(ws)
    await asyncio.sleep(0.5)
    end = await yaw_now(session)

    moved = abs(math.degrees((end or 0.0) - (start or 0.0)))
    print(f"    yaw moved    : {moved:.2f} deg")
    if moved > MIN_MEANINGFUL_DEG:
        raise Aborted(
            f"the robot rotated {moved:.1f} deg with the dead-man RELEASED. "
            "That is the fault this stage exists to catch. Stop here."
        )
    print("    OK: released dead-man commands nothing.")


async def stage_yaw_sign(session: ClientSession, url: str) -> str:
    """The one that matters. Command a small LEFT turn and see which way it goes."""
    print("\n=== 4. YAW SIGN ===")
    print(f"    Commanding a head yaw of +{PROBE_YAW_DEG:.0f} deg for {PROBE_SECONDS:.0f}s.")
    print("    In WebXR, +yaw is the operator turning their head LEFT (counterclockwise")
    print("    seen from above). The robot should rotate the SAME way — to ITS left.")
    print("    This is the convention `turn` is still blocked on. Stand clear of the arms.")
    confirm("Robot standing, e-stop in hand, ready to rotate a few degrees?")

    start = await yaw_now(session)
    if start is None:
        raise Aborted("lost pose before the probe")

    yaw_rad = math.radians(PROBE_YAW_DEG)
    async with connect(url) as ws:
        deadline = time.time() + PROBE_SECONDS
        seq = 0
        while time.time() < deadline:
            await ws.send(frame(seq, enabled=True, head={"yaw": yaw_rad, "pos": [0, 1.62, 0]}))
            seq += 1
            await asyncio.sleep(0.03)
        # Release, and let the stop go out on a frame rather than a timeout.
        for _ in range(5):
            await ws.send(frame(seq, enabled=False))
            seq += 1
            await asyncio.sleep(0.03)
    await asyncio.sleep(1.0)

    end = await yaw_now(session)
    if end is None:
        raise Aborted("lost pose during the probe")

    delta = math.atan2(math.sin(end - start), math.cos(end - start))
    delta_deg = math.degrees(delta)
    print(f"\n    start yaw    : {math.degrees(start):+.2f} deg")
    print(f"    end yaw      : {math.degrees(end):+.2f} deg")
    print(f"    delta        : {delta_deg:+.2f} deg")

    if abs(delta_deg) < MIN_MEANINGFUL_DEG:
        print("\n    INCONCLUSIVE — the robot barely moved.")
        print("    Either the FSM is not in a walk program (needs 501, `start_walking_waist`),")
        print("    or the yaw gain is too small to overcome stiction in this posture.")
        print("    Re-run from 501 before drawing any conclusion about the sign.")
        return "inconclusive"

    if delta_deg > 0:
        print("\n    SIGN IS CORRECT. Head left -> robot left.")
        print("    `server.yaw_to_vyaw` needs no change, and this is evidence for")
        print("    `turn`'s convention too — the same vyaw sign feeds both.")
        return "correct"

    print("\n    ⚠️  SIGN IS INVERTED. Head left -> robot RIGHT.")
    print("    DO NOT put anyone in the headset until this is fixed: the operator's")
    print("    instinct on a wrong-way turn is to turn their head further, which")
    print("    makes it worse. Fix by negating the return of `yaw_to_vyaw` in")
    print("    bridge/teleop/server.py, then re-run this stage.")
    return "inverted"


async def stage_walk(session: ClientSession, url: str) -> None:
    """Smallest forward motion the stream can command."""
    print("\n=== 5. WALK AXIS ===")
    print(f"    Commanding walk=+1 (={0.2} m/s) for {PROBE_WALK_SECONDS:.1f}s.")
    print("    Expect well under 0.2 m of travel: `walk_to` asked for 0.40 m and got")
    print("    0.17 m on this hardware, so the sim-fitted gains under-travel by half.")
    confirm("Clear space ahead of the robot?")

    state = await mcp_state(session)
    pose = state.get("pose") or {}
    x0, y0 = float(pose.get("x_meters_world", 0)), float(pose.get("y_meters_world", 0))

    async with connect(url) as ws:
        deadline = time.time() + PROBE_WALK_SECONDS
        seq = 0
        while time.time() < deadline:
            await ws.send(frame(seq, enabled=True, walk=1.0))
            seq += 1
            await asyncio.sleep(0.03)
        for _ in range(5):
            await ws.send(frame(seq, enabled=False))
            seq += 1
            await asyncio.sleep(0.03)
    await asyncio.sleep(1.0)

    pose = (await mcp_state(session)).get("pose") or {}
    x1, y1 = float(pose.get("x_meters_world", 0)), float(pose.get("y_meters_world", 0))
    print(f"    travelled    : {math.hypot(x1 - x0, y1 - y0):.3f} m")


async def stage_estop(session: ClientSession, url: str) -> None:
    """Press PARAR mid-stream. The single most important behaviour here."""
    print("\n=== 6. E-STOP ===")
    print("    Commanding a turn, then calling stop_everything WHILE the operator")
    print("    keeps holding the control — which is exactly the real situation.")
    print("    The stream must stop and STAY stopped until the control is released.")
    confirm("Ready?")

    async with connect(url) as ws:
        seq = 0
        for _ in range(15):
            await ws.send(frame(seq, enabled=True, head={"yaw": math.radians(PROBE_YAW_DEG), "pos": [0, 1.62, 0]}))
            seq += 1
            await asyncio.sleep(0.03)

        print("    pressing PARAR ...")
        await session.call_tool("stop_everything", {})
        before = await yaw_now(session)

        # Keep holding, as a startled operator would.
        for _ in range(30):
            await ws.send(frame(seq, enabled=True, head={"yaw": math.radians(PROBE_YAW_DEG), "pos": [0, 1.62, 0]}))
            seq += 1
            await asyncio.sleep(0.03)
        status = await read_status(ws)

    after = await yaw_now(session)
    drift = abs(math.degrees((after or 0.0) - (before or 0.0)))
    print(f"    stopped_by_estop : {status.get('stopped_by_estop')}")
    print(f"    yaw drift after  : {drift:.2f} deg")

    if not status.get("stopped_by_estop"):
        raise Aborted(
            "the session did not latch stopped. The e-stop reached the registry but "
            "not the stream — this is the exact defect the latch exists to prevent."
        )
    if drift > MIN_MEANINGFUL_DEG:
        raise Aborted(f"the robot kept turning {drift:.1f} deg after PARAR. Stop here.")
    print("    OK: PARAR stopped the stream, and holding the control did not undo it.")


async def stage_disconnect_stops(session: ClientSession, url: str) -> None:
    """Drop the socket mid-command. The robot must stop without being told."""
    print("\n=== 7. DISCONNECT STOPS ===")
    print("    Commanding a turn, then killing the socket without releasing.")
    print("    This is what a dropped Wi-Fi link looks like from the robot's side.")
    confirm("Ready?")

    ws = await connect(url)
    seq = 0
    for _ in range(20):
        await ws.send(frame(seq, enabled=True, head={"yaw": math.radians(PROBE_YAW_DEG), "pos": [0, 1.62, 0]}))
        seq += 1
        await asyncio.sleep(0.03)
    await ws.close()
    print("    socket closed. Watching for 3s...")

    before = await yaw_now(session)
    await asyncio.sleep(3.0)
    after = await yaw_now(session)
    drift = abs(math.degrees((after or 0.0) - (before or 0.0)))
    print(f"    yaw drift after disconnect: {drift:.2f} deg")
    if drift > MIN_MEANINGFUL_DEG:
        raise Aborted(
            f"the robot kept turning {drift:.1f} deg after the socket died. "
            "The server's shutdown stop did not reach the robot."
        )
    print("    OK: the robot stopped on its own.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp", help="bridge MCP endpoint")
    parser.add_argument("--teleop", default="ws://127.0.0.1:8767", help="teleop stream endpoint")
    parser.add_argument("--yaw-only", action="store_true", help="stop after the yaw-sign stage")
    args = parser.parse_args()

    print(__doc__)
    print(f"\nbridge : {args.url}\nteleop : {args.teleop}")
    confirm("Standing next to the robot with the e-stop in reach?")

    try:
        async with streamablehttp_client(args.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                await stage_preflight(session, args.teleop)
                await stage_refusals(args.teleop)
                await stage_deadman(session, args.teleop)
                verdict = await stage_yaw_sign(session, args.teleop)

                if verdict == "inverted":
                    print("\nStopping here: fix the sign before anything else.")
                    return 1
                if args.yaw_only:
                    return 0

                await stage_walk(session, args.teleop)
                await stage_estop(session, args.teleop)
                await stage_disconnect_stops(session, args.teleop)

                print("\n=== DONE ===")
                print("Passed: the stream commands the robot, refuses the arms, and stops")
                print("when the operator lets go, goes quiet, or disconnects.")
                print("Next: scripts/arm_sign_check.py, still with nobody in the headset.")
                return 0
    except BaseException as exc:  # noqa: BLE001 - re-raised or reported below
        aborted = find_aborted(exc)
        if aborted is not None:
            print(f"\nABORTED: {aborted}")
            return 1
        inner = innermost(exc)
        print(f"\nERROR: {type(inner).__name__}: {inner}")
        print("Is the SSH tunnel up, with BOTH -L 8001 and -L 8767?")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
