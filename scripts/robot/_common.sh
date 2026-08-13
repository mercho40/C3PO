#!/usr/bin/env bash
# Shared helpers for the robot stack controls (run_c3po / stop_c3po /
# run_gemm / stop_gemm). Sourced, never executed directly.
#
# Why these exist: the G1 carries two independent stacks — ours and a
# colleague's `gemm` workspace — and some of what they need cannot be shared.
# `realsense2_camera_node` holds /dev/video0-5 and a V4L2 device has exactly
# one owner; the Livox driver binds UDP 56100-56500 and the Mid-360 unicasts to
# one host. So "run both at once" is not a policy we chose against, it's a
# thing the kernel will not do. These scripts make that explicit: starting
# either stack stops the other first, loudly.
#
# The dangerous overlap is not the sensors, though — it's the robot's control
# API. `gemm`'s cmd_vel_to_loco and our bridge both command motion through
# /api/sport/request api_id 7105, and that cannot be isolated by DDS domain
# because both stacks must sit on domain 0 to reach the control board at all.
# One commander at a time is the invariant everything here protects.

set -euo pipefail

C3PO_DIR="${C3PO_DIR:-$HOME/c3po}"
BRIDGE_DIR="$C3PO_DIR/apps/bridge"
RUN_DIR="${C3PO_RUN_DIR:-$HOME/.c3po/run}"
LOG_DIR="${C3PO_LOG_DIR:-$HOME/.c3po/logs}"
BRIDGE_PID="$RUN_DIR/bridge.pid"
BRIDGE_LOG="$LOG_DIR/bridge.log"

# Containers belonging to the colleague's stack. Matched by prefix so a new
# `gemm-*` container is picked up without editing this script.
GEMM_PREFIX="${GEMM_PREFIX:-gemm}"

# --- output ----------------------------------------------------------------

if [ -t 1 ]; then
    _bold=$'\033[1m'; _dim=$'\033[2m'; _red=$'\033[31m'
    _green=$'\033[32m'; _yellow=$'\033[33m'; _reset=$'\033[0m'
else
    _bold=''; _dim=''; _red=''; _green=''; _yellow=''; _reset=''
fi

say()  { printf '%s%s%s\n' "$_bold" "$*" "$_reset"; }
ok()   { printf '  %s✓%s %s\n' "$_green" "$_reset" "$*"; }
warn() { printf '  %s!%s %s\n' "$_yellow" "$_reset" "$*"; }
err()  { printf '  %s✗%s %s\n' "$_red" "$_reset" "$*" >&2; }
info() { printf '  %s%s%s\n' "$_dim" "$*" "$_reset"; }

# --- docker ----------------------------------------------------------------

# The `unitree` user may or may not be in the docker group. Work either way
# rather than hardcoding sudo, which would prompt needlessly when it isn't
# required.
_docker() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
    else
        sudo docker "$@"
    fi
}

gemm_containers() {
    _docker ps -a --filter "name=^${GEMM_PREFIX}" --format '{{.Names}}' 2>/dev/null || true
}

gemm_running() {
    _docker ps --filter "name=^${GEMM_PREFIX}" --format '{{.Names}}' 2>/dev/null || true
}

# --- bridge ----------------------------------------------------------------

bridge_pid() {
    [ -f "$BRIDGE_PID" ] || return 1
    local pid
    pid="$(cat "$BRIDGE_PID" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    # A stale pidfile after an unclean shutdown must not read as "running".
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s' "$pid"
}

bridge_running() { bridge_pid >/dev/null 2>&1; }

# `uv run` execs nothing — it forks the interpreter as a child and waits. So
# the pid we record is uv's, and the bridge itself is one level down. SIGTERM
# propagates, but a SIGKILL aimed at the recorded pid alone would reap uv and
# leave the bridge orphaned: still holding DDS, still able to command the legs,
# and now invisible to `bridge_running`. Always signal the whole tree.
descendant_pids() {
    local parent="$1" child
    for child in $(pgrep -P "$parent" 2>/dev/null || true); do
        descendant_pids "$child"
        printf '%s\n' "$child"
    done
}

bridge_tree_pids() {
    local pid="$1"
    descendant_pids "$pid"
    printf '%s\n' "$pid"
}

# Any live bridge at all. Only ask this once you believe ours is stopped —
# both callers do — in which case whatever comes back is an orphan from an
# unclean stop or a second copy started by hand. Either one means something
# can command the legs that our pidfile cannot stop.
stray_bridge_pids() {
    pgrep -f 'bridge\.mcp_server' 2>/dev/null || true
}

# --- safety ----------------------------------------------------------------

# Everything on this machine that can command the robot. Checking only for
# `cmd_vel_to_loco` was not enough: on 2026-08-14 a full `xr_teleoperate` stack
# was found driving the arms over `rt/arm_sdk` and `rt/lowcmd`, wrapping
# `LocoClient.Move()`, and holding a hand through `brainco_hand_server` — and
# every one of our checks passed silently while it ran.
#
# The firmware arbitrates nothing here. Every vendor client is constructed
# `enableLease=false`, so whoever publishes to the request topic is obeyed;
# there is no lock to lose and no error to observe. The one-commander invariant
# is entirely ours to enforce, which is why this list has to be maintained by
# hand as we discover new ways to drive this robot.
#
# Override for a stack we haven't met yet:
#   OTHER_COMMANDER_PATTERNS='cmd_vel_to_loco|my_new_thing' run_c3po
OTHER_COMMANDER_PATTERNS="${OTHER_COMMANDER_PATTERNS:-cmd_vel_to_loco|xr_teleoperate|brainco_hand_server}"

# `pgrep -f` matches whole command lines, so it will happily match the shell
# that is asking the question — `ssh robot 'pgrep -f cmd_vel_to_loco'` reports
# itself. That false positive is not hypothetical; it cost real debugging time.
# Filter out this process and everything that spawned it.
_is_self_or_ancestor() {
    local target="$1" cur=$$
    while [ -n "$cur" ] && [ "$cur" -gt 1 ] 2>/dev/null; do
        [ "$cur" = "$target" ] && return 0
        cur="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ')"
    done
    return 1
}

other_commander_pids() {
    local pid
    for pid in $(pgrep -f "$OTHER_COMMANDER_PATTERNS" 2>/dev/null || true); do
        _is_self_or_ancestor "$pid" && continue
        printf '%s\n' "$pid"
    done
}

warn_if_other_commander() {
    local pids pid
    pids="$(other_commander_pids)"
    [ -n "$pids" ] || return 0

    err "something else can already command this robot:"
    for pid in $pids; do
        # Name the process, not just the pid — "xr_teleoperate" and
        # "cmd_vel_to_loco" call for completely different conversations with
        # completely different people.
        err "  pid $pid  $(ps -o args= -p "$pid" 2>/dev/null | cut -c1-90)"
    done
    err "Two commanders on one robot is the thing these scripts exist to prevent."
    err "Stop it before driving, or set OTHER_COMMANDER_PATTERNS if this is a false match."
    return 1
}
