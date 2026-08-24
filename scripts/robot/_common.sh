#!/usr/bin/env bash
# shellcheck disable=SC2034 # exported-by-sourcing constants are used by callers
# Shared sensor, process and safety helpers behind the `c3po` operator CLI.
# Sourced by narrow implementation scripts; never executed directly.
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
BRIDGE_BIND_HOST="${C3PO_BRIDGE_BIND_HOST:-0.0.0.0}"

# The VR teleop stream. Not managed by the bridge unit: it exists to
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

# Whether we are holding a DEVICE somebody else could want. This is what
# run_gemm and run_c3po report, so being wrong here sends people looking for a
# contention that is not happening.
#
# "Any perception container is running and the stage is not fake" was the old
# rule and it is now wrong twice over. The LiDAR stopped being a device claim
# when odometry.launch.py moved to lidar_source:=republish — an ordinary
# multi-consumer topic — and `stt` opens nothing at all, yet ran the nav-label
# lookup, found no nav container, compared "" against "fake" and concluded we
# held both sensors while transcribing speech on the GPU.
#
# The camera is the only device claim left, and the vision container is the one
# thing that opens it. C3PO_LIDAR_SOURCE=driver is the documented exception and
# is not detected here; a run that sets it knows it took the Livox.
perception_holds_sensors() {
    [ -n "$(_docker ps --filter 'name=^c3po-perception-vision' --format '{{.Names}}' 2>/dev/null)" ]
}

# --- bridge ----------------------------------------------------------------
# systemd is the sole lifecycle owner. Process discovery remains only to detect
# a bridge somebody launched by hand outside the unit.

bridge_running() {
    systemctl is-active --quiet c3po-bridge.service 2>/dev/null
}

bridge_main_pid() {
    local pid
    pid="$(systemctl show --property=MainPID --value c3po-bridge.service 2>/dev/null || true)"
    [ -n "$pid" ] && [ "$pid" != "0" ] || return 1
    printf '%s' "$pid"
}

bridge_process_pids() {
    pgrep -f 'bridge\.mcp_server' 2>/dev/null || true
}

# Prove that a TCP listener belongs to a specific process, rather than trusting
# an HTTP response from whatever happened to win the port. Match the configured
# IPv4 bind exactly. The deployed service deliberately uses 0.0.0.0 for direct
# LAN access; tests pass loopback explicitly when exercising that case.
# Linux exposes the socket inode without root through /proc/net/tcp and the same
# inode through /proc/<pid>/fd. C3PO_PROC_ROOT makes the parser testable.
process_listens_ipv4_port() {
    local pid="$1" port="$2" host="${3:-$BRIDGE_BIND_HOST}" proc_root="${C3PO_PROC_ROOT:-/proc}"
    local address_hex local_address listeners fd target inode listener

    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    case "$port" in ''|*[!0-9]*) return 1 ;; esac
    case "$host" in
        0.0.0.0)   address_hex="00000000" ;;
        127.0.0.1) address_hex="0100007F" ;;
        *) return 1 ;;
    esac
    [ -d "$proc_root/$pid/fd" ] || return 1
    local_address="$address_hex:$(printf '%04X' "$port")"
    listeners="$(awk -v local_address="$local_address" \
        '$4 == "0A" && toupper($2) == local_address { print $10 }' \
        "$proc_root/net/tcp" 2>/dev/null || true)"
    [ -n "$listeners" ] || return 1

    for fd in "$proc_root/$pid/fd"/*; do
        [ -e "$fd" ] || [ -L "$fd" ] || continue
        target="$(readlink "$fd" 2>/dev/null || true)"
        case "$target" in
            socket:\[*\])
                inode="${target#socket:\[}"
                inode="${inode%\]}"
                for listener in $listeners; do
                    [ "$inode" = "$listener" ] && return 0
                done
                ;;
        esac
    done
    return 1
}

# Tie the HTTP response to one stable systemd MainPID. Ownership is checked both
# before and after curl so a dying bridge cannot hand the port to an unrelated
# process during the probe and still be reported ready.
bridge_http_ready() {
    local port="${1:-8001}" before after

    before="$(bridge_main_pid || true)"
    [ -n "$before" ] || return 1
    bridge_running || return 1
    process_listens_ipv4_port "$before" "$port" || return 1
    curl -fsS --max-time 1 "http://127.0.0.1:${port}/telemetry/gate" >/dev/null 2>&1 \
        || return 1
    after="$(bridge_main_pid || true)"
    [ "$before" = "$after" ] || return 1
    bridge_running || return 1
    process_listens_ipv4_port "$after" "$port" || return 1
    printf '%s' "$after"
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
#   OTHER_COMMANDER_PATTERNS='cmd_vel_to_loco|my_new_thing' c3po up
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

# --- who holds the camera ---------------------------------------------------
#
# ONE IMPLEMENTATION, because there were three. `stop_gemm`, `take_camera` and
# `perception_up` each grew their own copy of this, written from scratch each
# time, and they did not agree: take_camera's copy matched only processes that
# NAME the device in their argv, which librealsense does not do — so it reported
# "nothing has it" while the other team pulled 15 fps off the sensor. Three
# copies of a rule is three chances to get it wrong, and it took two of them.
#
# SNAPSHOT FIRST, THEN FILTER, and that ordering is the design rather than a
# style choice. The obvious form of this — `ps | grep /dev/video4` — matches the
# grep's OWN command line, because the pattern is in its argv. Every previous
# copy papered over that with an exclusion (`index($0,"awk") == 0`, a bracket
# class like `[v]ideohub`), which is a rule you have to remember every single
# time and which silently breaks the day a real holder has "awk" in its path.
# Capturing the table into a variable first means the filter process does not
# exist yet when the snapshot is taken, so it cannot match itself and no
# exclusion is needed. That property is what makes this testable: the filter is
# a pure function over text.
VIDEO_DEVICE="${VIDEO_DEVICE:-/dev/video4}"

# The programs that actually open this camera, which is the ONLY reliable
# signal available without root.
#
# Everything else is a guess about arguments, and arguments lie in both
# directions: `grep /dev/video4 stop_gemm` passes the device as a real argv
# token while opening nothing, and gemm's ROS node opens the device while never
# mentioning it (librealsense enumerates by USB path). No amount of cleverness
# about the command line separates those two; the program name does.
#
# The cost is honest and stated where it matters: a holder we have not seen
# before is missed. That is why `stop_gemm` says "no holder in the process
# table" rather than "the camera is free", and points at `sudo fuser -v`, which
# is the authority. This check is for the common case, unattended, without a
# password prompt in the middle of a bring-up.
#
# Split in two because the device path disambiguates one group and not the
# other: `videohub_pc4` runs twice, once per camera, and only the instance
# holding THIS device counts (the chest one takes /dev/video10).
VIDEO_HOLDERS_BY_DEVICE="${VIDEO_HOLDERS_BY_DEVICE:-videohub_pc4 teleimager}"
VIDEO_HOLDERS_BY_NAME="${VIDEO_HOLDERS_BY_NAME:-realsense2_camera_node}"

# Pure. Reads `pid args` lines on stdin, prints those that hold the device.
#
# A DEVICE PATH IS MATCHED AS A WHOLE ARGUMENT, never as a substring, and that
# distinction is not pedantry — it was caught by the test suite the hour it was
# written. Any process whose command line merely MENTIONS the device matches a
# substring search: an editor with the file open, a grep, a colleague's shell,
# or the very command someone is using to debug the camera. The scan then
# reports that process as holding the sensor, which is the same class of
# confident-wrong answer as the self-match it replaced.
#
# `videohub_pc4 /dev/video4` passes it the device as its own argv token, so
# comparing tokens is both stricter and exactly right. A shell that happens to
# contain the string inside a longer quoted command has no such token.
filter_video_holders() {
    local dev="${1:-$VIDEO_DEVICE}"
    local by_dev="${2:-$VIDEO_HOLDERS_BY_DEVICE}"
    local by_name="${3:-$VIDEO_HOLDERS_BY_NAME}"
    awk -v dev="$dev" -v by_dev="$by_dev" -v by_name="$by_name" '
        function prog(path,   parts, n) {
            n = split(path, parts, "/")
            return parts[n]
        }
        {
            # $1 is the pid, $2 the executable, $3.. the arguments.
            name = prog($2)

            # Group one: known program AND this device among its arguments.
            # Both halves are required — `videohub_pc4` runs once per camera and
            # only the instance holding THIS device is the one in the way.
            n = split(by_dev, want, " ")
            for (j = 1; j <= n; j++) {
                if (want[j] == "" || index(name, want[j]) == 0) continue
                for (i = 3; i <= NF; i++)
                    if ($i == dev) { print; next }
            }

            # Group two: known program, device never named. The program IS the
            # evidence, because librealsense opens by USB path.
            n = split(by_name, want, " ")
            for (j = 1; j <= n; j++)
                if (want[j] != "" && index(name, want[j]) > 0) { print; next }
        }
    '
}

# The real thing: snapshot the process table, then filter it.
video_device_holders() {
    local table
    table="$(ps -eo pid=,args= 2>/dev/null || true)"
    printf '%s\n' "$table" | filter_video_holders "$@"
}

# A one-word answer for the callers that branch on WHO has it, since the fix
# differs: `take_camera` for Unitree's, `stop_gemm` for the co-tenant's.
#   videohub | gemm | other | none
video_holder_kind() {
    local holders
    holders="$(video_device_holders "$@")"
    if [ -z "${holders//[[:space:]]/}" ]; then echo none; return; fi
    case "$holders" in
        *videohub*) echo videohub ;;
        *realsense2_camera*) echo gemm ;;
        *) echo other ;;
    esac
}
