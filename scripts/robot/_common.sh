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

# --- safety ----------------------------------------------------------------

# The one check worth doing before we command anything: is something else
# already driving the legs? cmd_vel_to_loco is `gemm`'s /cmd_vel -> robot
# bridge. It ships disabled, but a launch arg is all it takes.
other_commander_pids() {
    pgrep -f "cmd_vel_to_loco" 2>/dev/null || true
}

warn_if_other_commander() {
    local pids
    pids="$(other_commander_pids)"
    if [ -n "$pids" ]; then
        err "cmd_vel_to_loco is RUNNING (pid: $(echo "$pids" | tr '\n' ' '))"
        err "Two stacks would be commanding the legs through the same API."
        err "Stop it before driving the robot."
        return 1
    fi
    return 0
}
