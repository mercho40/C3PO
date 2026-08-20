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
# NOT YET TESTED AGAINST AN ACTUAL QUEST. The reasoning above is sound and the
# checks below are real, but nobody has run this with a headset plugged in.

set -euo pipefail

_bold=$'\033[1m'; _red=$'\033[31m'; _green=$'\033[32m'; _yellow=$'\033[33m'; _reset=$'\033[0m'
ok()   { printf '  %s✓%s %s\n' "$_green" "$_reset" "$1"; }
warn() { printf '  %s!%s %s\n' "$_yellow" "$_reset" "$1"; }
err()  { printf '  %s✗%s %s\n' "$_red" "$_reset" "$1" >&2; }
say()  { printf '\n%s%s%s\n' "$_bold" "$1" "$_reset"; }

# port:what-it-is:fatal
PORTS=(
    "3001:web console (vite):yes"
    "3000:apps/back API:yes"
    "8767:teleop stream (tunnel to robot):yes"
    "8081:camera MJPEG (tunnel to robot):no"
)

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
    if nc -z 127.0.0.1 "$port" 2>/dev/null; then
        ok "$port  $label"
    elif [ "$required" = "yes" ]; then
        err "$port  $label — NOT listening"
        fatal=1
    else
        warn "$port  $label — not listening (optional; no camera picture)"
    fi
done

if [ "$fatal" = "1" ]; then
    echo
    err "start what is missing before forwarding — a forward to a dead port"
    err "succeeds now and fails later, in the headset."
    echo "     bun run dev                                  # 3000 + 3001"
    echo "     ssh -N -o ControlMaster=no \\"
    echo "         -L 8001:127.0.0.1:8001 \\"
    echo "         -L 8081:127.0.0.1:8081 \\"
    echo "         -L 8767:127.0.0.1:8767 c3po              # 8767 + 8081"
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
