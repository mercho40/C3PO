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

# The VR teleop stream. Not managed by run_c3po or the boot unit: it exists to
# serve a person who is currently wearing a headset, so it is per-session,
# started by hand, and stopped when that person takes it off.
TELEOP_PID="$RUN_DIR/teleop.pid"
TELEOP_LOG="$LOG_DIR/teleop.log"

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

# --- perception ------------------------------------------------------------

PERCEPTION_NAV_IMAGE="${PERCEPTION_NAV_IMAGE:-c3po/perception-nav:humble}"
PERCEPTION_VISION_IMAGE="${PERCEPTION_VISION_IMAGE:-c3po/perception-vision:r35.3.1}"
PERCEPTION_LOG_DIR="${PERCEPTION_LOG_DIR:-$HOME/.c3po/logs/perception}"

# Prefix filter, because there are TWO containers. run_c3po and stop_c3po used
# to DETECT with a prefix and ACT on an exact name — detection passed, the start
# failed, and run_c3po printed "ok started c3po-perception" either way.
perception_containers() {
    _docker ps -a --filter "name=^c3po-perception" --format '{{.Names}}' 2>/dev/null || true
}

perception_running_containers() {
    _docker ps --filter "name=^c3po-perception" --format '{{.Names}}' 2>/dev/null || true
}

perception_running() { [ -n "$(perception_running_containers)" ]; }

# Which bring-up stage is live. `fake` is the only one holding no sensors, which
# is the distinction run_gemm and the operator actually care about.
perception_stage() {
    _docker inspect c3po-perception-nav \
        --format '{{index .Config.Labels "c3po.stage"}}' 2>/dev/null || true
}

perception_holds_sensors() {
    perception_running || return 1
    [ "$(perception_stage)" != "fake" ]
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

# Every live bridge, whoever started it. `bridge_pid` only knows the one named
# in the pidfile, and on 2026-08-20 that was exactly the problem: a bridge was
# running and serving while the pidfile named a different, dead process.
running_bridge_pids() {
    pgrep -f 'bridge\.mcp_server' 2>/dev/null || true
}

# Reconcile the pidfile with reality, and say so when they disagree.
#
# `run_c3po` and the systemd unit BOTH start a bridge and BOTH write this
# pidfile, with no awareness of each other. Run one by hand while the unit owns
# the service and they race: the manual instance cannot bind 8001 and dies, but
# not before overwriting the pidfile with its own pid. systemd is Type=forking
# with PIDFile=, so it then waits for a process that no longer exists and sits
# in `activating` until TimeoutStartSec — while `bridge_running` reports false
# and `run_teleop` refuses to start, on the grounds that there is no e-stop.
#
# There IS an e-stop. That is what makes this worth fixing rather than
# documenting: a bookkeeping error that presents as a safety refusal teaches
# operators to bypass safety refusals.
#
# Returns 0 if a usable bridge exists (correcting the pidfile if needed).
reconcile_bridge_pidfile() {
    bridge_pid >/dev/null 2>&1 && return 0

    local live
    live="$(running_bridge_pids | head -1)"
    [ -n "$live" ] || return 1

    warn "the pidfile names a dead process, but a bridge IS running (pid $live)."
    warn "that happens when run_c3po and the systemd unit race for this file."
    mkdir -p "$(dirname "$BRIDGE_PID")"
    printf '%s' "$live" > "$BRIDGE_PID"
    ok "pidfile corrected to $live"
    return 0
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

# Same shape as bridge_pid, for a sidecar named by its pidfile.
sidecar_pid() {
    local pidfile="$1" pid
    [ -f "$pidfile" ] || return 1
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s' "$pid"
}

stray_teleop_pids() {
    pgrep -f 'bridge\.teleop\.server' 2>/dev/null || true
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
# `unitree_slam` earns its place for a non-obvious reason: its 1102 pose
# navigation closes its own velocity loop, so it is a locomotion commander even
# though nothing in its name says so (`docs/ROBOT-HARDWARE.md`).
OTHER_COMMANDER_PATTERNS="${OTHER_COMMANDER_PATTERNS:-cmd_vel_to_loco|xr_teleoperate|brainco_hand_server|unitree_slam}"

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
