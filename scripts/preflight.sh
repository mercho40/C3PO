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

# Read the camera URL the WEB APP is configured with, not whatever happens to
# be in this shell. Testing 127.0.0.1:8081 while apps/web/.env points somewhere
# else (or nowhere — .env.example ships this key empty) gives the camera a full
# green while the page renders "PUBLIC_ROBOT_CAM_URL no está configurado".
WEB_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/apps/web/.env"
CAM_URL=""
if [ -f "$WEB_ENV" ]; then
    CAM_URL=$(grep -E '^[[:space:]]*PUBLIC_ROBOT_CAM_URL=' "$WEB_ENV" 2>/dev/null \
              | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r' | xargs 2>/dev/null || true)
fi
CAM_URL_SOURCE="apps/web/.env"
if [ -z "$CAM_URL" ]; then
    CAM_URL="${PUBLIC_ROBOT_CAM_URL:-}"
    CAM_URL_SOURCE="the shell environment"
fi

# `ssh -L` binds its local listener at SETUP time, before it knows anything
# about the far end. So a plain connect() to a forwarded port SUCCEEDS whenever
# the ssh process is alive, whatever is or is not running on the robot — which
# makes `nc -z` useless here, and actively harmful: it is how a forgotten
# run_teleop earns a green tick.
#
# Forcing a byte through the channel is what tells them apart. ssh opens the
# channel, finds nothing listening, and drops the already-accepted local socket
# — so the client sees a reset or an empty reply, never a refusal.
#
#   nothing  no listener at all — the tunnel is not up
#   empty    tunnel is up, nothing is listening on the robot
#   alive    something answered
probe_tunnel() {
    local port="$1" out rc
    out=$(curl -sS --max-time 4 -o /dev/null "http://127.0.0.1:$port/" 2>&1); rc=$?
    if [ "$rc" -eq 0 ]; then echo alive; return; fi
    case "$out" in
        *"Connection refused"*|*"Couldn't connect to server"*|*"Failed to connect"*) echo nothing ;;
        *"reset by peer"*|*"Empty reply"*|*"Recv failure"*|*"closed connection"*)     echo empty ;;
        *"timed out"*|*"Operation timed out"*)                                        echo timeout ;;
        # Any HTTP-level complaint means something spoke to us.
        *) echo alive ;;
    esac
}

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
    for entry in "8767:teleop stream:fatal:run_teleop" \
                 "8001:bridge MCP:fatal:run_c3po" \
                 "8081:camera MJPEG:warn:perception_up perception"; do
        port="${entry%%:*}"; rest="${entry#*:}"
        label="${rest%%:*}"; rest="${rest#*:}"
        sev="${rest%%:*}"; starter="${rest#*:}"
        case "$(probe_tunnel "$port")" in
            alive)
                ok "$port  $label — answering"
                ;;
            empty)
                # The distinction this whole function exists for.
                if [ "$sev" = "fatal" ]; then
                    bad "$port  $label — the tunnel works, nothing is running on the robot"
                else
                    warn "$port  $label — tunnel works, nothing running on the robot"
                fi
                note "on the robot:  $starter"
                note "the forward is fine — do NOT go looking at the tunnel for this one"
                ;;
            timeout)
                bad "$port  $label — timed out (the tunnel is wedged)"
                note "kill the ssh process and open it again"
                ;;
            *)
                if [ "$sev" = "fatal" ]; then
                    bad "$port  $label — not forwarded at all"
                else
                    warn "$port  $label — not forwarded (no camera picture without it)"
                fi
                note "ssh -N -o ControlMaster=no \\"
                note "    -L 8001:127.0.0.1:8001 -L 8081:127.0.0.1:8081 \\"
                note "    -L 8767:127.0.0.1:8767 c3po"
                note "ControlMaster=no matters: a forward on a shared master evaporates"
                note "when the master idles out, mid-session, with no obvious cause."
                ;;
        esac
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
