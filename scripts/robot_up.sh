#!/usr/bin/env bash
# Bring the G1's side up from the Mac in one go: deploy, bridge, perception, the BrainCo
# hand server and the headset stream. Then run scripts/robot_session.sh (tunnel + console).
#
#   ./scripts/robot_up.sh [ssh-host]          # default unitree@192.168.123.164
#   ./scripts/robot_up.sh --print [ssh-host]  # the steps, nothing run
#
# Written after 2026-11-13, when each of these was a separate manual step after every reboot.
# It never edits gemm's files and never stops their containers: if a gemm-* container runs,
# it stops and says so, because two commanders on one robot is a decision for a person.
# The hand server needs root: macOS asks for the robot's sudo password in a dialog.
set -euo pipefail

print=0
if [ "${1:-}" = "--print" ]; then print=1; shift; fi
host="${1:-unitree@192.168.123.164}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
branch="${C3PO_DEPLOY_BRANCH:-feat/autonomy}"

steps=(
    "deploy   git push $branch to the robot's 'incoming', then git merge --ff-only there"
    "gemm     no gemm-* container may be running (stops here if one is)"
    "bridge   sudo -n systemctl restart c3po-bridge.service, wait for :8001"
    "vision   c3po perception up perception"
    "hands    brainco_hand_server (sudo password dialog) when TELEOP_HAND_TYPE=brainco"
    "headset  run_teleop on loopback, with TELEOP_STREAM_ARM/HAND_ENABLED=0 added to .env if unset"
)
if [ "$print" = 1 ]; then
    printf '%s\n' "${steps[@]}"
    exit 0
fi

r() { ssh -o ConnectTimeout=8 -o LogLevel=ERROR "$host" "$@"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

step "deploy $branch -> $host"
r true || die "cannot ssh to $host: is the cable in and en11 on 192.168.123.99?"
git -C "$repo" push -q "ssh://$host/home/unitree/c3po" "$branch:refs/heads/incoming"
r 'cd ~/c3po && git merge -q --ff-only incoming && git log --oneline -1' \
    || die "the robot's tree did not fast-forward: look at 'git status' on the robot"

step "gemm"
running="$(r 'docker ps --format "{{.Names}}" | grep -c "^gemm-" || true')"
[ "$running" = 0 ] || die "$running gemm-* containers are running: decide who drives before going on"
echo "✓ none running"

step "bridge"
r 'sudo -n systemctl restart c3po-bridge.service
   for _ in $(seq 40); do curl -s -o /dev/null -m 2 http://127.0.0.1:8001/safety/status && exit 0; sleep 1; done
   exit 1' || die "the bridge does not answer on :8001 (journalctl -u c3po-bridge.service)"
echo "✓ answering on :8001"

step "perception"
r 'cd ~/c3po && ./scripts/robot/c3po perception up perception 2>&1 | tail -3'

step "hands"
hand_type="$(r "grep -E '^TELEOP_HAND_TYPE=' ~/c3po/apps/bridge/.env | cut -d= -f2" || true)"
if [ "$hand_type" != "brainco" ]; then
    echo "– TELEOP_HAND_TYPE=${hand_type:-unset}: no hand server needed"
elif r "pgrep -f '[b]rainco_hand_server' >/dev/null"; then
    echo "✓ hand server already running"
else
    pw="$(osascript -e 'text returned of (display dialog "sudo password for unitree on the G1 (BrainCo hand server)" default answer "" with hidden answer with title "C3PO")')" \
        || die "no password given: the hand server is not running, set_hand will fail honestly"
    # Judge by the process, not the log: a redirected C++ stdout is block-buffered, so an empty
    # log is normal, and a grep miss here used to end the script with no message. `pgrep` without
    # -f matches the process name only, so it cannot match this ssh command's own text.
    printf '%s\n' "$pw" | r 'cd "$HOME/gemm_ai/xr_teleoperate/vendor/brainco_hand_service/bin" &&
        sudo -S -p "" sh -c "nohup ./brainco_hand_server --network_interface eth0 > /tmp/c3po-brainco-server.log 2>&1 < /dev/null &" &&
        sleep 8 && tail -4 /tmp/c3po-brainco-server.log; pgrep brainco_hand >/dev/null' \
        || die "the hand server is not running (wrong password? /tmp/c3po-brainco-server.log on the robot)"
    echo "✓ hand server running"
fi

step "headset stream"
r 'cd ~/c3po/apps/bridge
   grep -q "^TELEOP_STREAM_ARM_ENABLED=" .env || echo "TELEOP_STREAM_ARM_ENABLED=0" >> .env
   grep -q "^TELEOP_STREAM_HAND_ENABLED=" .env || echo "TELEOP_STREAM_HAND_ENABLED=0" >> .env
   cd ~/c3po && ./scripts/robot/run_teleop 2>&1 | tail -2 || true'
r 'ss -tln | grep -q "127.0.0.1:8767"' && echo "✓ listening on 127.0.0.1:8767" \
    || die "the headset stream is not listening (~/.c3po/logs/teleop.log)"

step "summary"
r 'cd ~/c3po/apps/bridge && grep -E "^(C3PO_ALLOW_PICK|C3PO_STT|C3PO_FACE_GREET|TELEOP_HAND_SIDES)=" .env'
echo
echo "Next on the Mac: ./scripts/robot_session.sh $host   (tunnel + console on http://localhost:3001)"
echo "Then: bash scripts/quest_reverse.sh, and «Caras» → «Escuchar después de saludar» on."
