#!/usr/bin/env bash
# Syncs apps/bridge/src to the G1's onboard Jetson and restarts the bridge
# there (SIM_MODE=real, from apps/bridge/.env).
#
# ⚠️ SUPERSEDED by `scripts/robot/run_c3po`, and it bypasses every interlock
# that script exists to enforce: it does not stop the colleague's `gemm` stack,
# does not check for a running `cmd_vel_to_loco`, writes no pidfile, and does
# not run `uv sync` + `postsync.sh`. A bridge started this way is invisible to
# `stop_c3po` — which is exactly the untracked-commander case `run_c3po` now
# refuses to start alongside. It also pins BRIDGE_* *after* sourcing `.env`,
# so those values win over `.env`, the opposite of `run_c3po`'s precedence.
#
# Prefer: `git pull && stop_c3po && run_c3po` on the robot. Reach for this only
# to push uncommitted work for a quick loop, and run `stop_c3po` afterwards.
#
# Usage: ROBOT_PASSWORD=... ./scripts/sync_to_robot.sh [robot_host]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# mDNS name, not an IP: the Jetson is on DHCP and its lease has already moved
# twice, and one of the old addresses later answered as a different device.
ROBOT_HOST="${1:-g1-orin.local}"
ROBOT_USER="unitree"

: "${ROBOT_PASSWORD:?set ROBOT_PASSWORD in the environment — not committed to git}"
export SSHPASS="$ROBOT_PASSWORD"

if ! command -v sshpass >/dev/null; then
    echo "sync_to_robot: needs sshpass (brew install sshpass)" >&2
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

echo "sync_to_robot: copying apps/bridge/src to $ROBOT_USER@$ROBOT_HOST ..."
sshpass -e rsync -avz -e "sshpass -e ssh $SSH_OPTS" \
    "$BRIDGE_DIR/src/" "$ROBOT_USER@$ROBOT_HOST:~/c3po/apps/bridge/src/"

echo "sync_to_robot: restarting bridge server ..."
# Precise process match — a broad pkill -f 'mcp_server' would also match this
# very ssh invocation's own command text and kill the wrapping shell first.
sshpass -e ssh $SSH_OPTS "$ROBOT_USER@$ROBOT_HOST" \
    "pkill -f '.venv/bin/python3 -m bridge.mcp_server' || true"
sleep 1
sshpass -e ssh $SSH_OPTS "$ROBOT_USER@$ROBOT_HOST" \
    "cd ~/c3po/apps/bridge && nohup env \$(grep -v '^#' .env | grep -v '^\$' | xargs) \
     BRIDGE_TRANSPORT=http BRIDGE_HOST=127.0.0.1 BRIDGE_PORT=8001 \
     ~/.local/bin/uv run python -m bridge.mcp_server > /tmp/c3po-bridge.log 2>&1 < /dev/null & disown" &
wait
sleep 4

echo "sync_to_robot: done. Confirm boot with:"
echo "  sshpass -e ssh $SSH_OPTS $ROBOT_USER@$ROBOT_HOST 'tail -20 /tmp/c3po-bridge.log'"
