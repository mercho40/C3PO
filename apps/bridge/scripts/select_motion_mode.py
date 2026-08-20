"""Load a motion controller back onto the robot. The fix for "nothing moves".

Whenever the colleague's `xr_teleoperate` stack runs, it calls
`Enter_Debug_Mode()`, which loops `ReleaseMode()` until `CheckMode` reports an
empty name — **deliberately leaving the robot with no motion controller
loaded**. That state lives in the robot, not in their process, so killing their
processes does not undo it and neither does restarting ours.

What it looks like from our side, and it looks like a network fault:

  * `check_motion_mode` returns `{"name": "", "form": "0"}` with rpc_code 0
  * FSM getters 7001/7002 answer nothing at all, so the bridge logs
    `rpc_code=3102` every poll and `get_state` reports `fsm_id=None`,
    `posture="unknown"`
  * every posture and locomotion command still returns **rpc_code 0** and does
    nothing whatsoever

We hit exactly this on 2026-08-20 and spent a while checking cables, the DDS
interface, `ROBOT_HOST` and the peer config — all of which were fine. The
robot simply had nothing loaded to act on any command.

WHY THIS IS A SCRIPT AND NOT AN MCP TOOL
----------------------------------------
`g1_rpc` registers **only** CHECK_MODE on the motion_switcher client, on
purpose: `_RegistApi` is what makes an api_id sendable at all, and SELECT_MODE
transfers ownership of the robot between controllers. Leaving it unregistered
means no future caller — no agent, no skill, no accident — can reach it.

That property is worth keeping, so this script builds its own client with 1002
registered rather than widening the shared one. Loading a controller is an
operator's decision, taken deliberately, with the robot in sight. Which is also
why it asks before sending.

    cd ~/c3po/apps/bridge
    set -a && . ./.env && set +a
    uv run python scripts/select_motion_mode.py

⚠️ THE ROBOT COMES UNDER POWER. Support it and have the e-stop in reach: a
loaded controller can take a stance, and the robot may be limp right now.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

CHECK_MODE = 1001
SELECT_MODE = 1002

#: The vendor's alias for the AI sport controller — the one the robot boots
#: with, and the one every FSM id in `g1_protocol` is written against.
DEFAULT_MODE = "ai"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_MODE, help=f"mode to select (default: {DEFAULT_MODE})")
    parser.add_argument("--check-only", action="store_true", help="report the current mode and exit")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    from bridge.sdk.connection import init_dds

    init_dds(
        robot_host=os.environ.get("ROBOT_HOST", "127.0.0.1"),
        domain_id=int(os.environ.get("DDS_DOMAIN_ID", "0")),
        interface=os.environ.get("DDS_INTERFACE") or None,
    )
    from bridge.sdk import g1_protocol as P
    from bridge.sdk.g1_rpc import SPORT_TIMEOUT_S, _G1Client

    client = _G1Client(P.MOTION_SWITCHER_SERVICE, (CHECK_MODE, SELECT_MODE), timeout_s=SPORT_TIMEOUT_S)
    client._SetApiVerson(P.MOTION_SWITCHER_API_VERSION)
    client.Init()

    def check() -> tuple[int, str]:
        code, data = client.call_raw(CHECK_MODE, json.dumps({}))
        try:
            return code, str(json.loads(data or "{}").get("name", ""))
        except (ValueError, TypeError):
            return code, ""

    code, name = check()
    print(f"current mode: {name!r}  (rpc_code {code})")

    if args.check_only:
        return 0

    if name:
        print(f"a controller is already loaded ({name!r}) — nothing to do.")
        print("re-run with --name to switch deliberately.")
        return 0

    print("\nNO controller is loaded. Every command returns rpc_code 0 and does nothing.")
    print("⚠️  Loading one brings the robot UNDER POWER — it may take a stance.")
    if not args.yes:
        try:
            if input("Robot supported, e-stop in reach? type 'yes': ").strip().lower() not in ("yes", "y"):
                print("aborted.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\naborted.")
            return 1

    code, _ = client.call_raw(SELECT_MODE, json.dumps({"name": args.name}))
    print(f"\nSelectMode({args.name!r}) -> rpc_code {code}")
    time.sleep(3)

    code, name = check()
    print(f"now loaded : {name!r}")
    if not name:
        print("\nSTILL EMPTY. rpc_code 0 here does not mean it worked — this service")
        print("acks regardless. Try another alias with --name, or power-cycle the robot,")
        print("which loads the default controller at boot.")
        return 1
    print("\nThe FSM getters should start answering now; `get_state` will report a")
    print("real posture instead of 'unknown'. Bring up with damp -> prepare -> 501.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
