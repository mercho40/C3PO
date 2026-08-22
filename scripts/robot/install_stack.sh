#!/usr/bin/env bash
# The one robot installer. It installs one operator CLI and one systemd unit:
# the bridge. Perception remains foreground-only and can never start at boot.

set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
root="/home/unitree/c3po/scripts/robot"
bin_dir="${BIN_DIR:-$HOME/.local/bin}"

if [ "$here" != "$root" ]; then
    echo "install_stack: checkout is at $here, but the unit hardcodes $root" >&2
    echo "move the checkout to /home/unitree/c3po or edit the unit first" >&2
    exit 1
fi

unit="c3po-bridge.service"
implementations="c3po run_c3po stop_c3po run_gemm stop_gemm perception_up
    stop_perception build_perception measure.sh run_teleop stop_teleop
    c3po_health c3po_preflight take_camera bridge_sync _common.sh"
for file in $implementations; do
    [ -x "$here/$file" ] || {
        echo "install_stack: missing or non-executable $here/$file" >&2
        exit 1
    }
    bash -n "$here/$file"
done
for file in "$unit" c3po-logs.logrotate c3po.sudoers; do
    [ -f "$here/$file" ] || {
        echo "install_stack: missing $here/$file" >&2
        exit 1
    }
done
for command in logrotate visudo systemd-analyze; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "install_stack: $command is not installed" >&2
        exit 1
    }
done

# Validate every privileged artifact before changing live systemd state. Staging
# under /run also means PID 1 never parses a unit from the writable checkout.
stage="$(sudo mktemp -d /run/c3po-install.XXXXXX)"
cleanup() { sudo rm -rf "$stage"; }
trap cleanup EXIT
sudo install -o root -g root -m 0644 "$here/$unit" "$stage/$unit"
sudo install -o root -g root -m 0644 "$here/c3po-logs.logrotate" "$stage/c3po.logrotate"
sudo install -o root -g root -m 0440 "$here/c3po.sudoers" "$stage/c3po.sudoers"
sudo systemd-analyze verify "$stage/$unit" >/dev/null
sudo logrotate --debug "$stage/c3po.logrotate" >/dev/null
sudo visudo -cf "$stage/c3po.sudoers" >/dev/null

# Preserve exactly the currently loaded bridge unit for a one-unit rollback.
# -L dereferences the legacy checkout symlink; the backup itself is root-owned.
sudo install -d -o root -g root -m 0755 /var/lib/c3po
if sudo test -e "/etc/systemd/system/$unit"; then
    sudo cp -L "/etc/systemd/system/$unit" "/var/lib/c3po/previous-$unit"
    sudo chown root:root "/var/lib/c3po/previous-$unit"
    sudo chmod 0644 "/var/lib/c3po/previous-$unit"
fi

# Retire the old automatic perception/repair layer. Stop the timer and any
# already-triggered repair job, then refuse to remove a live perception unit:
# releasing its containers remains an explicit operator decision.
health_timer_state="$(systemctl show --property=LoadState --value c3po-health.timer 2>/dev/null || true)"
if [ "$health_timer_state" = "loaded" ]; then
    sudo systemctl disable --now c3po-health.timer >/dev/null
fi
health_service_state="$(systemctl show --property=LoadState --value c3po-health.service 2>/dev/null || true)"
if [ "$health_service_state" = "loaded" ]; then
    sudo systemctl stop c3po-health.service
fi
legacy_live="$(systemctl list-units --all --plain --no-legend 'c3po-perception@*.service' 2>/dev/null \
    | awk '$3 == "active" || $3 == "activating" || $3 == "deactivating" || $3 == "reloading" { print $1 }')"
if [ -n "$legacy_live" ]; then
    echo "install_stack: legacy perception unit still live: $legacy_live" >&2
    echo "run ./scripts/robot/stop_perception, then install again" >&2
    exit 1
fi
legacy_enabled="$(systemctl list-unit-files --state=enabled --no-legend 'c3po-perception@*.service' 2>/dev/null \
    | awk '{print $1}')"
if [ -n "$legacy_enabled" ]; then
    # shellcheck disable=SC2086 # one or more exact unit names from systemctl
    sudo systemctl disable $legacy_enabled >/dev/null
fi

# Commit the prevalidated files. The staged-then-mv unit replacement removes a
# legacy symlink rather than following it back into the writable checkout.
sudo install -o root -g root -m 0644 "$stage/$unit" "/etc/systemd/system/.$unit.c3po-new"
sudo mv -f "/etc/systemd/system/.$unit.c3po-new" "/etc/systemd/system/$unit"
sudo install -o root -g root -m 0644 "$stage/c3po.logrotate" /etc/logrotate.d/c3po
sudo install -o root -g root -m 0440 "$stage/c3po.sudoers" /etc/sudoers.d/c3po
sudo rm -f /etc/sudoers.d/c3po-camera \
    /etc/systemd/system/c3po-health.service \
    /etc/systemd/system/c3po-health.timer \
    '/etc/systemd/system/c3po-perception@.service'

# One command on PATH. Remove only legacy symlinks that point into this checkout.
mkdir -p "$bin_dir"
ln -sf "$here/c3po" "$bin_dir/c3po"
echo "  linked $bin_dir/c3po"
legacy="run_c3po stop_c3po run_gemm stop_gemm perception_up stop_perception build_perception measure.sh run_teleop stop_teleop c3po_health c3po_preflight take_camera"
for name in $legacy; do
    link="$bin_dir/$name"
    if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$here/$name" ]; then
        rm "$link"
        echo "  removed legacy PATH link $link"
    fi
done

sudo systemctl daemon-reload
sudo systemctl enable c3po-bridge.service >/dev/null

cat <<'EOF'

Installed. Nothing was started, no sensor was claimed, and motion is not armed.

  c3po start                         # take ownership and ensure bridge is up
  c3po status                        # read-only health
  c3po perception up nav2-fake      # explicit synthetic stage; not persistent
  c3po preflight                     # required before any supervised arm

Perception has no boot unit or repair timer. Every stage remains an explicit
operator action. Nav2 lifecycle and the bridge motion gate remain closed.
EOF
