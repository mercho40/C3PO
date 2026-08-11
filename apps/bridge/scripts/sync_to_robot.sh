#!/usr/bin/env bash
# Syncs apps/bridge/src to the G1's onboard Jetson (default 10.4.67.47) and
# restarts the bridge server there (SIM_MODE=real, from apps/bridge/.env).
#
# Usage: ROBOT_PASSWORD=... ./scripts/sync_to_robot.sh [robot_host]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROBOT_HOST="${1:-10.4.67.47}"
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
