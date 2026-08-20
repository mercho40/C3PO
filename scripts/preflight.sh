#!/usr/bin/env bash
#
# Check the whole chain, from this Mac to the robot's camera, and say in plain
# language what is wrong and what to do about it.
#
# This exists because of a specific problem: the operator wears the headset
# alone. Every failure in this stack is silent by design — the robot ignores
# what it cannot act on, an MJPEG <img> freezes rather than erroring, a
# forwarded port succeeds even when nothing is listening behind it. From inside
# a Quest, five different causes all look like "it does not work", and there is
# no terminal in there to tell them apart.
#
# So this is the terminal, run once before the headset goes on. Every check
# prints what it means and what to do, not just a status.
#
#   ./scripts/preflight.sh          # everything
#   ./scripts/preflight.sh --quick  # skip the robot, just this Mac
#
# It is READ-ONLY. It starts nothing, restarts nothing, and never moves the
# robot. Failing checks are advice, not actions.

set -uo pipefail   # deliberately NOT -e: a failing check must not end the run,
                   # because the later checks are how you tell causes apart.

_b=$'\033[1m'; _r=$'\033[31m'; _g=$'\033[32m'; _y=$'\033[33m'; _d=$'\033[2m'; _z=$'\033[0m'
ok()   { printf '  %s✓%s %s\n' "$_g" "$_z" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$_r" "$_z" "$1"; FAILED=$((FAILED+1)); }
warn() { printf '  %s!%s %s\n' "$_y" "$_z" "$1"; WARNED=$((WARNED+1)); }
note() { printf '      %s%s%s\n' "$_d" "$1" "$_z"; }
head_() { printf '\n%s%s%s\n' "$_b" "$1" "$_z"; }

FAILED=0
WARNED=0
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

CAM_URL="${PUBLIC_ROBOT_CAM_URL:-http://127.0.0.1:8081}"

# ---------------------------------------------------------------- this mac ---
head_ "1. This machine"

for entry in "3000:apps/back API" "3001:web console (vite)"; do
    port="${entry%%:*}"; label="${entry#*:}"
    # Both stacks: Vite binds `localhost`, which on macOS resolves to ::1 and
    # NOT 127.0.0.1, so an IPv4-only check calls a healthy dev server dead.
    if nc -z 127.0.0.1 "$port" 2>/dev/null || nc -z ::1 "$port" 2>/dev/null; then
        ok "$port  $label"
    else
        bad "$port  $label — not listening"
        note "bun run dev   (starts both 3000 and 3001)"
    fi
done

# Only meaningful if something is actually listening. Reporting "it is not
# answering" about a port nothing has bound sends you looking for a crash that
# did not happen — found by running this script with the stack down.
if nc -z 127.0.0.1 3000 2>/dev/null || nc -z ::1 3000 2>/dev/null; then
    if curl -fsS --max-time 4 http://127.0.0.1:3000/health >/dev/null 2>&1; then
        ok "the API answers /health"
    else
        bad "3000 is open but /health does not answer"
        note "it bound the port and then failed — look at what it printed at boot"
    fi
fi

# ------------------------------------------------------------------ tunnel ---
head_ "2. The tunnel to the robot"

if [ "$QUICK" = "1" ]; then
    warn "skipped (--quick)"
else
    for entry in "8767:teleop stream:fatal" "8081:camera MJPEG:warn"; do
        port="${entry%%:*}"; rest="${entry#*:}"
        label="${rest%:*}"; sev="${rest##*:}"
        if nc -z 127.0.0.1 "$port" 2>/dev/null; then
            ok "$port  $label — forwarded and something is listening"
        elif [ "$sev" = "fatal" ]; then
            bad "$port  $label — nothing here"
            note "the tunnel is not up, or run_teleop is not running on the robot:"
            note "  ssh -N -o ControlMaster=no -L 8001:127.0.0.1:8001 \\"
            note "      -L 8081:127.0.0.1:8081 -L 8767:127.0.0.1:8767 c3po"
        else
            warn "$port  $label — nothing here (no camera picture without it)"
            note "add -L 8081:127.0.0.1:8081 to the tunnel, and run perception_up perception"
        fi
    done
fi

# ------------------------------------------------------------------ camera ---
head_ "3. The camera, end to end"

if [ "$QUICK" = "1" ]; then
    warn "skipped (--quick)"
else
    # The three failures below are indistinguishable from inside the headset,
    # and were confused for each other across two sessions. curl separates them
    # because it distinguishes refused from reset from answered.
    status_body=$(curl -sS --max-time 5 "$CAM_URL/status" 2>&1)
    status_rc=$?

    if [ "$status_rc" -ne 0 ]; then
        case "$status_body" in
            *"Connection refused"*|*"Couldn't connect to server"*|*"Failed to connect"*)
                bad "nothing is forwarding $CAM_URL"
                note "the SSH tunnel is missing -L 8081. This is a tunnel problem, not a robot one."
                ;;
            *"reset by peer"*|*"Empty reply"*|*"Recv failure"*)
                bad "the tunnel reaches the robot, and nothing is listening on 8081 there"
                note "the vision container is not up. On the robot:  perception_up perception"
                note "This is the failure that looked like a camera fault last time. It is not."
                ;;
            *"timed out"*|*"Operation timed out"*)
                bad "$CAM_URL timed out"
                note "the tunnel is wedged. Kill the ssh process and open it again."
                ;;
            *)
                bad "could not reach $CAM_URL"
                note "$status_body"
                ;;
        esac
    else
        ok "the camera server answers /status"
        case "$status_body" in
            *'"live":true'*|*'"live": true'*)
                ok "and it says it is LIVE — there are frames to show"
                ;;
            *'"live"'*)
                warn "but it says live: false — the server is up, the D435i is not producing"
                note "this is the camera itself, not the tunnel and not the web app"
                note "$status_body"
                ;;
            *)
                warn "the reply does not look like the expected /status shape"
                note "$status_body"
                ;;
        esac

        code=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "$CAM_URL/stream.mjpg" 2>/dev/null)
        if [ "$code" = "200" ]; then
            ok "the stream itself returns 200 — the whole chain to this Mac works"
            note "if the headset still shows nothing after this, it is the renderer, not the feed"
        else
            bad "/status works but the stream returned $code"
            note "the server is up but not serving frames — restart perception_up perception"
        fi
    fi
fi

# ------------------------------------------------------------------ headset ---
head_ "4. The headset"

if ! command -v adb >/dev/null 2>&1; then
    bad "adb is not installed"
    note "brew install --cask android-platform-tools"
else
    devices=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device" {print $1}')
    unauth=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="unauthorized" {print $1}')
    if [ -n "$devices" ]; then
        ok "headset connected and authorised"
        forwarded=$(adb reverse --list 2>/dev/null)
        for port in 3000 3001 8081 8767; do
            case "$forwarded" in
                *"tcp:$port"*) ok "  quest localhost:$port is forwarded" ;;
                *) warn "  quest localhost:$port is NOT forwarded — run ./scripts/quest_setup.sh" ;;
            esac
        done
    elif [ -n "$unauth" ]; then
        bad "headset plugged in but NOT authorised"
        note "there is an 'Allow USB debugging?' prompt INSIDE the headset. Put it on and accept it."
        note "This is the single easiest thing to miss, and it looks exactly like a bad cable."
    else
        warn "no headset on USB (fine if you have not plugged it in yet)"
    fi
fi

# -------------------------------------------------------------------- estop ---
head_ "5. Is a stop still standing?"

RUN_DIR="${C3PO_RUN_DIR:-$HOME/.c3po/run}"
if [ -f "$RUN_DIR/stop_everything" ]; then
    stop_at=$(stat -f %m "$RUN_DIR/stop_everything" 2>/dev/null || echo 0)
    ack_at=0
    [ -f "$RUN_DIR/stop_acknowledged" ] && ack_at=$(stat -f %m "$RUN_DIR/stop_acknowledged" 2>/dev/null || echo 0)
    if [ "$stop_at" -gt "$ack_at" ]; then
        warn "an emergency stop is recorded and has not been cleared"
        note "this is not broken — a stop deliberately outlives the session it was pressed in."
        note "It clears itself once you connect: hold the dead-man RELEASED for one full second."
        note "Pressed at: $(date -r "$stop_at" '+%H:%M:%S on %d %b')"
    else
        ok "no stop outstanding"
    fi
else
    ok "no stop outstanding"
fi

# ------------------------------------------------------------------ verdict ---
head_ "Verdict"
if [ "$FAILED" -eq 0 ] && [ "$WARNED" -eq 0 ]; then
    printf '  %s✓ everything checked out. Put the headset on.%s\n\n' "$_g" "$_z"
elif [ "$FAILED" -eq 0 ]; then
    printf '  %s! %d warning(s), nothing fatal.%s You can drive; read them first.\n\n' "$_y" "$WARNED" "$_z"
else
    printf '  %s✗ %d thing(s) will stop you.%s Fix those before the headset goes on —\n' "$_r" "$FAILED" "$_z"
    printf '    from inside it they all look the same, and you cannot debug from in there.\n\n'
fi
exit 0
