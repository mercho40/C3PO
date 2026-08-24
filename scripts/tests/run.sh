#!/usr/bin/env bash
# Tests for the shell scripts. `bash scripts/tests/run.sh`
#
# WHY THIS EXISTS. There were about twenty shell scripts in this repo and zero
# tests for any of them, and that is where the expensive bugs have been living.
# In one session, at the robot, by hand:
#
#   * `pgrep -f "docker build -t ..."` matched its OWN command line, so a build
#     that had been dead for 19 minutes was reported as still running.
#   * `stop_gemm` printed "✓ /dev/video4 is free" by checking a process that was
#     not running, while a different process held the device.
#   * `take_camera` reported "nothing has it" while the co-tenant pulled 15 fps
#     off the sensor, because its copy of the holder check could not see a
#     process that opens the device without naming it.
#   * the old PATH installer maintained a second command inventory that drifted
#     from the directory it was meant to expose.
#   * A `die` message with backticks in it was EXECUTED by bash.
#
# Every one of those is a pure-text bug, findable in milliseconds on a laptop,
# and every one was instead found by a person standing next to a robot. That is
# the whole argument for this file.
#
# NO DEPENDENCIES ON PURPOSE. Not bats, not shunit2: this has to run on the
# Jetson, on a Mac, and in whatever CI eventually appears, without an install
# step. The cost is that it is about forty lines of harness, which is cheaper
# than a dependency nobody can install on the robot.

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

pass=0; fail=0; failed_names=()

# Assertions print the DIFFERENCE, not just "failed". A test suite whose output
# sends you back to the source to work out what it meant is a slower version of
# no test suite.
check() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); failed_names+=("$name")
        printf '  ✗ %s\n' "$name" >&2
        printf '      expected: %s\n' "$(printf '%s' "$expected" | sed -n '1,6p' | tr '\n' '|')" >&2
        printf '      actual:   %s\n' "$(printf '%s' "$actual"   | sed -n '1,6p' | tr '\n' '|')" >&2
    fi
}

check_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) pass=$((pass + 1)) ;;
        *)
            fail=$((fail + 1)); failed_names+=("$name")
            printf '  ✗ %s\n      expected to contain: %s\n      in: %s\n' \
                "$name" "$needle" "$(printf '%s' "$haystack" | head -c 300)" >&2
            ;;
    esac
}

check_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*)
            fail=$((fail + 1)); failed_names+=("$name")
            printf '  ✗ %s\n      expected NOT to contain: %s\n' "$name" "$needle" >&2
            ;;
        *) pass=$((pass + 1)) ;;
    esac
}

# ---------------------------------------------------------------------------
# every script parses
# ---------------------------------------------------------------------------
#
# `bash -n` on everything. Cheap, and it is the check that would have caught the
# backticks-in-a-die-message bug before it ran on the robot.
echo "== syntax =="
while IFS= read -r script; do
    name="syntax: ${script#"$repo"/}"
    if out="$(bash -n "$script" 2>&1)"; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); failed_names+=("$name")
        printf '  ✗ %s\n      %s\n' "$name" "$out" >&2
    fi
done < <(find "$repo/scripts" -type f \( -name '*.sh' -o ! -name '*.*' \) \
         -exec grep -lE '^#!/usr/bin/env bash|^#!/bin/bash' {} + | sort)

# ---------------------------------------------------------------------------
# bridge socket ownership
# ---------------------------------------------------------------------------
echo "== bridge socket ownership =="

# shellcheck source=../robot/_common.sh
C3PO_DIR="$repo" source "$repo/scripts/robot/_common.sh"

# Readiness must belong to systemd's MainPID on the configured IPv4 address,
# not merely to whichever process or interface happens to own the port.
proc_fixture="$(mktemp -d)"
mkdir -p "$proc_fixture/net" "$proc_fixture/4321/fd" \
    "$proc_fixture/9876/fd" "$proc_fixture/2468/fd"
printf '%s\n' \
    '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode' \
    '   0: 0100007F:1F41 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 7654321' \
    '   1: 00000000:1F41 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 2222222' \
    > "$proc_fixture/net/tcp"
ln -s 'socket:[7654321]' "$proc_fixture/4321/fd/7"
ln -s 'socket:[9999999]' "$proc_fixture/9876/fd/8"
ln -s 'socket:[2222222]' "$proc_fixture/2468/fd/9"
if C3PO_PROC_ROOT="$proc_fixture" process_listens_ipv4_port 4321 8001 127.0.0.1; then
    pass=$((pass + 1))
else
    fail=$((fail + 1)); failed_names+=("MainPID owns its configured listening socket")
fi
if C3PO_PROC_ROOT="$proc_fixture" process_listens_ipv4_port 9876 8001 127.0.0.1; then
    fail=$((fail + 1)); failed_names+=("a different process cannot satisfy bridge readiness")
else
    pass=$((pass + 1))
fi
if C3PO_PROC_ROOT="$proc_fixture" process_listens_ipv4_port 4321 8000 127.0.0.1; then
    fail=$((fail + 1)); failed_names+=("the MainPID must own the configured port")
else
    pass=$((pass + 1))
fi
if C3PO_PROC_ROOT="$proc_fixture" process_listens_ipv4_port 2468 8001; then
    pass=$((pass + 1))
else
    fail=$((fail + 1)); failed_names+=("the deployed wildcard listener satisfies readiness")
fi
rm -rf "$proc_fixture"

# The combined probe checks ownership on both sides of HTTP. Model a bridge that
# loses its socket during curl; a successful HTTP response must not rescue it.
if (
    bridge_main_pid() { printf '4321'; }
    bridge_running() { return 0; }
    curl() { return 0; }
    ownership_calls=0
    process_listens_ipv4_port() {
        ownership_calls=$((ownership_calls + 1))
        [ "$ownership_calls" -eq 1 ]
    }
    bridge_http_ready 8001 >/dev/null
); then
    fail=$((fail + 1)); failed_names+=("readiness is rechecked after HTTP")
else
    pass=$((pass + 1))
fi

got="$(
    bridge_main_pid() { printf '4321'; }
    bridge_running() { return 0; }
    curl() { return 0; }
    process_listens_ipv4_port() { return 0; }
    bridge_http_ready 8001
)"
check "a stable owned HTTP listener returns its MainPID" "4321" "$got"

# ---------------------------------------------------------------------------
# who holds the camera
# ---------------------------------------------------------------------------
#
# The one that has been wrong in three different scripts. `filter_video_holders`
# is pure — it reads a process table on stdin — precisely so it can be tested
# against a fixture instead of against a robot.
echo "== camera holder detection =="
TABLE_VIDEOHUB='   1868 /unitree/module/video_hub_pc4/videohub_pc4_chest /dev/video10
   2233 /unitree/module/video_hub_pc4/videohub_pc4 /dev/video4
   3001 /usr/lib/systemd/systemd --user'

TABLE_GEMM='   3001 /usr/lib/systemd/systemd --user
  37305 /opt/ros/humble/lib/realsense2_camera/realsense2_camera_node --ros-args -r __ns:=/camera'

TABLE_IDLE='   3001 /usr/lib/systemd/systemd --user
   4002 sshd: unitree@pts/0'

got="$(printf '%s\n' "$TABLE_VIDEOHUB" | filter_video_holders)"
check_contains "videohub holding video4 is found" "videohub_pc4 /dev/video4" "$got"
check_not_contains "the chest camera on video10 is not a video4 holder" "video10" "$got"

# The bug that reported "nothing has it" while the other team used the camera:
# librealsense opens by USB path, so the device name never appears in argv.
got="$(printf '%s\n' "$TABLE_GEMM" | filter_video_holders)"
check_contains "gemm's node is found though it never names the device" \
    "realsense2_camera_node" "$got"

got="$(printf '%s\n' "$TABLE_IDLE" | filter_video_holders)"
check "an idle machine reports no holder" "" "$got"

# MENTIONING THE DEVICE IS NOT HOLDING IT. This suite caught the substring
# version reporting a shell as a camera holder, because the command being run to
# edit these very scripts had "/dev/video4" inside it. An editor, a grep, or the
# person debugging the camera would each have been reported as the thing holding
# it — the same confidently-wrong answer as the self-match it replaced.
TABLE_MENTIONS_ONLY="$TABLE_IDLE
   9998 grep --color=auto /dev/video4 scripts/robot/stop_gemm
   9999 bash -c sed -i s|index(\$0, \"/dev/video4\")|x| scripts/robot/_common.sh"
got="$(printf '%s\n' "$TABLE_MENTIONS_ONLY" | filter_video_holders)"
check "a process that merely mentions the device is not a holder" "" "$got"

# Including the hardest version: a grep whose PATTERN is the device path, which
# passes it as a genuine argv token while opening nothing. Argument matching
# cannot separate that from a real holder in either direction, which is why the
# rule is the program name.
check_not_contains "a grep whose pattern IS the device is not a holder" "grep" "$got"

# ...while the real holder passes it as its own argv token, and is still found.
TABLE_BOTH="$TABLE_MENTIONS_ONLY
   2233 /unitree/module/video_hub_pc4/videohub_pc4 /dev/video4"
got="$(printf '%s\n' "$TABLE_BOTH" | filter_video_holders)"
check_contains "the real holder is found alongside the noise" "videohub_pc4" "$got"
check_not_contains "and the noise is not reported with it" "grep --color" "$got"

# ...and the property that matters in practice: the real function never sees its
# own filter, because the table is captured before the filter exists.
got="$(video_device_holders)"
check_not_contains "video_device_holders does not match its own awk" "filter_video_holders" "$got"
check_not_contains "video_device_holders does not match its own ps" "ps -eo" "$got"

echo "== holder kind =="
# video_holder_kind shells out to ps, so test the classification through the
# same pure filter it wraps rather than faking the process table globally.
classify() {
    local table="$1" holders
    holders="$(printf '%s\n' "$table" | filter_video_holders)"
    if [ -z "${holders//[[:space:]]/}" ]; then echo none; return; fi
    case "$holders" in
        *videohub*) echo videohub ;;
        *realsense2_camera*) echo gemm ;;
        *) echo other ;;
    esac
}
check "videohub is classified as videohub" "videohub" "$(classify "$TABLE_VIDEOHUB")"
check "gemm's node is classified as gemm"  "gemm"     "$(classify "$TABLE_GEMM")"
check "an idle machine classifies as none" "none"     "$(classify "$TABLE_IDLE")"

# ---------------------------------------------------------------------------
# the one operator CLI
# ---------------------------------------------------------------------------
echo "== c3po CLI =="
out="$(bash "$repo/scripts/robot/c3po" --help 2>&1)"
check_contains "the CLI exposes integrated profiles" "c3po up [operator|core|teleop|<perception-stage>]" "$out"
check_contains "the CLI exposes complete shutdown" "c3po down | restart | status | logs" "$out"
check_contains "the CLI exposes explicit perception stages" "c3po perception up <stage>" "$out"
check_contains "the CLI exposes the deliberate camera takeover" "c3po camera take" "$out"

if out="$(bash "$repo/scripts/robot/c3po" up unknown 2>&1)"; then
    fail=$((fail + 1)); failed_names+=("the CLI rejects unknown integrated profiles")
else
    check_contains "the CLI rejects unknown integrated profiles" \
        "unknown up profile: unknown" "$out"
fi

if out="$(bash "$repo/scripts/robot/c3po" start unexpected 2>&1)"; then
    fail=$((fail + 1)); failed_names+=("the CLI rejects ignored lifecycle arguments")
else
    check_contains "the CLI rejects ignored lifecycle arguments" \
        "start takes no arguments" "$out"
fi
if out="$(bash "$repo/scripts/robot/c3po" perception stop unexpected 2>&1)"; then
    fail=$((fail + 1)); failed_names+=("perception stop rejects ignored arguments")
else
    check_contains "perception stop rejects ignored arguments" \
        "perception stop takes no arguments" "$out"
fi

cli_source="$(cat "$repo/scripts/robot/c3po")"
check_contains "camera take dispatches to the guarded implementation" \
    'exec "$here/take_camera"' "$cli_source"
check_contains "perception remains an explicit subcommand" \
    'exec "$here/perception_up"' "$cli_source"
check_not_contains "status exposes no automatic repair path" \
    '--repair' "$cli_source"
check_contains "restart requires an active systemd unit" \
    'if [ "$state" = "active" ] && ready_pid=' "$cli_source"
check_contains "restart rejects a second bridge process" \
    'bridge_process_pids | grep -vx "$ready_pid"' "$cli_source"
check_contains "restart ties HTTP readiness to a stable MainPID socket" \
    'bridge_http_ready 8001' "$cli_source"

start_source="$(cat "$repo/scripts/robot/run_c3po")"
check_contains "start ties HTTP readiness to a stable MainPID socket" \
    'bridge_http_ready 8001' "$start_source"
common_source="$(cat "$repo/scripts/robot/_common.sh")"
check_contains "socket ownership uses the explicit deployed LAN bind" \
    '0.0.0.0)   address_hex="00000000"' "$common_source"
check_contains "socket ownership is rechecked after HTTP" \
    'process_listens_ipv4_port "$after" "$port"' "$common_source"

# ---------------------------------------------------------------------------
# install and safety invariants that must remain visible in source
# ---------------------------------------------------------------------------

echo "== stack hardening invariants =="
install_stack_source="$(cat "$repo/scripts/robot/install_stack.sh")"
check_contains "install_stack stages logrotate config as root" \
    'sudo install -o root -g root -m 0644 "$here/c3po-logs.logrotate" "$stage/c3po.logrotate"' \
    "$install_stack_source"
check_contains "install_stack installs one operator command" \
    'ln -sf "$here/c3po" "$bin_dir/c3po"' "$install_stack_source"
check_not_contains "install_stack never symlinks a user-owned logrotate config" \
    'ln -sf "$here/c3po-logs.logrotate"' "$install_stack_source"
check_contains "the bridge unit is staged as a root-owned copy" \
    'sudo install -o root -g root -m 0644 "$here/$unit" "$stage/$unit"' "$install_stack_source"
check_contains "legacy bridge symlinks are atomically replaced" \
    'sudo mv -f "/etc/systemd/system/.$unit.c3po-new" "/etc/systemd/system/$unit"' "$install_stack_source"
check_contains "automatic perception is removed during migration" \
    "'/etc/systemd/system/c3po-perception@.service'" "$install_stack_source"
check_contains "the repair timer is disabled during migration" \
    'sudo systemctl disable --now c3po-health.timer' "$install_stack_source"
check_contains "an in-flight legacy repair is stopped too" \
    'sudo systemctl stop c3po-health.service' "$install_stack_source"
check_contains "migration refuses an activating perception unit" \
    '$3 == "active" || $3 == "activating" || $3 == "deactivating" || $3 == "reloading"' \
    "$install_stack_source"
check_not_contains "PID 1 never follows unit symlinks into the writable checkout" \
    'sudo ln -sf "$here/$unit"' "$install_stack_source"

bridge_unit_source="$(cat "$repo/scripts/robot/c3po-bridge.service")"
check_contains "systemd directly supervises the bridge" "Type=exec" "$bridge_unit_source"
check_contains "the bridge daemon binds to the robot LAN" "Environment=BRIDGE_HOST=0.0.0.0" "$bridge_unit_source"
check_contains "the bridge permits the local web dev origins" \
    "Environment=BRIDGE_CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001" \
    "$bridge_unit_source"
check_not_contains "the bridge has no pidfile" "PIDFile=" "$bridge_unit_source"
check_not_contains "the bridge unit does not call run_c3po" "ExecStart=/home/unitree/c3po/scripts/robot/run_c3po" "$bridge_unit_source"

stop_stack_source="$(cat "$repo/scripts/robot/stop_c3po")"
check_contains "stack stop removes teleop before the bridge" \
    '"$here/stop_teleop"' "$stop_stack_source"
check_contains "stack stop cancels even an activating bridge unit" \
    'sudo systemctl stop c3po-bridge.service' "$stop_stack_source"

stop_perception_source="$(cat "$repo/scripts/robot/stop_perception")"
check_contains "operator perception stop deactivates the owning unit" \
    "systemctl list-units --all --plain --no-legend 'c3po-perception@*.service'" \
    "$stop_perception_source"
check_contains "perception unit shutdown is delegated to systemd" \
    'sudo systemctl stop $units' "$stop_perception_source"

health_source="$(cat "$repo/scripts/robot/c3po_health")"
check_contains "health resolves the checkout independently of HOME" \
    'C3PO_DIR="${C3PO_DIR:-$(cd "$here/../.." && pwd)}"' "$health_source"
health_py_source="$(cat "$repo/apps/bridge/src/bridge/health.py")"
check_not_contains "health contains no systemd repair action" \
    'try-restart' "$health_py_source"
sudoers_source="$(cat "$repo/scripts/robot/c3po.sudoers")"
check_not_contains "sudoers grants no perception lifecycle command" \
    'c3po-perception@' "$sudoers_source"

commander_source="$(cat "$repo/scripts/robot/_common.sh")"
check_contains "unitree_slam is treated as a locomotion commander" \
    'brainco_hand_server|unitree_slam' "$commander_source"

# ---------------------------------------------------------------------------
# ShellCheck, when it is available
# ---------------------------------------------------------------------------
#
# Optional for a direct run on the robot, where it is not installed. Package/CI
# runs set C3PO_REQUIRE_SHELLCHECK=1 so a missing analyzer is a failure rather
# than a deceptively green skipped gate.
echo "== shellcheck =="
if command -v shellcheck >/dev/null 2>&1; then
    while IFS= read -r script; do
        name="shellcheck: ${script#"$repo"/}"
        if out="$(shellcheck -x -S warning "$script" 2>&1)"; then
            pass=$((pass + 1))
        else
            fail=$((fail + 1)); failed_names+=("$name")
            printf '  ✗ %s\n%s\n' "$name" "$(printf '%s' "$out" | head -20)" >&2
        fi
    done < <(find "$repo/scripts" -type f \( -name '*.sh' -o ! -name '*.*' \) \
             -exec grep -lE '^#!/usr/bin/env bash|^#!/bin/bash' {} + | sort)
elif [ "${C3PO_REQUIRE_SHELLCHECK:-0}" = "1" ]; then
    fail=$((fail + 1)); failed_names+=("shellcheck is required but not installed")
    printf '  ✗ shellcheck is required but not installed\n' >&2
else
    printf '  – skipped: shellcheck is not installed (brew install shellcheck)\n'
fi

# ---------------------------------------------------------------------------

echo
if [ "$fail" -eq 0 ]; then
    printf '%s tests passed\n' "$pass"
    exit 0
fi
printf '%s passed, %s FAILED:\n' "$pass" "$fail" >&2
printf '  - %s\n' "${failed_names[@]}" >&2
exit 1
