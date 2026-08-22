"""Supervised first-motion ladder for the VR teleop path.

Run this **standing next to the robot, with the physical e-stop in reach**,
against a bridge already running onboard (`c3po start`) and reached over the SSH
tunnel. It walks the escalation in the only order that makes sense — read
before write, arms before legs, smallest possible motion before anything
sustained — and stops dead on the first failure.

    ssh -N -L 8001:127.0.0.1:8001 -o ControlMaster=no c3po   # in another terminal
    uv run python scripts/vr_smoke_test.py

Why a script rather than a checklist: every one of these calls has a specific
"what should I see" and a specific abort condition, and the interesting ones
(`walk_velocity`, `dance`) have never executed on this hardware. Encoding that
means the operator watches the robot instead of reading a doc, and nothing
escalates without an explicit typed confirmation.

Nothing here is automatic. Each stage prompts, and anything other than `y`
stops the run. That is deliberate: this is the first time these commands reach
a humanoid, and the failure mode is a machine falling on someone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    print("mcp client not available -- run this via `uv run` inside apps/bridge.")
    sys.exit(2)


DEFAULT_URL = "http://127.0.0.1:8001/mcp"


class Aborted(Exception):
    """Operator declined a stage, or a stage failed its own check."""


def find_aborted(exc: BaseException) -> Aborted | None:
    """Dig an `Aborted` out of a (possibly nested) ExceptionGroup.

    The MCP client runs its transport in an anyio TaskGroup, so anything we
    raise inside the session block comes back wrapped — sometimes twice. A
    plain `except Aborted` therefore never fires, and a declined confirmation
    would print as an unexplained ERROR telling the operator to check their
    SSH tunnel.
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
    """Unwrap nested ExceptionGroups down to the first real exception.

    Same TaskGroup wrapping as above: without this, "the tunnel isn't up" --
    far and away the most likely failure here -- prints as a wall of
    `ExceptionGroup(...)` repr instead of `ConnectError`.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def confirm(prompt: str) -> None:
    answer = input(f"\n>>> {prompt} [y/N] ").strip().lower()
    if answer != "y":
        raise Aborted("operator declined")


def show(label: str, payload: Any) -> None:
    print(f"    {label}: {json.dumps(payload, indent=2, default=str)[:600]}")


async def call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    if not result.content:
        return {}
    text = getattr(result.content[0], "text", "")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"raw": text}


async def stage_read_only(session: ClientSession) -> dict:
    """No actuation. Confirms we are talking to the robot we think we are."""
    print("\n=== 1. READ-ONLY ===")
    state = await call(session, "get_state", {})
    show("get_state", state)

    env = state.get("env")
    if env != "real":
        raise Aborted(
            f"env is {env!r}, not 'real' -- this ladder is for real hardware. "
            "Check SIM_MODE onboard before continuing."
        )
    if state.get("stub"):
        raise Aborted("bridge answered in stub mode; it is not talking to the robot")

    mode = await call(session, "check_motion_mode", {})
    show("check_motion_mode", mode)
    name = (mode.get("mode") or {}).get("name") if isinstance(mode.get("mode"), dict) else None
    if name == "":
        print(
            "    NOTE: motion_switcher reports an EMPTY controller name. In that\n"
            "    state the sport service returns code 0 to everything and does\n"
            "    nothing -- posture/FSM calls will look like they worked and the\n"
            "    robot will not move. See docs/ROBOT-API.md §3."
        )
    return state


async def stage_speech(session: ClientSession) -> None:
    """First write, and the safest one: no joint moves at all."""
    print("\n=== 2. SPEECH (no motion) ===")
    confirm("Robot powered, area clear, e-stop in hand. Say a test phrase?")
    res = await call(session, "say", {"text": "Prueba de sonido"})
    show("say", res)
    confirm("Did you HEAR the robot speak?")


async def stage_gesture(session: ClientSession) -> None:
    """Arms only. Verified-live on this hardware (unlike everything below)."""
    print("\n=== 3. ARM GESTURE (wave) ===")
    print(
        "    Arm gestures need a locomotion-active FSM state. If this returns\n"
        "    7404/7302, the robot is not in walk/walk_waist/run -- that is a\n"
        "    precondition failure, not a bug in the skill."
    )
    confirm("Stand clear of the ARMS. Send `wave`?")
    res = await call(session, "wave", {})
    show("wave", res)
    if res.get("status") not in {"completed", "ok"}:
        raise Aborted(f"wave did not complete: {res.get('status')} / {res.get('error')}")
    confirm("Did the arm actually MOVE? (rpc code 0 alone does not prove it)")
    await call(session, "release_arm", {})


async def stage_dance(session: ClientSession) -> None:
    """First execution of the new skill. Three gestures, arms only, no legs."""
    print("\n=== 4. DANCE (NEVER RUN ON HARDWARE) ===")
    print(
        "    Sequences BOTH_HANDS_UP -> HIGH_FIVE -> WAVE_UNDER_HEAD, each\n"
        "    followed by RELEASE_ARM. Arms only; the legs are not commanded.\n"
        "    Abort condition: anything jerky, or an arm that does not settle\n"
        "    between steps."
    )
    confirm("Clear space around the ARMS. Run `dance` for the first time?")
    res = await call(session, "dance", {})
    show("dance", res)
    if res.get("status") != "completed":
        raise Aborted(f"dance did not complete: {res.get('status')} / {res.get('error')}")
    steps = (res.get("result") or {}).get("steps") or []
    print(f"    {len(steps)} steps dispatched, all rpc_code 0"
          if all(s.get("rpc_code") == 0 for s in steps) else "    SOME STEPS RETURNED NON-ZERO")
    confirm("Did all three gestures run cleanly, arms settling between each?")
    await call(session, "release_arm", {})


async def stage_first_velocity(session: ClientSession) -> None:
    """The one that moves the legs. Smallest command the API accepts."""
    print("\n=== 5. FIRST VELOCITY (NEVER RUN ON HARDWARE) ===")
    print(
        "    vx=0.1 m/s for 0.5s -- roughly a single small step forward.\n"
        "    This is the first SetVelocity ever dispatched to this robot.\n"
        "    The firmware stops itself after the duration; the bridge clamps\n"
        "    to 0.3 m/s regardless of what is asked.\n\n"
        "    ABORT IMMEDIATELY (physical e-stop) if the robot lurches, steps\n"
        "    sideways, or does not stop on its own within ~1s."
    )
    confirm("At least 2m clear ahead. Someone spotting. Send vx=0.1 for 0.5s?")
    res = await call(session, "walk_velocity", {"vx": 0.1, "duration_s": 0.5})
    show("walk_velocity", res)
    if res.get("status") not in {"completed", "ok"}:
        raise Aborted(f"walk_velocity did not complete: {res.get('status')} / {res.get('error')}")
    confirm("Did it take a small step forward AND stop by itself?")


async def stage_stop(session: ClientSession) -> None:
    """Prove the abort path works before trusting anything sustained."""
    print("\n=== 6. STOP PATH ===")
    print(
        "    Before any sustained motion, confirm the stop actually stops.\n"
        "    `stop_everything` damps on real hardware -- a standing robot will\n"
        "    GO LIMP and needs support or it will drop."
    )
    confirm("Robot supported / seated so damping is safe. Send `stop_everything`?")
    res = await call(session, "stop_everything", {})
    show("stop_everything", res)
    confirm("Did the robot visibly go limp (damp)?")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"bridge MCP URL (default {DEFAULT_URL})")
    parser.add_argument(
        "--skip-legs",
        action="store_true",
        help="run everything except stage 5 (the first leg motion)",
    )
    args = parser.parse_args()

    print(__doc__)
    print(f"Connecting to {args.url} ...")

    try:
        async with streamablehttp_client(args.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Connected.")

                await stage_read_only(session)
                await stage_speech(session)
                await stage_gesture(session)
                await stage_dance(session)
                if args.skip_legs:
                    print("\n=== 5. FIRST VELOCITY -- SKIPPED (--skip-legs) ===")
                else:
                    await stage_first_velocity(session)
                await stage_stop(session)

    except BaseException as exc:  # noqa: BLE001 - operator-facing tool
        aborted = find_aborted(exc)
        if aborted is not None:
            print(f"\nSTOPPED: {aborted}")
            print("Nothing further was sent. Re-run when ready.")
            return 1
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            print("\nSTOPPED by operator. Nothing further was sent.")
            return 1
        print(f"\nERROR: {innermost(exc)!r}")
        print(
            "If this is a connection error, check the SSH tunnel is up and the\n"
            "bridge is running onboard (`c3po start`). Note the port differs by\n"
            "target: 8001 onboard, 8000 for a locally-run bridge."
        )
        return 2

    print("\n" + "=" * 60)
    print("Ladder complete. Everything above was watched by a human.")
    print("Update apps/bridge/README.md's Phase status to record what now")
    print("has a verified works_real -- and flip works_real in mcp_server.py")
    print("ONLY for the skills you actually saw run.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
