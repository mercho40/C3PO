#!/usr/bin/env bash
# Install the whole C3PO stack as systemd units. Run ON the robot, needs sudo.
#
#     ./scripts/robot/install_stack.sh
#
# Replaces the manual bring-up — `run_c3po` in one terminal, `perception_up` in
# another, a `ros2 service call` to activate Nav2, an ssh tunnel somewhere else —
# with units that survive a reboot and a dropped SSH session.
#
# WHAT THIS DOES *NOT* DO, DELIBERATELY:
#
#   * It does not enable a sensor-claiming perception stage. `nav2` and
#     `perception` take the Livox AND the RealSense from the other team, and
#     enabling either at boot would take them again on every power cycle,
#     including reboots nobody intended. Enable those by hand, inside an agreed
#     window. Only `nav2-fake` — which claims nothing — is offered here.
#
#   * It does not autostart Nav2's lifecycle. `autostart: false` in
#     nav2_params.yaml is a safety decision, not an oversight: container start
#     must never be the same event as "the robot is ready to be driven".
#
#   * It does not arm anything. Nothing installed here can open the cmd_vel
#     gate; that stays a deliberate, expiring, logged action.
#
# Symlinks rather than copies, matching install_boot_unit.sh: `git pull` then
# updates the units and only a daemon-reload is needed.

set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

if [ "$here" != "/home/unitree/c3po/scripts/robot" ]; then
    echo "install_stack: checkout is at $here, but the units hardcode" >&2
    echo "  /home/unitree/c3po — systemd cannot expand \$HOME. Edit them first." >&2
    exit 1
fi

UNITS="c3po-bridge.service c3po-perception@.service c3po-health.service c3po-health.timer"

for unit in $UNITS; do
    [ -f "$here/$unit" ] || { echo "install_stack: missing $here/$unit" >&2; exit 1; }
done

echo "==> linking units into /etc/systemd/system"
for unit in $UNITS; do
    sudo ln -sf "$here/$unit" "/etc/systemd/system/$unit"
    echo "    $unit"
done

sudo systemctl daemon-reload

echo "==> enabling the bridge (it owns stop_everything — it should always be up)"
sudo systemctl enable c3po-bridge.service

echo "==> enabling the health timer"
sudo systemctl enable c3po-health.timer

# logrotate: the bridge logs every tool call, and this robot has been left
# running for days. Without this the log grows until somebody notices.
if [ -f "$here/c3po-logs.logrotate" ]; then
    echo "==> installing logrotate config"
    sudo ln -sf "$here/c3po-logs.logrotate" /etc/logrotate.d/c3po
fi

cat <<'EOF'

==> installed. Nothing has been started or armed.

Start the bridge now:            sudo systemctl start c3po-bridge
Persistent sensor-free stack:    sudo systemctl enable --now c3po-perception@nav2-fake
Check the whole stack:           c3po_health

For a sensor window (claims the Livox AND the RealSense from gemm — agree it
first, and hand them back after):

    sudo systemctl start c3po-perception@nav2
    ...
    sudo systemctl stop  c3po-perception@nav2

Nav2 still comes up UNCONFIGURED and the cmd_vel gate is still closed. Making
the robot drivable remains two deliberate steps, and that is the design:

    ros2 service call /lifecycle_manager_navigation/manage_nodes \
        nav2_msgs/srv/ManageLifecycleNodes "{command: 0}"
    arm_navigation(reason="...", seconds=30)      # via MCP, supervised
EOF
