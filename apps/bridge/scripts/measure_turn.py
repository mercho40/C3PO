"""Supervised measurement of `turn` — the run that decides its `works_real`.

Run this **standing next to the robot, with the physical e-stop in reach**,
against a bridge already running onboard (`run_c3po`) and reached over the SSH
tunnel:

    ssh -N -L 8001:127.0.0.1:8001 -o ControlMaster=no c3po   # another terminal
    uv run python scripts/measure_turn.py --degrees 40

WHY A SCRIPT AND NOT "JUST CALL TURN AND WATCH".

`turn`'s docstring says exactly what is missing, and it is not the yaw sign —
that was settled on 2026-08-20, measured three times off `rt/odommodestate`
through the same `send_velocity` path. What has never run on hardware is this
skill's OWN CLOSED LOOP: it reads pose, computes an error, and decides when to
stop. `works_real` means a human watched the skill run, so the evidence has to
be about the loop, and the loop's whole claim is "it converges and then stops".

That claim is three numbers — yaw before, yaw after, and whether the residual
landed inside tolerance — and reading them off a terminal while also watching a
humanoid rotate is how numbers get misremembered. So they are captured here,
wrapped correctly, and printed in a form that can be pasted into a commit.

WHAT IT REFUSES TO DO. It never repeats on its own, never escalates the delta,
and stops on the first refusal. Every run is one confirmed rotation. A skill
whose stopping condition is unproven is the last thing that should be handed a
loop.

EXPECT UNDER-TRAVEL. Measured yaw under-travels command by about 2.2x on this
body, so a 40-degree request may produce ~18 degrees within a short timeout.
That is a finding to record, NOT a failure to retry into — and specifically not
a reason to raise the delta, which is how a 40-degree test becomes a 90-degree
one next to a person.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from typing import Optional

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    print("mcp client not available -- run this via `uv run` inside apps/bridge.")
    sys.exit(2)

DEFAULT_URL = "http://127.0.0.1:8001/mcp"

#: Postures with a walk program. `turn` drives pure yaw velocity, which the
#: firmware ignores without one — the exact state that produced "the robot
#: does not turn with my head" and three other symptoms on 2026-08-21.
WALK_POSTURES = ("walk", "walk_waist", "run")


class Aborted(Exception):
    """Operator declined a stage, or a precondition failed."""


def find_aborted(exc: BaseException) -> Optional[Aborted]:
    """Dig an `Aborted` out of a (possibly nested) ExceptionGroup.

    The MCP client runs its transport in an anyio TaskGroup, so anything raised
    inside the session block comes back wrapped, sometimes twice.
    """
    if isinstance(exc, Aborted):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = find_aborted(inner)
            if found is not None:
                return found
    return None


def innermost(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def wrap_pi(radians: float) -> float:
    """Shortest signed angle. THE reason this is not `after - before`.

    A robot at +175 degrees turning 10 degrees left reads -175 after. The naive
    difference is -350 degrees: a 10-degree turn recorded as most of a full
    rotation the wrong way, which would look like the sign bug that was already
    ruled out and send someone chasing it a second time.
    """
    return (radians + math.pi) % (2.0 * math.pi) - math.pi


def confirm(prompt: str) -> None:
    answer = input(f"\n>>> {prompt} [y/N] ").strip().lower()
    if answer != "y":
        raise Aborted("operator declined")


async def call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    if not result.content:
        return {}
    text = getattr(result.content[0], "text", "")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"raw": text}


def yaw_of(state: dict) -> float:
    """The world yaw, or an abort. Never a default.

    A missing pose is exactly when a measurement must stop: `turn` itself
    cannot close its loop without one, so measuring it against a substituted
    zero would produce a number that describes nothing.
    """
    pose = state.get("pose")
    if not isinstance(pose, dict):
        raise Aborted("no pose in get_state — turn cannot close its loop either")
    yaw = pose.get("yaw_radians_world")
    if not isinstance(yaw, (int, float)) or not math.isfinite(float(yaw)):
        raise Aborted(f"yaw_radians_world is {yaw!r}, not a usable angle")
    return float(yaw)


async def preflight(session: ClientSession) -> dict:
    """Read-only. Confirms this is the robot, awake, and able to turn."""
    print("\n=== READ-ONLY ===")
    state = await call(session, "get_state", {})
    print(f"    {json.dumps(state, default=str)[:400]}")

    if state.get("env") != "real":
        raise Aborted(f"env is {state.get('env')!r}, not 'real'")
    if state.get("stub"):
        raise Aborted("bridge answered in stub mode; it is not talking to the robot")

    posture = state.get("posture")
    if posture not in WALK_POSTURES:
        raise Aborted(
            f"posture is {posture!r}; turn needs a walk program "
            f"({'/'.join(WALK_POSTURES)}). Do damp -> prepare -> 501 first."
        )

    battery = state.get("battery_pct")
    if isinstance(battery, (int, float)) and battery < 25:
        # The robot has already died mid-session at 14%. A closed loop that
        # under-travels is exactly the workload that keeps drawing current.
        raise Aborted(f"battery is {battery}% — charge before measuring a loop")

    print(f"    posture={posture} battery={battery}%")
    return state


async def one_run(session: ClientSession, degrees: float, timeout_s: float) -> dict:
    """One confirmed rotation, measured. Returns the record."""
    before = yaw_of(await call(session, "get_state", {}))
    print(f"\n    yaw before : {math.degrees(before):+8.2f}°")

    confirm(
        f"Room to rotate, nobody within arm's reach, e-stop in hand. "
        f"Command {degrees:+.0f}° ?"
    )

    started = time.monotonic()
    result = await call(
        session,
        "turn",
        {"delta_yaw_radians": math.radians(degrees), "timeout_s": timeout_s},
    )
    elapsed = time.monotonic() - started
    print(f"    turn returned: {json.dumps(result, default=str)[:300]}")

    # Settle before reading: the firmware forgets a setpoint after 1 s, and
    # sampling while the body is still coasting measures the coast, not the
    # stop. The stopping behaviour is the thing under test.
    await asyncio.sleep(1.5)
    after = yaw_of(await call(session, "get_state", {}))

    achieved = wrap_pi(after - before)
    commanded = math.radians(degrees)
    residual = wrap_pi(commanded - achieved)
    ratio = (commanded / achieved) if abs(achieved) > 1e-6 else float("inf")

    print(f"    yaw after  : {math.degrees(after):+8.2f}°")
    print(f"    achieved   : {math.degrees(achieved):+8.2f}°  in {elapsed:.1f}s")
    print(f"    residual   : {math.degrees(residual):+8.2f}°")
    print(f"    under-travel ratio (commanded/achieved): {ratio:.2f}x")

    confirm("Did the robot rotate SMOOTHLY and STOP BY ITSELF?")

    return {
        "commanded_deg": degrees,
        "yaw_before_deg": round(math.degrees(before), 2),
        "yaw_after_deg": round(math.degrees(after), 2),
        "achieved_deg": round(math.degrees(achieved), 2),
        "residual_deg": round(math.degrees(residual), 2),
        "ratio": round(ratio, 2) if math.isfinite(ratio) else None,
        "elapsed_s": round(elapsed, 1),
        "status": result.get("status"),
    }


def verdict(runs: list, tolerance_deg: float) -> str:
    """What the numbers do and do not license.

    Deliberately conservative about the ONE claim `works_real` makes. Turning
    is not the property under test — stopping is. A run that rotated beautifully
    and then kept going is a failure, and a run that under-travelled but halted
    inside tolerance is a pass with a number worth recording.
    """
    if not runs:
        return "NOTHING MEASURED — works_real stays False."
    converged = [r for r in runs if abs(r["residual_deg"]) <= tolerance_deg]
    lines = [
        f"{len(runs)} run(s), {len(converged)} inside {tolerance_deg}° tolerance.",
    ]
    if len(converged) == len(runs) and len(runs) >= 2:
        lines.append(
            "The loop converged and stopped every time it was watched. That is "
            "what works_real asserts — flip it, and paste the table into the "
            "commit so the claim carries its evidence."
        )
    elif converged:
        lines.append(
            "Converged some of the time. works_real STAYS FALSE: an "
            "intermittent stopping condition is the one this flag exists for."
        )
    else:
        lines.append(
            "Never landed inside tolerance. works_real stays False. Record the "
            "under-travel ratio — the timeout may simply be too short for this "
            "body, which is a tuning finding rather than a broken loop."
        )
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--degrees",
        type=float,
        default=40.0,
        help="yaw delta per run (default 40; the docstring's 30-45 window)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="confirmed rotations (default 2; each is prompted separately)",
    )
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument("--tolerance-deg", type=float, default=3.0)
    args = parser.parse_args()

    if abs(args.degrees) > 60:
        # The bound is the point: this measures a stopping condition, and the
        # cost of it not stopping scales with how far it was asked to go.
        print("Refusing: keep the delta at 60 degrees or less for a first loop.")
        return 2

    print(__doc__)
    print(f"Connecting to {args.url} ...")

    runs: list = []
    try:
        async with streamablehttp_client(args.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Connected.")
                await preflight(session)
                for i in range(args.runs):
                    print(f"\n=== RUN {i + 1} of {args.runs} ===")
                    runs.append(await one_run(session, args.degrees, args.timeout_s))
    except BaseException as exc:  # noqa: BLE001 - operator-facing tool
        aborted = find_aborted(exc)
        if aborted is not None:
            print(f"\nSTOPPED: {aborted}")
        elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
            print("\nSTOPPED by operator.")
        else:
            print(f"\nERROR: {innermost(exc)!r}")
            print(
                "If this is a connection error, check the SSH tunnel and that the\n"
                "bridge is running onboard. Port 8001 onboard, 8000 run locally."
            )
        # Fall through: partial measurements are still worth printing.

    print("\n" + "=" * 60)
    for r in runs:
        print(f"  {json.dumps(r)}")
    print()
    print(verdict(runs, args.tolerance_deg))
    print("=" * 60)
    return 0 if runs else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
