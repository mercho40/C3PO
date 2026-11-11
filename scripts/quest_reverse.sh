#!/usr/bin/env bash
# Forward the Quest's localhost to this machine over USB, nothing else.
#
#   bash scripts/quest_reverse.sh
#
# The quick version of quest_setup.sh (which also checks every port is ours
# and alive): 3001 the console, apps/back's PORT (apps/back/.env, default
# 3000; BACK_PORT overrides), 8001 the bridge's camera (dev:robot's default
# PUBLIC_ROBOT_CAM_URL) and 8767 teleop. localhost keeps WebXR in a secure
# context. Undo with: adb reverse --remove-all

set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

back_port="${BACK_PORT:-}"
if [ -z "$back_port" ] && [ -f "$repo/apps/back/.env" ]; then
    back_port=$(sed -n 's/^[[:space:]]*PORT[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
        "$repo/apps/back/.env" | tail -n 1)
fi
back_port="${back_port:-3000}"

if ! command -v adb >/dev/null 2>&1; then
    echo "✗ adb is not installed: brew install --cask android-platform-tools" >&2
    exit 1
fi

if [ -z "$(adb devices | awk 'NR>1 && $2=="device" {print $1}')" ]; then
    echo "✗ no Quest attached (or USB debugging not accepted inside the headset):" >&2
    adb devices | sed 's/^/    /' >&2
    exit 1
fi

for port in 3001 "$back_port" 8001 8767; do
    if ! adb reverse "tcp:$port" "tcp:$port" >/dev/null; then
        echo "✗ adb reverse failed for $port" >&2
        exit 1
    fi
    echo "✓ quest localhost:$port -> this machine :$port"
done

cat <<'DONE'

In the Quest browser open (localhost, not an IP):
    http://localhost:3001/login
    http://localhost:3001/vr-control
DONE
