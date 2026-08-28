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
if [ -n "$cam_port" ]; then
    PORTS+=("$cam_port:camera MJPEG (PUBLIC_ROBOT_CAM_URL in $cam_from):no")
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
    case "$port" in
        8767|"${cam_port:-__none__}")
            # Capture first, match second. Under `set -o pipefail` (line 31) a
            # `curl | grep` pipeline reports CURL's status, not grep's — and the
            # curl that proves the tunnel is empty exits 52/56 by definition. So
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
    if [ "$listening" = "1" ]; then
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

if [ "$fatal" = "1" ]; then
    echo
    err "start what is missing before forwarding — a forward to a dead port"
    err "succeeds now and fails later, in the headset."
    echo "     bun run dev                                  # 3000 + 3001"
    echo "     ssh -N -o ControlMaster=no \\"
    echo "         -L 8001:127.0.0.1:8001 \\"
    echo "         -L 8767:127.0.0.1:8767 c3po              # 8767 + the camera"
    echo
    echo "     the camera rides 8001 with the bridge — PUBLIC_ROBOT_CAM_URL"
    echo "     in apps/web/.env is what decides, and this script follows it."
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
