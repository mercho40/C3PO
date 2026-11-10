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
    fail=$((fail + 1)); failed_names+=("a wildcard listener satisfies a loopback request")
fi
# The regression of 2026-09-14: the unit moved to 127.0.0.1 and this default was
# left at 0.0.0.0, so the probe hunted for a wildcard listener that no longer
# existed and `c3po up` aborted on a bridge that was serving fine.
if C3PO_PROC_ROOT="$proc_fixture" process_listens_ipv4_port 4321 8001; then
    pass=$((pass + 1))
else
    fail=$((fail + 1)); failed_names+=("the deployed loopback listener satisfies the default host")
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
check_contains "the CLI exposes the map session" "c3po map start [name] | save [name]" "$out"
if out="$(bash "$repo/scripts/robot/c3po" map save a b 2>&1)"; then
    fail=$((fail + 1)); failed_names+=("map save rejects a second name")
else
    check_contains "map save rejects a second name" "takes at most a map name" "$out"
fi

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
# 2026-10-15: run_c3po stopped the other team's gemm-bringup on every start.
# Stopping gemm is now opt-in, and the only stop_gemm call sits behind it.
check_contains "start stops gemm only on an explicit take-over" \
    'if [ "${C3PO_TAKE_OVER:-0}" = "1" ]; then' "$start_source"
check_contains "start calls stop_gemm exactly once (the take-over branch)" \
    "stop_gemm calls: 1." "stop_gemm calls: $(grep -c '"$here/stop_gemm"' "$repo/scripts/robot/run_c3po")."
# ...and a sudo that needs a password with no terminal fails with the fix,
# not with sudo's own "a terminal is required".
check_contains "start explains a sudo that cannot prompt" \
    'if ! sudo -n -l /usr/bin/systemctl start c3po-bridge.service >/dev/null 2>&1 && ! [ -t 0 ]; then' "$start_source"
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
check_contains "install_stack backs the unit up only through the changed-unit check" \
    'backup_unit_if_changed "/etc/systemd/system/$unit" "$stage/$unit"' "$install_stack_source"
check_contains "install_stack records the deployed commit in ~/.c3po" \
    'record_deploy "$here/../.." "$HOME/.c3po"' "$install_stack_source"

# The rollback copy and the deploy record, against a temp HOME and a throwaway
# repo. sudo runs the command as us, and chown root is a no-op, so the real
# functions run unchanged.
install_tmp="$(mktemp -d)"
deploy_dir="$install_tmp/home/.c3po"
git -C "$install_tmp" init -q repo
commit_in() { git -C "$install_tmp/repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "$1"; }
head_of() { git -C "$install_tmp/repo" rev-parse HEAD; }
first_field() { cut -d' ' -f1 "$1" 2>/dev/null; }
commit_in one; c1="$(head_of)"
(HOME="$install_tmp/home"; record_deploy "$install_tmp/repo" "$HOME/.c3po")
check "the first install records its commit" "$c1" "$(first_field "$deploy_dir/deployed")"
check "the first install has no previous deploy" "absent" \
    "$([ -e "$deploy_dir/previous-deploy" ] && echo present || echo absent)"
commit_in two; c2="$(head_of)"
(HOME="$install_tmp/home"; record_deploy "$install_tmp/repo" "$HOME/.c3po")
(HOME="$install_tmp/home"; record_deploy "$install_tmp/repo" "$HOME/.c3po")
check "a new commit is recorded as deployed" "$c2" "$(first_field "$deploy_dir/deployed")"
check "re-installing the same commit keeps the one before it" "$c1" \
    "$(first_field "$deploy_dir/previous-deploy")"
check_contains "the record carries a UTC time" "Z" "$(cut -d' ' -f2 "$deploy_dir/deployed")"

backup_with() {  # <live content or -> <staged content>: prints the backup afterwards
    (
        sudo() { "$@"; }
        chown() { :; }
        rm -f "$install_tmp/live"
        [ "$1" = - ] || printf '%s\n' "$1" >"$install_tmp/live"
        printf '%s\n' "$2" >"$install_tmp/staged"
        backup_unit_if_changed "$install_tmp/live" "$install_tmp/staged" "$install_tmp/backup"
    )
    cat "$install_tmp/backup" 2>/dev/null
}
printf 'older\n' >"$install_tmp/backup"
check "no loaded unit leaves the backup alone" "older" "$(backup_with - new)"
check "re-installing the loaded unit keeps the older backup" "older" "$(backup_with same same)"
check "a changed unit backs up the loaded one" "loaded" "$(backup_with loaded new)"
rm -rf "$install_tmp"

bridge_unit_source="$(cat "$repo/scripts/robot/c3po-bridge.service")"
check_contains "systemd directly supervises the bridge" "Type=exec" "$bridge_unit_source"
# LOOPBACK, NOT THE LAN. This asserted `BRIDGE_HOST=0.0.0.0` until 2026-09-06,
# which pinned the wrong behaviour: /mcp can walk a 35 kg humanoid and carries no
# authentication of its own, so a wildcard bind put it on the school Wi-Fi for
# anyone. Five other places in the repo already said loopback — .env.example, the
# module default, run_c3po, telemetry.ts and OPERATIONS — and this test was
# holding the one that disagreed in place. Reach it through the documented
# tunnel; docs/OPERATIONS.md has the redeploy steps.
check_contains "the bridge daemon binds loopback, not the LAN" \
    "Environment=BRIDGE_HOST=127.0.0.1" "$bridge_unit_source"
check_not_contains "the bridge daemon does not bind a wildcard address" \
    "Environment=BRIDGE_HOST=0.0.0.0" "$bridge_unit_source"
check_contains "the bridge permits the local web dev origins" \
    "Environment=BRIDGE_CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001" \
    "$bridge_unit_source"
check_not_contains "the bridge has no pidfile" "PIDFile=" "$bridge_unit_source"
check_not_contains "the bridge unit does not call run_c3po" "ExecStart=/home/unitree/c3po/scripts/robot/run_c3po" "$bridge_unit_source"
check_contains "the robot installs exactly the committed lock" \
    "uv sync --locked" "$(cat "$repo/scripts/robot/bridge_sync")"

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
# sudo matches exact argv: the grant and its three callers must stay identical.
for verb in start stop restart; do
    check_contains "sudoers grants exactly: systemctl $verb c3po-bridge.service" \
        "unitree ALL=(root) NOPASSWD: /usr/bin/systemctl $verb c3po-bridge.service" "$sudoers_source"
done
for forbidden in 'daemon-reload' 'systemctl edit' 'enable' '*'; do
    check_not_contains "sudoers grants nothing with: $forbidden" "$forbidden" "$sudoers_source"
done
check_contains "c3po restart runs the exact granted command line" \
    'sudo systemctl restart c3po-bridge.service' "$cli_source"
check_contains "start runs the exact granted command line" \
    'sudo systemctl start c3po-bridge.service' "$start_source"

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
echo "== teleop readiness =="
#
# `run_teleop` used to sleep 2 s and ask whether the process still existed. A
# server that comes up, fails to bind 8767 and sits there is ALIVE — so it
# printed "teleop stream up" and handed the operator a port serving nothing.
# `quest_setup.sh` then forwards that port to the headset, where it finally
# surfaces as a session that will not start, a long way from the cause.
#
# `run_c3po` was rewritten for exactly this bug and waits for its port. These
# assert the sidecar learned the same lesson.

run_teleop_src="$(cat "$repo/scripts/robot/run_teleop")"
check_contains "teleop start waits for the port, not just the process" \
    "nc -z 127.0.0.1" "$run_teleop_src"
check_contains "teleop start distinguishes running-but-not-listening" \
    "never bound" "$run_teleop_src"
check_contains "teleop start gives up early when the process died" \
    'sidecar_pid "$TELEOP_PID" >/dev/null 2>&1 || break' "$run_teleop_src"
check_not_contains "teleop start no longer reports success on a bare sleep" \
    'sleep 2

if sidecar_pid' "$run_teleop_src"

echo "== robot tunnel =="
# The reconnecting tunnel (2026-10-15: it dropped twice in one session).
tunnel_cmd="$("$repo/scripts/robot_tunnel.sh" --print)"
check_contains "the tunnel forwards the bridge to the robot's loopback" \
    "-L 8001:127.0.0.1:8001" "$tunnel_cmd"
check_contains "the tunnel forwards teleop to the robot's loopback" \
    "-L 8767:127.0.0.1:8767" "$tunnel_cmd"
check_contains "a taken local port fails the tunnel instead of a silent no-op" \
    "ExitOnForwardFailure=yes" "$tunnel_cmd"
check_contains "the tunnel defaults to the c3po ssh alias" " c3po" "$tunnel_cmd"
check_contains "the tunnel takes the host as an argument" \
    " c3po-wire" "$("$repo/scripts/robot_tunnel.sh" --print c3po-wire)"

echo "== robot internet =="
check "the Mac proxy refuses loopback, private and non-web destinations" "ok" \
    "$(python3 "$repo/scripts/robot_internet.py" --self-test 2>&1)"

echo "== robot session =="
# One command for tunnel + robot internet + console. Env cleared so a developer's
# own C3PO_SSH_HOST / C3PO_ROBOT_HOST cannot change what is asserted.
session_print="$(C3PO_SSH_HOST='' C3PO_ROBOT_HOST='' "$repo/scripts/robot_session.sh" --print)"
check_contains "the session starts the tunnel to the c3po alias" "robot_tunnel.sh c3po" "$session_print"
check_contains "the session lends the robot the Mac's internet" "robot_internet.py c3po" "$session_print"
check_contains "the session starts the console against the tunnel" \
    "C3PO_ROBOT_HOST=localhost bun run dev:robot" "$session_print"
session_wire="$(C3PO_SSH_HOST='' "$repo/scripts/robot_session.sh" --print c3po-wire)"
check_contains "the session takes the host as an argument (tunnel)" "robot_tunnel.sh c3po-wire" "$session_wire"
check_contains "the session takes the host as an argument (internet)" "robot_internet.py c3po-wire" "$session_wire"

# Fake `route` / `ipconfig`: ipconfig answers only for the interface route named.
session_stub="$(mktemp -d)"
# shellcheck disable=SC2016  # the $vars are for the stub to expand, not us
printf '#!/bin/sh\necho "  interface: $FAKE_IFACE"\n' >"$session_stub/route"
# shellcheck disable=SC2016
printf '#!/bin/sh\n[ "$2" = "$FAKE_IFACE" ] && echo "$FAKE_ADDR"\n' >"$session_stub/ipconfig"
chmod +x "$session_stub/route" "$session_stub/ipconfig"
cable_out="$(PATH="$session_stub:$PATH" FAKE_IFACE=en11 FAKE_ADDR=192.168.123.99 C3PO_ETH='' \
    "$repo/scripts/robot_session.sh" --print)"
check_contains "the cable on en11 at 192.168.123.99 passes" "✓ cable" "$cable_out"
check_not_contains "a good cable prints no sudo fix" "sudo ipconfig" "$cable_out"
wifi_out="$(PATH="$session_stub:$PATH" FAKE_IFACE=en0 FAKE_ADDR=10.10.32.5 C3PO_ETH='' \
    "$repo/scripts/robot_session.sh" --print)"
check_contains "the robot reached over Wi-Fi prints the cable fix, with no router" \
    "sudo ipconfig set en11 MANUAL 192.168.123.99 255.255.255.0" "$wifi_out"
check_not_contains "the session never says ✓ cable off the cable" "✓ cable" "$wifi_out"
rm -rf "$session_stub"

# The whole session against stub children: a copy of the script whose repo holds
# a fake robot_tunnel.sh, and fake python3/bun/curl/lsof/sleep on PATH. Each long
# child records its pid and idles. It runs in its own process group (perl's
# setpgrp) because the session's teardown is `kill 0`, which would otherwise take
# this test runner down with it.
session_fake="$(mktemp -d)"
mkdir -p "$session_fake/scripts" "$session_fake/bin"
cp "$repo/scripts/robot_session.sh" "$session_fake/scripts/"
# shellcheck disable=SC2016  # the $vars are for the stubs to expand, not us
child='#!/bin/sh\necho $$ >>"$FAKE_PIDS"\nexec /bin/sleep 300\n'
# shellcheck disable=SC2059  # the stub body is the format on purpose
printf "$child" >"$session_fake/scripts/robot_tunnel.sh"
# shellcheck disable=SC2059
printf "$child" >"$session_fake/bin/python3"
# shellcheck disable=SC2016
printf '#!/bin/sh\ncase "$*" in *smoke-agent*) exit "$FAKE_AGENT_RC" ;; esac\necho $$ >>"$FAKE_PIDS"\nexec /bin/sleep 300\n' \
    >"$session_fake/bin/bun"
printf '#!/bin/sh\nexit 0\n' >"$session_fake/bin/curl"
printf '#!/bin/sh\nexit 1\n' >"$session_fake/bin/lsof"
printf '#!/bin/sh\nexec /bin/sleep 0.1\n' >"$session_fake/bin/sleep"
chmod +x "$session_fake/scripts/robot_tunnel.sh" "$session_fake/bin/"*

# run_session <agent rc>: start it, wait for the table, TERM it, then echo the
# output and how many children survived. Survivors are killed so nothing leaks.
run_session() {
    local out="$session_fake/out.$1" pids="$session_fake/pids.$1" pid survivors=0 p _
    : >"$pids"
    FAKE_PIDS="$pids" FAKE_AGENT_RC="$1" PATH="$session_fake/bin:$PATH" TMPDIR="$session_fake" \
        C3PO_SSH_HOST='' perl -e 'setpgrp(0, 0); exec @ARGV' \
        bash "$session_fake/scripts/robot_session.sh" >"$out" 2>&1 &
    pid=$!
    for _ in $(seq 1 100); do grep -q 'Ctrl-C stops all three' "$out" && break; /bin/sleep 0.1; done
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; /bin/sleep 0.1; done
    /bin/sleep 0.2
    while read -r p; do
        if kill -0 "$p" 2>/dev/null; then survivors=$((survivors + 1)); kill -9 "$p"; fi
    done <"$pids"
    kill -9 "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    printf '%s\nchildren started: %s\nsurvivors: %s\n' "$(cat "$out")" "$(wc -l <"$pids" | tr -d ' ')" "$survivors"
}
agent_ok_out="$(run_session 0)"
check_contains "a model that makes a tool call is a passing agent row" "✓ agent" "$agent_ok_out"
agent_bad_out="$(run_session 1)"
check_contains "a model that does not is a failing agent row" "✗ agent" "$agent_bad_out"
check_contains "the session started its three children" "children started: 3" "$agent_ok_out"
check_contains "TERM leaves no child of the session running" "survivors: 0" "$agent_ok_out"
check_contains "TERM leaves no child running after a failed row either" "survivors: 0" "$agent_bad_out"
rm -rf "$session_fake"

# ---------------------------------------------------------------------------
# c3po_preflight: no SAFETY_CONFIRM_SECRET on the bridge is a blocker
# ---------------------------------------------------------------------------
echo "== c3po_preflight confirm secret =="
preflight_stub="$(mktemp -d)"
# A curl that answers /safety/status from $FAKE_STATUS and fails everything else.
# shellcheck disable=SC2016  # the $vars are for the stub to expand, not us
printf '#!/bin/sh\nfor a; do case "$a" in */safety/status) echo "$FAKE_STATUS"; exit 0 ;; esac; done\nexit 7\n' \
    >"$preflight_stub/curl"
chmod +x "$preflight_stub/curl"
unset_out="$(PATH="$preflight_stub:$PATH" FAKE_STATUS='{"confirm_configured":false}' \
    bash "$repo/scripts/robot/c3po_preflight" 2>&1 || true)"
set_out="$(PATH="$preflight_stub:$PATH" FAKE_STATUS='{"confirm_configured":true}' \
    bash "$repo/scripts/robot/c3po_preflight" 2>&1 || true)"
check_contains "an unset confirm secret is a blocker" \
    "SAFETY_CONFIRM_SECRET IS NOT SET on the bridge" "$unset_out"
check_contains "a set confirm secret passes" "SAFETY_CONFIRM_SECRET is set —" "$set_out"
# The rest of the stack is absent in both runs, so the secret is the only difference in the count.
blockers_in() { printf '%s' "$1" | grep -oE '[0-9]+ blocker' | grep -oE '[0-9]+'; }
check "an unset confirm secret adds exactly one blocker" \
    "$(( $(blockers_in "$set_out") + 1 ))" "$(blockers_in "$unset_out")"

# Person rule off, geofence off and a standing stop warn, and block nothing (a
# decision about what should block is still open).
loose_out="$(PATH="$preflight_stub:$PATH" FAKE_STATUS='{"person_rule":false,"confirm_configured":true,"geofence":{"radius_m":null},"stop_standing":true}' \
    bash "$repo/scripts/robot/c3po_preflight" 2>&1 || true)"
tight_out="$(PATH="$preflight_stub:$PATH" FAKE_STATUS='{"person_rule":true,"confirm_configured":true,"geofence":{"radius_m":3.0},"stop_standing":false}' \
    bash "$repo/scripts/robot/c3po_preflight" 2>&1 || true)"
check_contains "a person rule that is off is a warning" "the person rule is OFF" "$loose_out"
check_contains "a geofence that is off is a warning" "the geofence is OFF" "$loose_out"
check_contains "a standing stop is a warning" "a PARAR is still standing" "$loose_out"
for needle in "the person rule is OFF" "the geofence is OFF" "a PARAR is still standing"; do
    check_not_contains "no '$needle' when it is not so" "$needle" "$tight_out"
done
warnings_in() { printf '%s' "$1" | grep -oE '[0-9]+ warning' | grep -oE '[0-9]+'; }
check "the three add three warnings" \
    "$(( $(warnings_in "$tight_out") + 3 ))" "$(warnings_in "$loose_out")"
check "and no blocker" "$(blockers_in "$tight_out")" "$(blockers_in "$loose_out")"
rm -rf "$preflight_stub"

# ---------------------------------------------------------------------------
# install_piper.sh: the voice is pinned by commit and by sha256
# ---------------------------------------------------------------------------
echo "== piper voice pin =="
# Against a fake server: curl serves $piper_tmp/server/<basename> and resumes by
# appending past the local size, as the real one does. sha256sum prints a file's
# first 64 bytes, so a served file is "good" when it starts with its pinned hash.
# uname says Linux and stat is GNU's -c%s, so this runs on the Mac too.
piper_script="$repo/apps/bridge/scripts/install_piper.sh"
piper_tmp="$(mktemp -d)"
mkdir -p "$piper_tmp/bin" "$piper_tmp/server" "$piper_tmp/dest"
# shellcheck disable=SC2016  # the $vars are for the stubs to expand, not us
{
    printf '#!/bin/sh\ncase "$1" in -s) echo Linux ;; *) echo x86_64 ;; esac\n' >"$piper_tmp/bin/uname"
    printf '#!/bin/sh\nprintf "%%s  %%s\\n" "$(head -c 64 "$1")" "$1"\n' >"$piper_tmp/bin/sha256sum"
    printf '#!/bin/sh\nwc -c <"$2" | tr -d " "\n' >"$piper_tmp/bin/stat"
    printf '#!/bin/sh\ncat >/dev/null\nhead -c 4000 /dev/zero\n' >"$piper_tmp/dest/piper"
    cat >"$piper_tmp/bin/curl" <<'STUB'
#!/bin/sh
resume=0 out="" url=""
while [ $# -gt 0 ]; do
    case "$1" in -C) resume=1; shift ;; -o) out="$2"; shift ;; http*) url="$1" ;; esac
    shift
done
echo "$resume $url" >>"$FAKE_CURL_LOG"
src="$FAKE_SERVER/${url##*/}"
[ -f "$src" ] || exit 22
have=0; [ "$resume" = 1 ] && [ -f "$out" ] && have="$(wc -c <"$out" | tr -d ' ')"
want="$(wc -c <"$src" | tr -d ' ')"
[ "$have" -eq 0 ] && { cat "$src" >"$out"; exit 0; }
[ "$have" -eq "$want" ] && exit 0  # curl: "already completely downloaded"
[ "$have" -gt "$want" ] && exit 33  # the server refuses the range
tail -c +"$((have + 1))" "$src" >>"$out"
STUB
}
chmod +x "$piper_tmp/bin/"* "$piper_tmp/dest/piper"
onnx_pin="$(sed -n 's/^VOICE_ONNX_SHA256=//p' "$piper_script")"
json_pin="$(sed -n 's/^VOICE_JSON_SHA256=//p' "$piper_script")"
serve() {  # serve <first 64 bytes> <file>
    { printf '%s\n' "$1"; head -c 60 /dev/zero | tr '\0' x; } >"$piper_tmp/server/$2"
}
serve "$onnx_pin" es_AR-daniela-high.onnx
serve "$json_pin" es_AR-daniela-high.onnx.json
served="$piper_tmp/server/es_AR-daniela-high.onnx"
installed="$piper_tmp/dest/es_AR-daniela-high.onnx"
good_onnx="$(cat "$served")"
run_piper() {
    : >"$piper_tmp/curl.log"
    PATH="$piper_tmp/bin:$PATH" PIPER_DEST="$piper_tmp/dest" FAKE_SERVER="$piper_tmp/server" \
        FAKE_CURL_LOG="$piper_tmp/curl.log" bash "$piper_script" >"$piper_tmp/out" 2>&1
    echo "exit $?"
}

check "each voice file has a sha256 pin" "64 64" "${#onnx_pin} ${#json_pin}"
check "a fresh install succeeds" "exit 0" "$(run_piper)"
check "and installs the served voice" "$good_onnx" "$(cat "$installed")"
check_not_contains "the voice is fetched by commit, not from main" "/resolve/main/" "$(cat "$piper_tmp/curl.log")"
check "a verified voice is not fetched again" "exit 0|" "$(run_piper)|$(cat "$piper_tmp/curl.log")"
# Right size, wrong bytes: what a resume onto another revision's file leaves,
# and what a size check calls complete.
{ printf z; tail -c +2 "$served"; } >"$installed"
check "a same-size file with the wrong hash is replaced" "exit 0|$good_onnx" "$(run_piper)|$(cat "$installed")"
head -c 40 "$served" >"$installed"
check "a truncated download is resumed, not fetched again" "exit 0|$good_onnx|1" \
    "$(run_piper)|$(cat "$installed")|$(cut -d' ' -f1 "$piper_tmp/curl.log")"
rm -f "$installed"
serve "0000" es_AR-daniela-high.onnx
check "a server that sends other bytes fails the install" "exit 1" "$(run_piper)"
check_contains "and says it is the hash" "sha256" "$(cat "$piper_tmp/out")"
rm -rf "$piper_tmp"

# ---------------------------------------------------------------------------
# .gitignore: only apps/back's compiled binary is called `server`
# ---------------------------------------------------------------------------
echo "== gitignore =="
# A source directory named server (SvelteKit's $lib/server) used to be ignored
# everywhere, so a file in it stayed off git without a word.
ignored() { git -C "$repo" check-ignore -q --no-index "$1" && echo ignored || echo tracked; }
if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    check "apps/back's compiled server binary is ignored" ignored "$(ignored apps/back/server)"
    for path in apps/web/src/lib/server/auth.ts apps/back/src/server/index.ts packages/x/server/a.ts; do
        check "a source file under $path is not ignored" tracked "$(ignored "$path")"
    done
else
    printf '  – skipped: not a git checkout\n'
fi

echo "== audit watch =="
watch_query="$("$repo/scripts/audit_watch.sh" --print)"
check_contains "the audit watch prints each row once, by id" "select id," "$watch_query"
check_contains "the audit watch shows whether a stop damped" \
    "real_damp_fallback_reason" "$watch_query"
check_not_contains "the audit watch only reads" "insert" "$watch_query"

echo "== quest port ownership =="
#
# THE FAILURE THIS SECTION EXISTS FOR, 2026-09-08.
#
# `quest_setup.sh` verified that something was LISTENING on 3001 and forwarded
# it to the headset. Two different strangers held that port in succession: a
# Vite left running for two weeks from another checkout, and a Next.js dev
# server from an unrelated project. Both answered a TCP connect, so both passed.
# The operator put the headset on and got somebody else's website, with a login
# form no account could satisfy -- and every check in the script said green.
#
# These tests exercise the real classifier against real listeners, rather than
# grepping the script for the word "cwd": the thing worth proving is that a
# process outside this repo is actually rejected.

quest_setup_src="$(cat "$repo/scripts/quest_setup.sh")"
check_contains "quest setup identifies who owns a local port" \
    "port_owner_cwd" "$quest_setup_src"
check_contains "quest setup fails on a port held from outside the checkout" \
    "held by something OUTSIDE this checkout" "$quest_setup_src"

# The classifier, lifted verbatim from the script so the test cannot drift from
# an implementation it only pretends to check.
_owner_pid() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }
_owner_cwd() { lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }
_classify() {
    local port="$1" pid cwd
    pid="$(_owner_pid "$port")"
    [ -n "$pid" ] || { printf 'nothing'; return; }
    cwd="$(_owner_cwd "$pid")"
    case "$cwd" in
        "$repo"|"$repo"/*) printf 'ours' ;;
        "")                printf 'unknown' ;;
        *)                 printf 'stranger' ;;
    esac
}

# Wait for a listener rather than sleeping a fixed time: on a slow CI runner
# python had not bound the port after 2 s, `_classify` read "nothing", and the
# test failed for a reason that had nothing to do with the classifier.
_await_listen() {
    local port="$1" _
    for _ in $(seq 1 50); do
        [ -n "$(_owner_pid "$port")" ] && return 0
        sleep 0.2
    done
    return 1
}

# A listener started from INSIDE the repo is ours. `exec`, so `$!` is the
# server itself and the `kill` below stops it rather than its wrapper shell.
( cd "$repo" && exec python3 -m http.server 39311 >/dev/null 2>&1 ) &
_ours_job=$!
disown "$_ours_job" 2>/dev/null || true
_await_listen 39311 || true
check "a port served from inside the checkout is accepted" "ours" "$(_classify 39311)"
kill "$_ours_job" 2>/dev/null || true

# A listener started from OUTSIDE it is not, however healthy it looks.
( cd /tmp && exec python3 -m http.server 39312 >/dev/null 2>&1 ) &
_them_job=$!
disown "$_them_job" 2>/dev/null || true
_await_listen 39312 || true
check "a port served from outside the checkout is rejected" "stranger" "$(_classify 39312)"
kill "$_them_job" 2>/dev/null || true

# And an unused port is neither -- "nothing is listening" is a different
# problem with a different message, and must not be reported as a stranger.
check "an unused port is not mistaken for a stranger" "nothing" "$(_classify 39313)"

wait 2>/dev/null || true

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
