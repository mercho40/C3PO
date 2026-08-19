#!/usr/bin/env bash
# Put the stack controls on PATH — run_c3po / stop_c3po / run_gemm / stop_gemm /
# perception_up / build_perception / measure.sh, plus the per-session VR
# sidecars run_teleop / stop_teleop — and bound ~/.c3po/logs.
#
# Symlinks rather than copies, so `git pull` in the checkout updates the
# commands with no reinstall step. Run this on the robot.
#
# Unprivileged by design: everything it needs lives under $HOME. The one
# exception is the logrotate drop-in at the end, which is optional, asks for
# sudo only if it has something to do, and never fails the install.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$target"

# perception_up, build_perception and measure.sh are on this list because the
# plan invokes them by bare name over ssh (`ssh c3po 'perception_up fake'`), and
# because each already resolves _common.sh through `readlink -f "$BASH_SOURCE"`
# specifically so it can be reached through one of these symlinks. measure.sh
# keeps its extension so the command matches the filename the plan names.
# run_teleop / stop_teleop are per-session VR sidecar controls, reached the same
# way.
for cmd in run_c3po stop_c3po run_gemm stop_gemm perception_up build_perception measure.sh run_teleop stop_teleop; do
    chmod +x "$here/$cmd"
    ln -sf "$here/$cmd" "$target/$cmd"
    echo "  linked $target/$cmd -> $here/$cmd"
done
chmod +x "$here/_common.sh"

# --- logrotate --------------------------------------------------------------
#
# ~/.c3po/logs has never been bounded. bridge.log grows for as long as the
# bridge runs, and perception adds a Cyclone trace file per container plus the
# build logs. This robot has one filesystem, and filling it takes the bridge —
# which is the process that owns stop_everything.
#
# COPIED, NOT SYMLINKED, unlike everything above. logrotate refuses config files
# in an include directory that are not owned by root, and it refuses them from
# the daily timer where nobody reads the complaint — so a symlink into a
# checkout owned by `unitree` would look installed and silently never run. The
# cost is that `git pull` does NOT update it: re-run this script, which reports
# below whenever the installed copy has drifted from the checkout.
#
# Set SKIP_LOGROTATE=1 to leave /etc alone entirely.
src="$here/c3po-logs.logrotate"
dest="/etc/logrotate.d/c3po"

echo
if [ "${SKIP_LOGROTATE:-0}" = "1" ]; then
    echo "  logrotate: skipped (SKIP_LOGROTATE=1) — ~/.c3po/logs stays unbounded"
elif [ ! -f "$src" ]; then
    echo "  logrotate: $src missing — skipped"
elif [ "$HOME" != "/home/unitree" ]; then
    # The config hardcodes /home/unitree because root's logrotate cannot expand
    # $HOME, and its `su unitree unitree` line would be wrong for anyone else.
    # Same rule install_boot_unit.sh applies to the unit file: refuse rather
    # than install something that silently points at the wrong home.
    echo "  logrotate: HOME is $HOME, but $(basename "$src") hardcodes /home/unitree"
    echo "             skipped — edit the paths and the su line first"
elif [ -f "$dest" ] && cmp -s "$src" "$dest"; then
    echo "  logrotate: $dest already current"
else
    [ -f "$dest" ] && echo "  logrotate: $dest differs from the checkout — reinstalling"
    if sudo install -o root -g root -m 0644 "$src" "$dest"; then
        echo "  installed $dest (copy of $src)"
        # Parse it now rather than discovering a typo at 06:25 tomorrow in a
        # cron mail nobody on this robot reads. --debug implies a dry run, so
        # this rotates nothing.
        if sudo logrotate --debug "$dest" >/dev/null 2>&1; then
            echo "  logrotate parses it (dry run clean)"
        else
            echo "  WARNING: logrotate could not parse $dest — check it:"
            echo "    sudo logrotate --debug $dest"
        fi
    else
        echo "  WARNING: could not write $dest (no sudo?). ~/.c3po/logs stays unbounded."
        echo "    sudo install -o root -g root -m 0644 $src $dest"
    fi
fi

case ":$PATH:" in
    *":$target:"*)
        echo
        echo "Ready. Try: run_c3po"
        ;;
    *)
        echo
        echo "$target is not on your PATH. Add it:"
        echo "  echo 'export PATH=\"$target:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
        ;;
esac
