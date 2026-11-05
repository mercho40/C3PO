# Stand runbook: MVP Experience 2026

The G1 at a stand, with people walking past. **Show only what has worked on this robot.** As of 2026-11-05, that means the skills marked `works_real` in the catalogue, plus the back-side `look`.

The lidar is faulted and the hands are dead (`docs/autonomy/hardware-tickets.md`), so there is no obstacle avoidance and no grasping.

## 0. The area

- **Space:** a roped area of at least **3 × 3 m**, with the robot in the middle and the operator at the rope.
  - Visitors stay **≥ 1.5 m** away. Closer than that, the robot slows to 40 %; under 0.5 m it stops. This is the person rule, and it is working as intended.
  - Anyone within 0.8 m blocks arm motion.
- **Walking:** go_to_object and walk_to only, inside the area. They use the camera only, with nothing watching the floor (no lidar), so keep the floor clear.
- **PARAR:** the console's red button (or Shift+Space) is always on screen. A spoken «para» / «stop» also works. «Reanudar» clears it (admin only).
- **The cable:** the Ethernet in the Anker hub drops when the robot walks. Tape the cable along the robot's back, plug the adapter straight into the Mac rather than the hub, and leave slack for the walking area.

## 1. Bring-up (about 15 min before doors open)

1. Power on the robot, on the gantry. On the Mac, set `en11` to static `192.168.123.99`.
2. `ssh unitree@192.168.123.164 docker ps`: no `gemm-*` containers should be running. If any are, stop them; their autostart is off, but check anyway.
3. Run `scripts/robot_session.sh unitree@192.168.123.164`. All five rows should show ✓. Open http://localhost:3001 (not 127.0.0.1).
4. Start perception on the robot: `c3po perception up perception`.
5. At **/vr-control**: «1 · Amortiguar» → «2 · Preparar» → lower the gantry until the feet carry weight → «3 · Habilitar marcha». Only FSM **501** walks: check `get_state` before each walk.
6. Smoke test: say "hola", `wave`, `look`, and `describe_surroundings`, from /chat or /voice.

## 2. The loop to show

Each one works on its own; skip any that misbehave today.

| Show             | How                                                                | Verified                    |
| ---------------- | ------------------------------------------------------------------ | --------------------------- |
| A conversation   | /voice, legacy engine: talk to it in Spanish                       | listen/say ✅               |
| "¿Qué ves?"      | `look`: the school vision model describes the camera frame         | look ✅ (2026-10-27)        |
| Counting objects | `describe_surroundings`: detector labels with distance and bearing | ✅                          |
| Gestures         | `wave`, `clap`, `shake_hand`, `hug`, `gesture`                     | ✅                          |
| Going to a thing | "andá hasta la silla" → `go_to_object`, 1–3 m, in the clear area   | ✅ (3 arrivals, 2026-11-05) |
| Turning          | `turn`                                                             | ✅                          |

**Do not demo:**

- **pick, place or handover:** the hands are dead and the arm path is unverified on the robot.
- **Nav2 navigation:** the lidar is down.
- **dance, squat, sit_g1, lie_up:** not verified on this robot.

## 3. When something breaks

| Symptom                                       | Do                                                                                                                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Console shows the bridge offline after a walk | It's the cable. `robot_internet.py` should reconnect on its own (tested off the robot only); re-run `robot_session.sh` if the tunnel row is ✗. |
| Robot doesn't walk; velocity accepted         | Not in 501, or in 802 after the remote's R2+A. Redo step 5.                                                                                    |
| `look` says unavailable                       | The camera or perception is down: `c3po perception up perception`. Use describe_surroundings meanwhile.                                        |
| Every move refused                            | PARAR is latched. Press «Reanudar» as admin.                                                                                                   |
| The robot slows or stops by itself            | Someone is inside 1.5 m. Ask them back to the rope.                                                                                            |
| A `gemm-*` container is back up               | `docker stop` it (it holds the D435). Never delete their files.                                                                                |
| Bridge needs a restart                        | `ssh unitree@192.168.123.164 sudo -n /usr/bin/systemctl restart c3po-bridge.service`                                                           |

## 4. Teardown

PARAR, then «1 · Amortiguar» with the robot on the gantry. Stop `robot_session.sh` with Ctrl-C. It should leave no ssh, bun or python children behind; check with `pgrep -fl 'ssh|bun|robot_internet'`, which is not yet verified with the cable. Power off.
