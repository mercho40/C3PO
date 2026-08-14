#!/usr/bin/env bash
# Install the boot unit so the bridge survives a robot reboot. Run on the robot.
#
# Symlinks rather than copies, matching install_robot_scripts.sh: `git pull`
# then updates the unit, and only a `systemctl daemon-reload` is needed.
#
# Needs sudo — unlike install_robot_scripts.sh, this writes under /etc.

set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

unit="c3po-bridge.service"
target="/etc/systemd/system/$unit"

if [ ! -f "$here/$unit" ]; then
    echo "install_boot_unit: $here/$unit missing" >&2
    exit 1
fi

# The unit hardcodes /home/unitree paths, because a systemd unit cannot expand
# $HOME. Refuse rather than install something that silently points elsewhere.
if [ "$here" != "/home/unitree/c3po/scripts/robot" ]; then
    echo "install_boot_unit: checkout is at $here," >&2
    echo "  but $unit hardcodes /home/unitree/c3po — edit the unit first." >&2
    exit 1
fi

sudo ln -sf "$here/$unit" "$target"
echo "  linked $target -> $here/$unit"

sudo systemctl daemon-reload
echo "  daemon-reload done"

sudo systemctl enable "$unit" >/dev/null 2>&1
echo "  enabled (will start on boot)"

echo
echo "Not started. To start it now:   sudo systemctl start $unit"
echo "To check:                       systemctl status $unit"
echo
echo "Note it starts with C3PO_NO_TAKEOVER=1 — it brings the bridge up but"
echo "does NOT stop the gemm stack. Use run_c3po to actually take the robot."
