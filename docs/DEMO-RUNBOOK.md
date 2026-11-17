# Stand runbook: MVP Experience 2026

The G1 at a stand, with people walking past. **Show only what has worked on this robot.** As of 2026-11-05, that means the skills marked `works_real` in the catalogue, plus the back-side `look`.

The lidar is faulted, so there is no obstacle avoidance. The **right** BrainCo hand works since its wrist cable was reseated (2026-11-13); the left one is dead (`docs/autonomy/hardware-tickets.md` §2). Pick is shown to the public with the right hand **only after the morning gantry check in §1 passes** (the user's decision, 2026-11-13).

## 0. The area

- **Space:** a roped area of at least **3 × 3 m**, with the robot in the middle and the operator at the rope.
  - Visitors stay **≥ 1.5 m** away. Keep them there yourself: do not count on the robot to do it.
  - **The person rule is not a guarantee.** With perception up, a detected person closer than 1.5 m slows the robot to 40 %, and under 0.5 m it stops. Its range has never been checked against a tape (`docs/autonomy/test-day/safety.md` step 5). It only sees what the head camera sees straight ahead: people beside or behind the robot do not count. With perception down it does nothing.
  - **The 0.8 m arm block covers T2 tools only** (`move_arm`, `set_hand`, `reach`, `pick`, `place`). `wave`, `hug`, `shake_hand`, `clap` and `gesture` are not T2, so they run with someone right next to the robot. Check that nobody is within arm's reach before each gesture.
- **Walking:** go_to_object and walk_to only, inside the area. They use the camera only, with nothing watching the floor (no lidar), so keep the floor clear. go_to_object stops on its own within about a second when the detector goes quiet (`perception_stale`).
- **PARAR:** the console's red button (or Shift+Space) is always on screen. It sends zero velocity and the robot stays standing. If the robot acknowledges none of those zero commands, PARAR damps it, and a standing robot then falls. «Reanudar» clears it (admin only).
- **The spoken stop:** say «emergencia» or «pará, pará». A single «pará» or «stop» does nothing. It works only while a voice conversation runs on /voice (either engine), and only while the robot's microphone delivers audio (/voice warns when none has arrived). On synthesised speech it stopped the robot 13 of 15 times for «emergencia», 14 of 15 for «pará, pará» and «¡detener!», and only 7 of 15 for a flat «detener». It has never been tested with real voices or crowd noise (`safety.md` step 13). It is a convenience, not the stop to rely on.
- **The remote:** L2+B is the hardware e-stop. It damps the motors and the robot falls, so use it only when PARAR has not worked.
- **The cable:** the Ethernet in the Anker hub drops when the robot walks. Tape the cable along the robot's back, plug the adapter straight into the Mac rather than the hub, and leave slack for the walking area.

## 1. Bring-up (about 15 min before doors open)

1. Power on the robot, on the gantry. On the Mac, set `en11` to static `192.168.123.99`.
2. Battery full: the robot rebooted at 19 % on 2026-11-13.
3. Robot side, one command: `./scripts/robot_up.sh`. It deploys `feat/autonomy`, checks that no `gemm-*` container runs (it stops if one does: decide who drives), restarts the bridge, starts perception, the BrainCo hand server (a macOS dialog asks for the robot's sudo password, unless `c3po-brainco-hand.service` already runs) and the headset stream on loopback. Run it again after any robot reboot: see «What starts at boot» below.

   **What starts at boot.** Only what is enabled below, and nothing that walks or moves an arm:
   - The bridge, `c3po-bridge.service`: `c3po install` enables it (it claims no sensor and commands no motion). `c3po status` shows it on the `bridge at boot` line; if that is not `enabled`, run `sudo systemctl enable c3po-bridge.service` on the robot (`ssh -t`, it asks for the password).
   - The BrainCo hand server, `c3po-brainco-hand.service`: `c3po install` installs it but never enables it, because it drives the hand motors. To have it at every boot, once, on the robot: `sudo systemctl enable --now c3po-brainco-hand.service`; to stop that, `sudo systemctl disable --now c3po-brainco-hand.service`. It runs the vendor binary in place under `~/gemm_ai` (never edited) and is skipped if the binary is missing. Logs: `journalctl -u c3po-brainco-hand.service`. While it runs, `c3po up` and `c3po preflight` report it as another commander (`brainco_hand_server` is in `OTHER_COMMANDER_PATTERNS`), as they do after `robot_up.sh` starts it by hand.
   - Perception (`c3po perception up perception`) and the headset stream (`run_teleop`) never start at boot: `robot_up.sh` starts them.

4. Mac side: `./scripts/robot_session.sh unitree@192.168.123.164` (tunnel, console on http://localhost:3001). Quest on USB: `bash scripts/quest_reverse.sh`, then http://localhost:3001/vr-control. In «Caras», turn «Escuchar después de saludar» on (again after every back restart: who turned it on lives in back's memory).
5. At **/vr-control**: «1 · Amortiguar» → «2 · Preparar» → lower the gantry until the feet carry weight → «3 · Habilitar marcha». Only FSM **501** walks: check `get_state` before each walk.
6. Smoke test: say "hola", `wave`, `look`, and `describe_surroundings`, from /chat or /voice. Each move is preceded by «Atención, todos los humanos córranse: voy a …».
7. **Pick check, on the gantry, before doors open (15 min).** An opaque plastic bottle upright on a 0.80–0.95 m table, about 38 cm in front of the robot and 15 cm to its right. «agarrá la botella» three times. All three must: plan from the side (result `approach_tilt_deg` ≤ 30), keep the elbow off the torso and the fingers off the table, end with the palm on the bottle, speak without overlaps, and say «Confirmado: ejecuté pick.» only when it holds it (a refusal says «Confirmado, pero pick falló…»). If any fails: `C3PO_ALLOW_PICK=0` in the robot's `apps/bridge/.env`, `sudo -n systemctl restart c3po-bridge.service`, and leave pick out today.
8. Battery: read it on the console's topbar chip, and watch it during the day. At the amber «Batería baja» banner (under 25 %), finish the current round, press PARAR, then «1 · Amortiguar» with the robot on the gantry, and charge. At 20 % or less the banner turns red («Batería crítica»), and under 20 % the bridge refuses `walk_to`, `go_to_object` and the T2 arm tools; gestures and `turn` still run.

## 2. The loop to show

Each one works on its own; skip any that misbehave today.

| Show                      | How                                                                                                                      | Verified                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| A conversation            | /voice, legacy engine: talk to it in Spanish                                                                             | listen/say ✅                                                                              |
| "¿Qué ves?"               | `look`: the school vision model describes the camera frame                                                               | look ✅ (2026-10-27)                                                                       |
| Counting objects          | `describe_surroundings`: detector labels with distance and bearing                                                       | ✅                                                                                         |
| Gestures                  | `wave`, `clap`, `shake_hand`, `hug`, `gesture`                                                                           | ✅                                                                                         |
| Going to a thing          | "andá hasta la silla" → `go_to_object`, 1–3 m, in the clear area                                                         | ✅ (3 arrivals, 2026-11-05)                                                                |
| Turning                   | `turn`                                                                                                                   | ✅                                                                                         |
| Greeting by name          | «Caras» → «Saludar a conocidos»; an enrolled person crouches 0.5–0.8 m in front, face chest-high                         | ✅ (2026-11-13: Oliver, Simon)                                                             |
| Answer after the greeting | «Escuchar después de saludar» on: after «Hola X, ¿qué querés que haga?» the robot mic listens 20 s; anyone near is heard | built 2026-11-13, not yet tried                                                            |
| Hand shapes               | «cerrá la mano derecha», «cerrá solo el índice», «hacé el saludo peronista con la mano derecha»                          | ✅ hand moves (2026-11-13); single fingers and the thumb's rotation not checked one by one |
| Picking a bottle          | «agarrá la botella», right hand, after the §1 pick check                                                                 | pending: the 2026-11-13 fixes have not run on the robot                                    |

**Do not demo:**

- **pick, place or handover with the left hand**, or any pick if the §1 check failed today.
- **the arm held out for long while standing:** on 2026-11-13 it made the robot wobble.
- **Nav2 navigation:** the lidar is down.
- **dance, squat, sit_g1, lie_up:** not verified on this robot.

## 3. When something breaks

| Symptom                                                                       | Do                                                                                                                                                                                                                                        |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Console shows the bridge offline after a walk                                 | It's the cable. `robot_internet.py` should reconnect on its own (tested off the robot only); re-run `robot_session.sh` if the bridge row is ✗.                                                                                            |
| Robot doesn't walk; velocity accepted, or the walk refused `fsm_not_walkable` | Not in 501, or in 802 after the remote's R2+A. Redo step 5.                                                                                                                                                                               |
| A walk refused `arm_held`                                                     | The arms are still held from an arm move. `release_arm_control`, then walk.                                                                                                                                                               |
| `look` says unavailable                                                       | The camera or perception is down: `c3po perception up perception`. Use describe_surroundings meanwhile.                                                                                                                                   |
| Every move refused                                                            | PARAR is latched. Press «Reanudar» as admin.                                                                                                                                                                                              |
| The robot slows or stops by itself                                            | The detector sees someone inside 1.5 m. Ask them back to the rope.                                                                                                                                                                        |
| A `gemm-*` container is back up                                               | `docker stop` it (it holds the D435). Never delete their files.                                                                                                                                                                           |
| Nobody is recognised, or no face boxes                                        | The camera points about 48° down: crouch so the face is chest-high to the robot, 0.5–0.8 m away, Quest off. Boxes update only while a greet switch is on in «Caras».                                                                      |
| Every agent turn fails with `502 upstream_unreachable`                        | ia-tic is down. Restart back on the Gemini fallback (`docs/OPERATIONS.md` §4, "When ia-tic is down"); robot-mic `C3PO_STT=back` cannot work meanwhile.                                                                                    |
| The cable link and the camera drop together                                   | The USB-Ethernet adapter and the D435 went together twice on 2026-11-11. Re-seat the adapter off the hub and keep the cable out of the walk. After the vision image rebuild the detector reopens the camera by itself; if not, `c3po up`. |
| The robot rebooted (uptime of minutes)                                        | At most the bridge and the hand server start at boot, and only if enabled (§1, «What starts at boot»): `./scripts/robot_up.sh` again, then reload the console and the Quest. Check the battery first.                                     |
| «ECONNRESET» in /voice or the Quest disconnects                               | The bridge restarted. Press «Iniciar» on /voice again and reload /vr-control.                                                                                                                                                             |
| The Quest never connects                                                      | The console must be started with `C3PO_ROBOT_HOST=localhost` (robot_session.sh does), `adb reverse --list` must show 3001, 3000, 8001, 8767, and the headset stream must listen on 127.0.0.1:8767 (`ss -tln` on the robot).               |
| `set_hand` or pick fails with no hand state                                   | The BrainCo hand server is not running: `./scripts/robot_up.sh` starts it (password dialog).                                                                                                                                              |
| «Confirmado, pero pick falló: …»                                              | The pick refused before moving; the text says why. `torso` / «out of reach»: put the bottle ~38 cm ahead, 15 cm to the right. `table_clearance`: raise the bottle. Nothing moved.                                                         |
| The greeting no longer opens the mic                                          | Back restarted and forgot who turned the switch on: «Caras» → «Escuchar después de saludar» off and on.                                                                                                                                   |
| A `gemm-*` container is running at bring-up                                   | `robot_up.sh` stops there on purpose. Someone decides who drives; never delete their files.                                                                                                                                               |
| Bridge needs a restart                                                        | `ssh unitree@192.168.123.164 sudo -n /usr/bin/systemctl restart c3po-bridge.service`                                                                                                                                                      |

## 4. Teardown

PARAR, then «1 · Amortiguar» with the robot on the gantry. Stop `robot_session.sh` with Ctrl-C. It should leave no ssh, bun or python children behind; check with `pgrep -fl 'ssh|bun|robot_internet'`, which is not yet verified with the cable. Power off.

## 5. The sign at the stand

Print it large and put it at the rope, where people read it before the camera sees them. Uruguay's data protection law (Ley 18.331) is why it is worth having; the text only says what the code does.

> **Este robot tiene una cámara y micrófonos encendidos.**
>
> - Reconoce por la cara solo a los integrantes del equipo que dieron su consentimiento.
> - De las demás personas no guarda nada: ni fotos ni datos de la cara. Solo cuenta cuántos desconocidos saludó.
> - Lo que le decís se procesa con servicios de inteligencia artificial en la nube.
> - Las imágenes que salen a internet van con las caras borroneadas.
>
> ¿Preguntas o no querés salir en cámara? Hablá con el equipo.

The blurred-faces line is true only once `docs/autonomy/test-day/vlm.md` §10 passes on the robot. Until then, leave it off the printed sign.
