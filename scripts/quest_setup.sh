#!/usr/bin/env bash
# Point a Meta Quest at the console, over USB, with everything on localhost.
#
# WHY LOCALHOST AND NOT THE LAN
# -----------------------------
# WebXR refuses to start an `immersive-vr` session outside a **secure context**.
# Browsing the Quest to `http://<mac-ip>:3001` is not one, so `navigator.xr` is
# simply undefined there and /vr-control reports "WebXR no está disponible" with
# nothing obviously wrong. The usual fix is HTTPS with a self-signed cert, which
# means a certificate warning to click through in a headset, per port.
#
# `http://localhost` IS a secure context — the spec calls it a potentially
# trustworthy origin, and Quest Browser is Chromium, which implements that. So
# `adb reverse` is the clean answer: the Quest's own localhost is forwarded over
# USB to this machine's ports. The page becomes secure-context, WebXR works, and
# `ws://localhost:8767` is same-scheme so there is no mixed-content problem
# either. Nothing is exposed to the school LAN, which is a bonus rather than a
# cost — the teleop socket has no authentication of its own.
#
# WHAT IT CHECKS BEFORE FORWARDING
# --------------------------------
# Every port is verified to be listening on this machine FIRST. A forward to a
# dead port succeeds silently and fails later, in the headset, as a page that
# will not load — which is the worst possible moment to learn the SSH tunnel
# dropped.
#
# FIRST RUN AGAINST A REAL QUEST: 2026-08-21. adb detection, authorisation, the
# port checks and all four `adb reverse` forwards work, and the Quest browser
# loads the console over `http://localhost:3001` — confirmed by Vite
# server-rendering a request that came from the headset. The secure-context
# reasoning above holds.
#
# Two bugs surfaced on that run, both now fixed and both worth knowing about:
#   * Vite binds `localhost` -> `::1` ONLY, while `adb reverse` connects over
#     IPv4. The forward succeeded and the headset got a blank page with nothing
#     logged anywhere. `apps/web/package.json` now passes `--host 127.0.0.1`.
#   * The empty-tunnel check below was defeated by `pipefail` (see §3).
#
# STILL UNPROVEN: that an immersive WebXR session drives the robot. On the first
# run no teleop session ever registered (`list_active_tasks` stayed empty),
# because 8767 was not yet forwarded. Head yaw reaching the robot from a headset
# has not been observed end to end.

set -euo pipefail

_bold=$'\033[1m'; _red=$'\033[31m'; _green=$'\033[32m'; _yellow=$'\033[33m'; _reset=$'\033[0m'
ok()   { printf '  %s✓%s %s\n' "$_green" "$_reset" "$1"; }
warn() { printf '  %s!%s %s\n' "$_yellow" "$_reset" "$1"; }
err()  { printf '  %s✗%s %s\n' "$_red" "$_reset" "$1" >&2; }
say()  { printf '\n%s%s%s\n' "$_bold" "$1" "$_reset"; }

# WHOSE SERVER IS THAT, ACTUALLY?
#
# "Something is listening" is not "our app is listening", and on 2026-09-08 that
# gap cost a whole headset session. Port 3001 held two strangers in succession:
# a Vite left running for two WEEKS from a different checkout, and then a
# Next.js dev server belonging to an unrelated project. Both answered. Both were
# forwarded to the headset. What the operator saw was somebody else's website,
# with no error anywhere naming the cause -- and a login form no account could
# ever satisfy, because it was not our login form.
#
# This script already refuses to forward to a DEAD port, on the stated grounds
# that the failure would otherwise surface later, inside the headset. A forward
# to the WRONG LIVE port breaks the same promise and costs more to diagnose,
# because every check reports healthy.
#
# The owner's working directory is the cheapest reliable signal: a dev server
# for this repo runs inside this repo. Only meaningful for the servers that run
# HERE -- 8767 and the camera port are ssh tunnels, where the local owner is ssh
# by design.
port_owner_pid() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }
port_owner_cwd() { lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }
port_owner_cmd() { ps -o command= -p "$1" 2>/dev/null | cut -c1-90; }

# THE CAMERA PORT IS READ, NOT HARDCODED, AND THAT IS THE WHOLE POINT.
#
# It was hardcoded to 8081, and on 2026-08-27 an operator wearing the headset
# reported "i cannot see the camara" while everything else worked. Nothing was
# broken. The feed had MOVED: `apps/bridge` now serves it on 8001 (see the
# "WHY NOT PORT 8081" note in `mcp_server.py` — 8081 belongs to the vision
# container, which is dead in exactly the case this feed exists for), and
# `PUBLIC_ROBOT_CAM_URL` was updated to match. This list was not.
#
# So the console asked the headset's own `127.0.0.1:8001`, where nothing is
# listening, the <img> errored, and `CameraLayer.draw` returned before drawing
# anything — silently, because a camera with no frame is indistinguishable
# from a camera that has not connected yet. Everything else reaches the bridge
# through apps/back on 3000, which IS forwarded, which is exactly why movement
# worked and only the picture was missing.
#
# Two places had to agree and one of them moved. Reading the port the console
# will actually ask for means they cannot disagree again.
_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cam_port=""
cam_from=""
# `.env` first, `.env.example` as the fallback: `.env` is gitignored, so a
# fresh clone has none, and silently forwarding nothing would reproduce the
# very symptom this reads the file to prevent. Forwarding the documented
# default instead is safe — if it is the wrong port, the listening check below
# says so out loud, which is the whole contract of this script.
for _env in "$_repo/apps/web/.env" "$_repo/apps/web/.env.example"; do
    [ -f "$_env" ] || continue
    cam_url=$(sed -n 's/^[[:space:]]*PUBLIC_ROBOT_CAM_URL[[:space:]]*=[[:space:]]*//p' \
        "$_env" | tail -n 1 | tr -d '"'\''[:space:]')
    # host:port from the URL, and only if the port is digits. No port means
    # the scheme default, which nothing here tunnels — leave it empty and say
    # so rather than forwarding a guess.
    cam_port=$(printf '%s' "$cam_url" | sed -n 's#^[a-zA-Z]*://[^/:]*:\([0-9][0-9]*\).*#\1#p')
    if [ -n "$cam_port" ]; then cam_from="${_env##*/}"; break; fi
done

# port:what-it-is:fatal
PORTS=(
    "3001:web console (vite):yes"
    "3000:apps/back API:yes"
    "8767:teleop stream (tunnel to robot):yes"
)
# `.env.example` is the fallback, and running on it means the console was
# started without a real `.env` — worth seeing before putting the headset on.
if [ "$cam_from" = ".env.example" ]; then
    warn "camera URL read from apps/web/.env.example — apps/web/.env is missing"
fi
if [ "$cam_port" = "3000" ]; then
    # THE NORMAL CASE: the camera comes through apps/back, behind Better Auth
    # (`apps/back/src/routes/camera.ts`), on the port already forwarded above.
    # Nothing extra goes to the headset — in particular not the bridge's 8001,
    # which also serves `/mcp`, the unauthenticated tool surface that can walk
    # the robot. Forwarding that into the Quest's browser is what this used to
    # do, protected only by a test keeping CORS off `/mcp`.
    :
elif [ -n "$cam_port" ]; then
    # A DEBUG SETUP pointing the console straight at the bridge (8001) or the
    # vision container (8081). Still forwarded so it works, but said out loud:
    # 8001 carries `/mcp` into the headset along with the picture.
    warn "PUBLIC_ROBOT_CAM_URL bypasses apps/back (port $cam_port)"
    if [ "$cam_port" = "8001" ]; then
        warn "  that forward also exposes the bridge's /mcp to the headset browser"
    fi
    PORTS+=("$cam_port:camera MJPEG, direct (debug):no")
else
    warn "no port in PUBLIC_ROBOT_CAM_URL — the headset will have no picture"
fi

say "1. adb"
if ! command -v adb >/dev/null 2>&1; then
    err "adb is not installed."
    echo "     brew install --cask android-platform-tools"
    echo "   Then put the Quest in developer mode (Meta Horizon app -> Headset"
    echo "   Settings -> Developer Mode), plug in USB, and accept the 'Allow USB"
    echo "   debugging?' prompt INSIDE the headset — it is easy to miss."
    exit 1
fi
ok "$(adb version | head -1)"

say "2. Headset"
devices="$(adb devices | awk 'NR>1 && $2=="device" {print $1}')"
if [ -z "$devices" ]; then
    err "no authorised device."
    adb devices | sed 's/^/     /'
    echo "   'unauthorized' means the in-headset prompt has not been accepted yet."
    echo "   Nothing there at all means USB, cable, or developer mode."
    exit 1
fi
for d in $devices; do ok "device $d"; done

say "3. Ports on this machine"
fatal=0
for entry in "${PORTS[@]}"; do
    port="${entry%%:*}"; rest="${entry#*:}"
    label="${rest%:*}"; required="${rest##*:}"
    # Both stacks. Vite binds `localhost`, which on macOS resolves to ::1 and
    # NOT 127.0.0.1 — so an IPv4-only check reports a perfectly healthy dev
    # server as down, and refuses to forward to it. Found while setting up the
    # first real headset session.
    # `nc -z` is enough for the two LOCAL servers, and actively misleading for
    # the two TUNNELLED ones: `ssh -L` binds its local listener at setup time,
    # before it knows anything about the far end, so a connect() succeeds
    # whenever the ssh process is alive — whatever is running on the robot.
    # That is exactly the "forward to a dead port" this script's header
    # promises to prevent, and it was committing it. Forcing a byte through
    # the channel is what tells them apart.
    listening=0
    # The camera port comes from PUBLIC_ROBOT_CAM_URL and is tunnelled like
    # 8767, so it needs the forced-byte probe rather than `nc -z`. Naming it
    # by variable keeps this branch correct when the feed moves again, which
    # is precisely what caught us out when it moved from 8081 to 8001.
    # 3000 is apps/back, local — when the camera rides it, `nc -z` is right.
    tunnelled_cam="${cam_port:-__none__}"
    [ "$tunnelled_cam" = "3000" ] && tunnelled_cam="__none__"
    case "$port" in
        8767|"$tunnelled_cam")
            # Capture first, match second. Under pipefail (the `set -euo
            # pipefail` line at the top) a `curl | grep` pipeline reports
            # CURL's status, not grep's — and the curl that proves the tunnel
            # is empty exits 52/56 by definition. So
            # the match was computed correctly and then thrown away, the `elif`
            # ran, and `nc -z` awarded the dead port a green tick. That is the
            # failure this script's header promises to prevent, committed again
            # by the fix for it. Found with a real headset on 2026-08-21.
            probe=$(curl -sS --max-time 4 -o /dev/null "http://127.0.0.1:$port/" 2>&1 || true)
            case "$probe" in
                *"reset by peer"*|*"Empty reply"*|*"Recv failure"*)
                    listening=0   # tunnel up, nothing behind it on the robot
                    tunnel_empty=1
                    ;;
                *)
                    if nc -z 127.0.0.1 "$port" 2>/dev/null; then
                        listening=1
                    fi
                    ;;
            esac
            ;;
        *)
            if nc -z 127.0.0.1 "$port" 2>/dev/null || nc -z ::1 "$port" 2>/dev/null; then
                listening=1
            fi
            ;;
    esac
    # Ownership, for the servers that run on this machine. See the note beside
    # `port_owner_pid`. A stranger holding the port is a HARD failure: forwarding
    # it hands the headset an app we did not build, and every other check in this
    # script will still say green.
    wrong_owner=0
    if [ "$listening" = "1" ]; then
        case "$port" in
            3000|3001)
                _pid="$(port_owner_pid "$port")"
                _cwd=""
                [ -n "$_pid" ] && _cwd="$(port_owner_cwd "$_pid")"
                case "$_cwd" in
                    "$_repo"|"$_repo"/*)
                        : ;;                       # ours
                    "")
                        # lsof gave nothing (permissions, or a process that just
                        # exited). Not proof of a stranger, so do not fail on it.
                        warn "$port  $label — listening, but the owner could not be identified"
                        ;;
                    *)
                        wrong_owner=1 ;;
                esac
                ;;
        esac
    fi

    if [ "$wrong_owner" = "1" ]; then
        err "$port  $label — held by something OUTSIDE this checkout"
        echo "     pid $_pid  cwd: $_cwd"
        echo "     $(port_owner_cmd "$_pid")"
        echo "     this repo: $_repo"
        echo "     Forwarding it would hand the headset that app instead of the"
        echo "     console — which is exactly what happened on 2026-09-08."
        echo "     Stop it, or run the dev server from this checkout:"
        echo "       kill $_pid"
        fatal=1
    elif [ "$listening" = "1" ]; then
        ok "$port  $label"
    elif [ "$required" = "yes" ]; then
        if [ "${tunnel_empty:-0}" = "1" ]; then
            err "$port  $label — forwarded, but nothing is running on the robot"
            tunnel_empty=0
        else
            err "$port  $label — NOT listening"
        fi
        fatal=1
    else
        if [ "${tunnel_empty:-0}" = "1" ]; then
            warn "$port  $label — forwarded, but nothing running on the robot (no picture)"
            tunnel_empty=0
        else
            warn "$port  $label — not listening (optional; no camera picture)"
        fi
    fi
done

# THE BRIDGE, AS APPS/BACK SEES IT. Not forwarded to the headset — nothing in
# the headset talks to it any more — but everything the headset does goes
# through back to it: the camera, walking, gestures, and PARAR. When the camera
# rode 8001 this was checked as a side effect of checking the camera; now that
# the camera rides 3000, a dead Mac->robot tunnel would pass every check above
# and surface as a black view and an e-stop that reaches nothing. Any HTTP
# answer means the bridge is alive; "000" is no connection, or a tunnel with
# nothing behind it.
bridge_url="${BRIDGE_URL:-}"
if [ -z "$bridge_url" ] && [ -f "$_repo/apps/back/.env" ]; then
    bridge_url=$(sed -n 's/^[[:space:]]*BRIDGE_URL[[:space:]]*=[[:space:]]*//p' \
        "$_repo/apps/back/.env" | tail -n 1 | tr -d '"'\''[:space:]')
fi
# scheme://host:port only — the host matters as much as the port: back may
# dial the robot's LAN address directly rather than a local tunnel.
bridge_base=$(printf '%s' "$bridge_url" | sed -n 's#^\([a-zA-Z]*://[^/]*\).*#\1#p')
# Same default as apps/back/src/bridge/url.ts — 8001, the robot's port.
bridge_base="${bridge_base:-http://127.0.0.1:8001}"
bridge_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 \
    "$bridge_base/camera/status" 2>/dev/null || true)
if [ -n "$bridge_code" ] && [ "$bridge_code" != "000" ]; then
    ok "bridge at $bridge_base, as apps/back reaches it (HTTP $bridge_code)"
else
    err "bridge at $bridge_base — apps/back cannot reach it (tunnel/network down, or bridge not running)"
    err "      camera, walking and PARAR all go through it"
    fatal=1
fi

if [ "$fatal" = "1" ]; then
    echo
    err "start what is missing before forwarding — a forward to a dead port"
    err "succeeds now and fails later, in the headset."
    echo "     bun run dev                                  # 3000 + 3001"
    echo "     ssh -N -o ControlMaster=no \\"
    echo "         -L 8001:127.0.0.1:8001 \\"
    echo "         -L 8767:127.0.0.1:8767 c3po              # bridge (for back) + teleop"
    echo
    echo "     the camera reaches the headset through apps/back on 3000;"
    echo "     back reaches the bridge over the 8001 tunnel."
    exit 1
fi

say "4. Forwarding"
for entry in "${PORTS[@]}"; do
    port="${entry%%:*}"
    if adb reverse "tcp:$port" "tcp:$port" >/dev/null 2>&1; then
        ok "quest localhost:$port -> this machine :$port"
    else
        err "adb reverse failed for $port"
        exit 1
    fi
done

say "Ready"
cat <<'DONE'
  In the Quest browser, open:

      http://localhost:3001/vr-control

  It MUST be localhost, not an IP — that is the whole point (secure context).

  Before putting the headset on, confirm the yaw sign is not inverted:
      cd apps/bridge && uv run python scripts/teleop_smoke_test.py --yaw-only

  Undo the forwards with:  adb reverse --remove-all
DONE
