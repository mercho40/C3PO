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
#   * `install_robot_scripts.sh`'s new checker flagged four scripts that were on
#     its own list, because `case` does no word splitting across newlines.
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
# who holds the camera
# ---------------------------------------------------------------------------
#
# The one that has been wrong in three different scripts. `filter_video_holders`
# is pure — it reads a process table on stdin — precisely so it can be tested
# against a fixture instead of against a robot.
echo "== camera holder detection =="

# shellcheck source=../robot/_common.sh
C3PO_DIR="$repo" source "$repo/scripts/robot/_common.sh"

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
# the PATH install list
# ---------------------------------------------------------------------------
#
# This checker shipped broken and reported four scripts that were on its list.
echo "== install_robot_scripts =="
tmpbin="$(mktemp -d)"
out="$(BIN_DIR="$tmpbin" SKIP_LOGROTATE=1 bash "$repo/scripts/robot/install_robot_scripts.sh" 2>&1)"
check_not_contains "a clean checkout produces no unlisted-script NOTE" "NOTE:" "$out"
check_contains "take_camera is linked onto PATH" "take_camera" "$out"

# ...and it must still fire, or it is decoration.
touch "$repo/scripts/robot/zz_test_unlisted"
out="$(BIN_DIR="$tmpbin" SKIP_LOGROTATE=1 bash "$repo/scripts/robot/install_robot_scripts.sh" 2>&1)"
rm -f "$repo/scripts/robot/zz_test_unlisted"
check_contains "an unlisted script IS reported" "zz_test_unlisted" "$out"
rm -rf "$tmpbin"

# ---------------------------------------------------------------------------
# shellcheck, when it is available
# ---------------------------------------------------------------------------
#
# Not a hard requirement: it is not installed on the robot and this suite has to
# run there. Skipped loudly rather than silently, so "0 findings" is never
# confused with "never ran".
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
