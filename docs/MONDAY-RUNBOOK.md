# Monday robot validation

Run this in order on the first supervised hardware window. The order keeps read-only and
non-motion checks ahead of sensor takeover and motion. Record the command output and stop
at the first failed gate; do not "try the next thing" around a safety refusal.

## People and stop conditions

Before connecting:

- One operator is beside the G1 with the physical e-stop in reach.
- The floor is clear and the robot starts standing still.
- The other team has agreed to the sensor/build window before `c3po gemm stop`,
  `c3po camera take`, `c3po perception up perception`, or an eight-core image build.
- Nobody enables `TELEOP_ARM_ENABLED` until `arm_sign_check.py` has completed.
- Any unexpected motion, second commander, unknown gate state, missing physical e-stop, or
  failed health check ends the run. Use the physical e-stop first if motion is unexpected.

Create one log for the session:

```bash
ssh c3po
mkdir -p ~/.c3po/logs
exec > >(tee -a ~/.c3po/logs/monday-validation.log) 2>&1
```

## 1. Baseline: observe only

```bash
cd ~/c3po
git status --short --branch
./scripts/robot/c3po status   # path form: the simplified CLI is not installed yet
docker ps
pgrep -af 'cmd_vel_to_loco|xr_teleoperate|brainco_hand_server|unitree_slam' || true
curl -sS http://127.0.0.1:8001/telemetry/gate
curl -sS http://127.0.0.1:8001/camera/status
```

Pass only if the bridge answers, the gate is closed, no unexpected locomotion commander is
running, and the camera status is valid JSON. Do not start a second bridge to fix a failed
health check.

## 2. Prove the non-invasive shims

These commands claim no sensor and command no motion:

```bash
./scripts/robot/c3po perception build vision --dry-run
./scripts/robot/c3po perception measure idle 90
```

Pass if the dry run prints the intended Docker argv and the measurement verdict table has
real values rather than `UNKNOWN`.

## 3. Install the simplified stack

Record the pre-deploy commit before pulling. The software e-stop disappears briefly during
the restart, so keep the physical e-stop in reach and do this before any motion test.

```bash
cd ~/c3po
printf 'previous=%s\n' "$(git rev-parse HEAD)" | tee ~/.c3po/previous-deploy
./scripts/robot/c3po install
c3po restart
systemctl is-active c3po-bridge
curl -sS http://127.0.0.1:8001/telemetry/gate
c3po status
docker ps
sudo systemctl stop c3po-bridge
docker ps
sudo systemctl start c3po-bridge
c3po restart   # proves systemd's MainPID owns 127.0.0.1:8001
curl -sS http://127.0.0.1:8001/telemetry/gate
docker ps
```

Pass if both starts become `active`, the gate reports closed, `c3po status` reads the
systemd-owned PID, and stopping the bridge never stops perception containers. Also confirm
that `~/.local/bin` exposes `c3po` rather than the old lifecycle command set.

If it fails, restore the prior checkout and the exact bridge unit that the new installer
backed up. Do **not** run the old full-stack installer: it would restore the retired
perception unit and repair timer.

```bash
previous="$(cut -d= -f2 ~/.c3po/previous-deploy)"
git switch --detach "$previous"
sudo install -o root -g root -m 0644 \
  /var/lib/c3po/previous-c3po-bridge.service \
  /etc/systemd/system/c3po-bridge.service
sudo systemctl daemon-reload

# Restore only that checkout's user commands, never its systemd automation.
if [ -x ./scripts/robot/install_robot_scripts.sh ]; then
  SKIP_LOGROTATE=1 ./scripts/robot/install_robot_scripts.sh
elif [ -x ./scripts/robot/c3po ]; then
  ln -sf "$PWD/scripts/robot/c3po" ~/.local/bin/c3po
fi
sudo systemctl restart c3po-bridge.service
```

This rollback keeps perception foreground-only. Do not continue to sensor or motion tests
after a rollback.

## 4. Build checks before taking sensors

Coordinate the CPU/GPU window first.

```bash
c3po perception build vision
c3po perception build bench
```

Pass if the image builds and the benchmark loads the existing engine. Record throughput,
median, p90, and p99 next to the earlier measured baseline in `apps/perception/README.md`.

To test the detector's cold-engine `np.bool` fix, preserve the known-good plan rather than
deleting it:

```bash
docker run --rm --entrypoint sh \
  -v c3po-trt-engines:/engines \
  c3po/perception-vision:r35.3.1 \
  -c 'mv /engines/yolo11n.fp16.plan /engines/yolo11n.fp16.plan.warm-backup'
c3po perception build engine
c3po perception build bench
```

If the cold build fails, restore the known-good plan:

```bash
docker run --rm --entrypoint sh \
  -v c3po-trt-engines:/engines \
  c3po/perception-vision:r35.3.1 \
  -c 'mv -f /engines/yolo11n.fp16.plan.warm-backup /engines/yolo11n.fp16.plan'
```

After a successful cold build, keep the backup until the full perception stage passes.

## 5. Camera relay handoff

This changes a shared vendor service. Confirm agreement again before typing `take`.

```bash
curl -sS http://127.0.0.1:8001/camera/status
c3po camera take
c3po perception up perception
curl -sS http://127.0.0.1:8001/camera/status
c3po status
```

Pass if the same `:8001/camera` endpoint changes from `source: videohub` to
`source: vision`, perception is healthy, and no URL/configuration change was needed.

Rollback:

```bash
c3po perception stop
sudo systemctl start master_service
curl -sS http://127.0.0.1:8001/camera/status
```

Pass rollback only when the status returns to `source: videohub`.

## 6. Voice loop, harmless utterance first

Keep the gate closed. Start the loop from the dashboard and first say a question that needs
no tool and no motion. Confirm transcription and spoken response. Then say the configured
stop phrase and verify the stop reaches the bridge without waiting for the model.

Only after both pass may the operator try a deliberately chosen harmless tool such as
`say`. Do not use an open-ended instruction for the first end-to-end voice test: overheard
speech enters the same agent/tool path as chat.

## 7. Motion and teleop are a separate escalation

Run `c3po preflight` immediately before any planner or leg command:

```bash
c3po preflight
```

A refusal is the result; do not override it. With a clean preflight, use the existing
supervised ladders rather than ad-hoc calls:

```bash
cd ~/c3po/apps/bridge
uv run python scripts/vr_smoke_test.py --skip-legs
uv run python scripts/teleop_smoke_test.py
```

`--skip-legs` is the first pass. Leg motion is a second explicit decision with the operator
still beside the physical e-stop. Arms remain disabled until this separate check succeeds:

```bash
uv run python scripts/arm_sign_check.py --dry
uv run python scripts/arm_sign_check.py
```

Never run the arm check while walking.

## 8. End the window deliberately

```bash
c3po stop
sudo systemctl start master_service
c3po status
docker ps
```

Return the sensors/stack according to the agreement with the other team. Preserve
`~/.c3po/logs/monday-validation.log`, note every failed gate, and update the "Written and
never run on hardware" inventory in `docs/OPERATIONS.md` only for checks that actually
passed.
