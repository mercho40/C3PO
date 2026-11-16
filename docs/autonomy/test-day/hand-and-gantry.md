# Test day: the hands, then the first table pick on the gantry

Branch `feat/autonomy`. Code: `apps/bridge/scripts/hand_probe.py` (serial probe),
`apps/bridge/src/bridge/teleop/hands.py` (the hand driver) and
`apps/bridge/src/bridge/skills/manipulation.py` (`pick`). Status 2026-11-11: neither BrainCo
hand has answered since 2026-11-03, so the bridge runs `TELEOP_HAND_TYPE=simulated` (BACKLOG
5.1). `/dev/ttyUSB0`–`3` exist. Nothing in this sheet has run on the robot yet.

Do it in order. Each section needs the one before it. Stop at the first section that fails and
write down what you saw.

How the hands are wired: hand → RS485 → FTDI FT4232H (`0403:6011`) → `/dev/ttyUSB0`–`3` →
the vendor `brainco_hand_server` (Modbus RTU, 460800 baud, slave 126 = left, 127 = right) →
DDS `rt/brainco/{side}/{cmd,state}` → the bridge. **The bridge never opens a ttyUSB.** The
probe talks to the hands directly, without the vendor server, so it tells you whether the
hand or the software is the problem.

Robot: sitting or damped for §1–3, arms at rest. On the gantry for §5–6. One person on the
remote, one watching the arm. PARAR in the console (or Shift+Space) stops everything and opens
the hands; «Reanudar» clears it.

## 1. Free the lines and probe (no motion, 5 min)

On the robot (`ssh c3po`):

| #   | Do                                                                     | Expect                                                                                                                                                                                                                                   |
| --- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `ls -l /dev/ttyUSB*` and `lsusb \| grep 0403:6011`                     | Four ports, `crw-rw---- root dialout`, and one FTDI line. No ports at all: the FT4232H is gone, go to §2 row 9.                                                                                                                          |
| 2   | `pgrep -af brainco_hand_server` and `sudo fuser -v /dev/ttyUSB*`       | Nothing. If `brainco_hand_server` is listed: `sudo pkill -f brainco_hand_server`, then check again. The bridge does not need stopping: with `simulated` it publishes nothing, and it never holds a port.                                 |
| 3   | `cd ~/c3po/apps/bridge && sudo .venv/bin/python scripts/hand_probe.py` | One line per port. A hand that answers: `/dev/ttyUSB1: opened yes, 7 bytes received, right hand (slave 127): registers 0x…`, or `… Modbus exception 2 (alive; register not readable)`. Both mean the hand is alive. Write the port down. |
| 4   | Read the last line and the exit code (`echo $?`).                      | `Both hands answered.` (0): go to §3. `Only one hand answered.` (1): go to §3 with that side only. `No hand answered.` (2): go to §2.                                                                                                    |
| 5   | If it prints `in use by …` and `Refusing` (exit 3).                    | Something still holds a line. Stop the named process (row 2) and run row 3 again. Never run two programs on one RS485 line.                                                                                                              |
| 6   | If a port prints `opened no (Permission denied)`.                      | You forgot `sudo`.                                                                                                                                                                                                                       |
| 7   | If a port prints `… bytes received, silent (noise, no valid frame)`.   | Something is on that line but not speaking Modbus at 460800. Write the byte count down; treat it as silent for §2, and mention it in the ticket.                                                                                         |

The probe sends only Modbus "read holding registers" (function 0x03), one per slave id per
port. A read changes nothing on the hand. There is no move test in it.

## 2. If no hand answers: power and cable (15 min)

From BACKLOG 5.1, `docs/ROBOT-HARDWARE.md` §7 and `docs/autonomy/hardware-tickets.md` §2.
After each fix, run §1 row 3 again.

| #   | Do                                                                                                                                                            | Expect                                                                                                                                                                  |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 8   | Robot powered off (damp, then the power button). Look at each wrist: is the hand's cable seated in its connector? Reseat it once. Power on.                   | The cable clicks in. On 2026-08-19 one hand was found simply unplugged, so check this first.                                                                            |
| 9   | `dmesg \| grep -i ftdi \| tail`                                                                                                                               | `ftdi_sio … now attached to ttyUSB0` … `ttyUSB3`. Disconnects or errors: the adapter or its USB cable, not the hands.                                                   |
| 10  | Any LED on the hand when the robot is on? Does a finger twitch at power-on?                                                                                   | Note yes or no for each hand. No LED and no twitch: the hand has no power. That is a Unitree/BrainCo question; do not open the wrist.                                   |
| 11  | Try the official server: `sudo ./brainco_hand_server --network_interface eth0` from `~/gemm_ai/xr_teleoperate/vendor/brainco_hand_service/bin/`, then Ctrl-C. | Its log lists the four ports and either binds a hand (`right hand bound to /dev/ttyUSB1 port`) or prints `Failed to get device info`. Run it; do not edit gemm's files. |
| 12  | Still nothing.                                                                                                                                                | Send the ticket in `hardware-tickets.md` §2 with today's probe output and rows 8–11. Stop here: the bridge stays on `simulated`, and §5 runs without fingers.           |

## 3. Switch to the real driver (10 min)

Only for a side that answered in §1.

| #   | Do                                                                                                                                                                             | Expect                                                                                                                                                                                                                        |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 13  | In `~/c3po/apps/bridge/.env`: `TELEOP_HAND_ENABLED=1`, `TELEOP_HAND_TYPE=brainco`, `TELEOP_BRAINCO_OPEN_AT=0`, `TELEOP_HAND_SIDES=right` (`left,right` only if both answered). | `0` is open (settled 2026-08-27, `hands.py`). The driver refuses anything but `0` or `1` here.                                                                                                                                |
| 14  | Start the vendor server in its own terminal (§2 row 11 command) and leave it running.                                                                                          | It binds the port(s) from §1: `right hand bound to /dev/ttyUSB1 port`, `Starting worker for right (slave 127)`.                                                                                                               |
| 15  | `c3po restart`                                                                                                                                                                 | `bridge restarted under systemd … answers on port 8001`. `c3po logs` shows `teleop.hands.publisher.ready hand=brainco` on the first hand call, and no `teleop.hands.simulated`.                                               |
| 16  | `c3po preflight`                                                                                                                                                               | It blocks on "SOMETHING ELSE CAN DRIVE THE LEGS" because `brainco_hand_server` is on the commander list (`_common.sh`). Expected while the real hands are in use: check the listed pid is only the hand server, nothing else. |
| 17  | `cd ~/c3po/apps/bridge && SIM_MODE=real ROBOT_HOST=127.0.0.1 DDS_INTERFACE=eth0 uv run python scripts/hand_dds_probe.py` with the hand open.                                   | `ANSWERED brainco-right rt/brainco/right/state (6 motors)` and `q` near 0 on every channel. `q` near 1 with the hand open: set `TELEOP_BRAINCO_OPEN_AT=1` and restart.                                                        |
| 18  | Console chat: «cerrá un poco la mano derecha». The card must show `set_hand` side `right`, `closure 0.3`. Press «Confirmar set_hand». Then `open_hands`.                       | All fingers curl about a third, then open. There is no force setting: a small `closure` is the low-force test. Fingers open instead of closing: polarity is wrong, PARAR, fix row 13.                                         |
| 19  | `set_hand` side right `shape power`, then `open_hands`.                                                                                                                        | Fingers wrap an imaginary bottle, thumb across the palm. Thumb swings outward: fix channel 1 in `hands.SHAPES` (`manipulation.md` row 9).                                                                                     |
| 20  | Stop: `stop_everything` (PARAR) with the hand at `closure 0.3`.                                                                                                                | The hand opens. Nothing else opens a BrainCo: it has no firmware deadman. «Reanudar» before the next call.                                                                                                                    |

If the hand stops moving in the middle of this, check the vendor server's terminal first.

## 4. Measure the hand (10 min, arm at rest or held by `reach`)

`pick` models the open hand against the table with two guesses. Until they are measured, most
table picks refuse with `table_clearance`. Use a tape. The tool point (TCP) is the palm centre,
where the middle of a closed `power` grip sits (`kinematics.GRASP_TCP_M`, 0.10 m from the
wrist-yaw axis).

| #   | Do                                                                                         | Write into `apps/bridge/src/bridge/skills/manipulation.py`                                                  |
| --- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| 21  | `set_hand shape open`. Tape from the palm centre to the tip of the longest finger, in m.   | `FINGER_M` (today `0.09`).                                                                                  |
| 22  | Same open hand: tape the palm's full width across the knuckles and halve it.               | `HAND_OPEN_RADIUS_M` (today `0.04`).                                                                        |
| 23  | `set_hand shape power`. Tape the fist's thickness, back of the hand to the curled fingers. | Half of it is the `"hand"` radius in `ARM_CAPSULES` (today `0.031`). Change it only if your half is larger. |
| 24  | `open_hands`.                                                                              | Hand open.                                                                                                  |

Then on the Mac, from `apps/bridge`:

```bash
PYTHONPATH=src uv run pytest -q                                   # every suite, incl. test_manipulation.py
uv sync --extra sim && bash scripts/fetch_menagerie_g1.sh && uv run pytest -m sim   # the MuJoCo twin
```

Expect 0 failed. The twin runs 20 random table scenes: more of them should plan than before
(1 in 20 with the guesses). Commit the numbers, push to the robot (`README.md` §1), and
`c3po restart`.

## 5. First pick, on the gantry (20 min)

> ⚠️ `pick` refuses on the robot until `C3PO_ALLOW_PICK=1` is in `apps/bridge/.env`. Set it for
> this section only, and remove it when you are done.

Setup: the robot hung in the gantry, standing (FSM 4 or 501), not walking. **Nobody within 1 m.**
An opaque plastic cup on a table or counter **0.80–0.95 m above the floor**, **25–35 cm in front
of the chest**, a little to the right. Perception up, the cup in the head camera's view. Hand
empty and open. With the face gate on (`safety.md` 25), the operator's face must be in view.

| #   | Do                                                                                      | Expect                                                                                                                                                                                                                                                                                 |
| --- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 25  | `C3PO_ALLOW_PICK=1` (and `TELEOP_ARM_ENABLED=1`) in `apps/bridge/.env`, `c3po restart`. | Bridge back on port 8001.                                                                                                                                                                                                                                                              |
| 26  | Console: `describe_surroundings`.                                                       | The cup is listed, with a `support` under it.                                                                                                                                                                                                                                          |
| 27  | Console: «agarrá el vaso». The card shows `pick` label "cup". Press «Confirmar pick».   | `result.plan` has 11 entries, `curl` to `lift`. The hand curls, the elbow juts out to the side (the shoulder-out tuck), the hand rises beside the body and passes **above the table** until it is over the cup, comes down 10 cm short, opens, moves in, closes, lifts 8 cm and holds. |
| 28  | Watch while it runs.                                                                    | The elbow never touches the torso. The hand stays above the table top until it is over the cup. The open fingertips clear the table. If the hand gets within about 3 cm of the table, or anything touches: **PARAR**.                                                                  |
| 29  | `verify("¿tenés un vaso en la mano?")`                                                  | Yes. If not, write down the miss in cm (too high / short / left) and `result.target_torso_m`.                                                                                                                                                                                          |
| 30  | Confirm + `place` side right.                                                           | Lowers, opens, rises, draws back, hands the arm back to the built-in controller.                                                                                                                                                                                                       |
| 31  | Refused with `table_clearance`.                                                         | Nothing moved. §4 is still wrong or the cup is too low: raise it to 0.90 m and try again. Do not lower `TABLE_CLEARANCE_M`.                                                                                                                                                            |

How to stop: PARAR in the console, Shift+Space, the spoken stop, or the remote. Each one releases
the arm and opens the hand. A person stepping inside 0.8 m also aborts the motion. Then «Reanudar».

More picks, the table edge and the place onto a table: `manipulation.md` §5 and §8.

## 6. Then the Quest voice pick (10 min)

Only after row 27 worked from the console. Same gantry setup, `C3PO_ALLOW_PICK=1`, nobody within
1 m. The operator in the headset on /vr-control (`c3po up teleop`).

| #   | Do                                                                                     | Expect                                                                                   |
| --- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 32  | Hold B, say «agarrá la taza», release.                                                 | The headset shows the transcript, then the refused `pick` call waiting for confirmation. |
| 33  | Hold B, say «confirmo», release.                                                       | «Transcribiendo… si dijiste «confirmo», se ejecuta», then the pick runs as in row 27.    |
| 34  | While it runs, the person on the console keeps a finger on PARAR.                      | Same checks as row 28. Both grips held 300 ms also stops it.                             |
| 35  | Done: `open_hands`, `release_arm_control`, remove `C3PO_ALLOW_PICK=1`, `c3po restart`. | Arm back under the built-in controller, `pick` refused again.                            |

## Rollback

| Problem                         | Do                                                                                                       |
| ------------------------------- | -------------------------------------------------------------------------------------------------------- |
| A hand moves wrong              | PARAR. Then `TELEOP_HAND_TYPE=simulated` in `.env`, `c3po restart`, `sudo pkill -f brainco_hand_server`. |
| The arm moves wrong             | PARAR or the remote. Remove `C3PO_ALLOW_PICK=1`, `c3po restart`.                                         |
| Arms must not move today at all | `TELEOP_ARM_ENABLED=0`, `c3po restart`.                                                                  |
