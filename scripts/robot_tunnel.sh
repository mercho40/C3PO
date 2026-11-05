#!/usr/bin/env bash
# Keep the Mac's tunnel to the robot up, and say whether the robot answers.
#
#   ./scripts/robot_tunnel.sh [ssh-host]      # default: $C3PO_SSH_HOST, else c3po
#   ./scripts/robot_tunnel.sh --print [host]  # print the ssh command and exit
#
# Forwards this machine's 127.0.0.1:8001 (bridge: /mcp, /camera, /telemetry) and
# 127.0.0.1:8767 (teleop stream) to the robot's loopback, where both listen.
#
# WHY A LOOP. On 2026-10-15 the tunnel dropped twice in one session: the Mac
# auto-joined another Wi-Fi, then the robot rebooted. Each time the console
# just went offline until somebody noticed and retyped the command. This
# reconnects on its own, two seconds after any exit, until Ctrl-C.
#
# WHY ExitOnForwardFailure. Without it ssh stays connected when a local port is
# already taken (a tunnel left running in another terminal), forwarding nothing
# while looking healthy. With it, that is an immediate, named failure.
#
# "channel open failed" in the output means the tunnel is up but nothing on the
# robot listens on that port: the bridge or teleop is not running there.

set -uo pipefail

print_only=0
if [ "${1:-}" = "--print" ]; then
    print_only=1
    shift
fi
host="${1:-${C3PO_SSH_HOST:-c3po}}"

ssh_cmd=(
    ssh -N
    -o ControlMaster=no
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=5
    -o ServerAliveCountMax=2
    -o ConnectTimeout=5
    -L 8001:127.0.0.1:8001
    -L 8767:127.0.0.1:8767
    "$host"
)

if [ "$print_only" = "1" ]; then
    printf '%s\n' "${ssh_cmd[*]}"
    exit 0
fi

for port in 8001 8767; do
    holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 { print $1 " (pid " $2 ")" }')"
    if [ -n "$holder" ]; then
        echo "✗ 127.0.0.1:$port is already held by $holder — another tunnel, or a local bridge." >&2
        echo "  see it: lsof -nP -iTCP:$port -sTCP:LISTEN" >&2
        exit 1
    fi
done

trap 'echo; echo "tunnel stopped"; exit 0' INT TERM

# Once the tunnel is up, say what is behind it: a tunnel to a robot whose
# bridge is down looks exactly like a working one until the console polls.
report() {
    sleep 4
    # ssh binds the local end only once connected, so no listener = no tunnel.
    if ! lsof -nP -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1; then
        echo "$(date +%H:%M:%S) … not connected yet (robot off, or not on its network?)"
    elif curl -s -m 3 -o /dev/null http://127.0.0.1:8001/telemetry/gate; then
        echo "$(date +%H:%M:%S) ✓ tunnel up — the bridge answers on 127.0.0.1:8001"
    else
        echo "$(date +%H:%M:%S) ✗ tunnel up, but the bridge does not answer — is it running on the robot? (c3po status)"
    fi
}

while true; do
    echo "$(date +%H:%M:%S) connecting to $host (8001, 8767)"
    report &
    reporter=$!
    "${ssh_cmd[@]}" 2>&1 | grep -v -i "post-quantum\|store now, decrypt later\|upgraded\|openssh.com/pq"
    kill "$reporter" 2>/dev/null
    echo "$(date +%H:%M:%S) tunnel down — retrying in 2 s (Ctrl-C to stop)"
    sleep 2
done
