#!/usr/bin/env bash
# One Mac command for a robot session: tunnel, internet for the robot, console.
#
#   ./scripts/robot_session.sh [ssh-host]      # default: $C3PO_SSH_HOST, else c3po
#   ./scripts/robot_session.sh --print [host]  # cable verdict + the three commands, then exit
#
# Starts, in the background, logging to ${TMPDIR:-/tmp}/c3po-session/*.log:
#   scripts/robot_tunnel.sh <host>          Mac 8001/8767 -> robot bridge/teleop
#   python3 scripts/robot_internet.py <host> robot 127.0.0.1:18899 -> Mac proxy :8899
#   bun run dev:robot                        apps/back :3000 + apps/web :3001
# then prints a status table and the lines to paste on the robot, and stays up
# until Ctrl-C or until one of the three exits (it says which, and where its log is).
#
# Runs on macOS /bin/bash 3.2: no `wait -n`, no empty arrays under `set -u`.
# Never runs sudo: a wrong cable address is printed with its fix.

set -uo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

print_only=0
if [ "${1:-}" = "--print" ]; then
    print_only=1
    shift
fi
host="${1:-${C3PO_SSH_HOST:-c3po}}"
robot_ip="${C3PO_ROBOT_IP:-192.168.123.164}"
eth="${C3PO_ETH:-en11}"
robot_host="${C3PO_ROBOT_HOST:-localhost}"
tmp="${TMPDIR:-/tmp}"
log_dir="${tmp%/}/c3po-session"

# (1) Is the robot reached over the cable, from a 192.168.123.x address?
# Non-fatal: the ssh alias may go over Wi-Fi instead.
cable_iface="$(route -n get "$robot_ip" 2>/dev/null | awk '$1 == "interface:" { print $2 }')"
cable_addr=""
[ -n "$cable_iface" ] && cable_addr="$(ipconfig getifaddr "$cable_iface" 2>/dev/null)"
cable_ok() { case "$cable_addr" in 192.168.123.*) return 0 ;; *) return 1 ;; esac; }
if cable_ok; then
    cable_verdict="✓ cable: $robot_ip via $cable_iface ($cable_addr)"
else
    # No router, so the cable can never become the default route (OPERATIONS §2).
    cable_verdict="✗ cable: $robot_ip via ${cable_iface:-no interface} (${cable_addr:-no address}), not a 192.168.123.x address
  fix: sudo ipconfig set $eth MANUAL 192.168.123.99 255.255.255.0"
fi

tunnel_cmd="scripts/robot_tunnel.sh $host"
internet_cmd="python3 -u scripts/robot_internet.py $host"
console_cmd="C3PO_ROBOT_HOST=$robot_host bun run dev:robot"

echo "$cable_verdict"
if [ "$print_only" = "1" ]; then
    printf '%s\n' "$tunnel_cmd" "$internet_cmd" "$console_cmd"
    exit 0
fi

# (2) robot_tunnel.sh already refuses a held 8001/8767; these are the other three.
for port in 3000 3001 8899; do
    holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 { print $1 " (pid " $2 ")" }')"
    if [ -n "$holder" ]; then
        echo "✗ 127.0.0.1:$port is already held by $holder — another session, or a dev server." >&2
        echo "  see it: lsof -nP -iTCP:$port -sTCP:LISTEN" >&2
        exit 1
    fi
done

# (3) The three jobs. Without job control they share this script's process group.
cd "$repo" || exit 1
mkdir -p "$log_dir"
# shortcut: `kill 0` signals the whole process group, so it also kills a parent that wraps this script (e.g. `bun run`); give each child its own group if that ever matters.
trap 'trap - EXIT INT TERM; kill 0' EXIT INT TERM
scripts/robot_tunnel.sh "$host" </dev/null >"$log_dir/tunnel.log" 2>&1 &
tunnel_pid=$!
python3 -u scripts/robot_internet.py "$host" </dev/null >"$log_dir/internet.log" 2>&1 &
internet_pid=$!
C3PO_ROBOT_HOST="$robot_host" bun run dev:robot </dev/null >"$log_dir/console.log" 2>&1 &
console_pid=$!
echo "started: tunnel (pid $tunnel_pid), internet (pid $internet_pid), console (pid $console_pid)"
echo "logs:    $log_dir/{tunnel,internet,console}.log"

# (4) Status, once everything has had time to come up.
sleep 8
row() {  # row <name> <detail> <command...>
    local name="$1" detail="$2"
    shift 2
    if "$@" >/dev/null 2>&1; then printf '  ✓ %-7s %s\n' "$name" "$detail"
    else printf '  ✗ %-7s %s\n' "$name" "$detail"; fi
}
proxy_code=""
proxy_ok() {
    lsof -nP -iTCP:8899 -sTCP:LISTEN >/dev/null 2>&1 || return 1
    proxy_code="$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" \
        "curl -s -m5 -x http://127.0.0.1:18899 -o /dev/null -w '%{http_code}' https://generativelanguage.googleapis.com/" 2>/dev/null)"
    [ -n "$proxy_code" ] && [ "$proxy_code" != "000" ]
}
echo
row cable   "${cable_iface:-?} ${cable_addr:-no 192.168.123.x address}" cable_ok
row bridge  "127.0.0.1:8001/telemetry/gate through the tunnel" curl -s -m3 -o /dev/null http://127.0.0.1:8001/telemetry/gate
proxy_ok; proxy_rc=$?
row proxy   "Mac :8899 + robot -> Gemini (HTTP ${proxy_code:-none})" test "$proxy_rc" = 0
row back    "127.0.0.1:3000/health" curl -sf -m3 -o /dev/null http://127.0.0.1:3000/health
# back's /health is static, so ask the model itself (this Mac's internet, as back uses it).
# perl's alarm bounds it: macOS has no `timeout`, and a hung gateway must not hold the table.
row agent   "AGENT_MODEL makes a tool call (bun apps/back/scripts/smoke-agent.ts)" \
    perl -e 'alarm shift; exec @ARGV' 60 bun apps/back/scripts/smoke-agent.ts
web_up() {  # vite's first start takes longer than the 8 s above
    for _ in $(seq 1 15); do curl -s -m3 -o /dev/null http://127.0.0.1:3001 && return 0; sleep 2; done
    return 1
}
row web     "127.0.0.1:3001" web_up
cable_ok || printf '%s\n' "$cable_verdict" | sed -n '2p'
cat <<EOF

console: http://localhost:3001   (localhost, not 127.0.0.1: CORS)

on the robot (ssh $host), paste:
  export http_proxy=http://127.0.0.1:18899 https_proxy=http://127.0.0.1:18899 no_proxy=localhost,127.0.0.1,::1
  c3po up

Ctrl-C stops all three.
EOF

# `kill -0 a b c` succeeds if ANY is alive, so test each one.
while kill -0 "$tunnel_pid" 2>/dev/null && kill -0 "$internet_pid" 2>/dev/null &&
    kill -0 "$console_pid" 2>/dev/null; do
    sleep 2
done
exited() {  # exited <name> <pid>
    kill -0 "$2" 2>/dev/null && return
    echo "✗ $1 exited — $log_dir/$1.log ends:" >&2
    tail -n 15 "$log_dir/$1.log" >&2
}
exited tunnel "$tunnel_pid"
exited internet "$internet_pid"
exited console "$console_pid"
echo "stopping the rest" >&2
exit 1
