# Back at the robot: what to test, in order

Written 2026-11-03 while the operators were away. Everything here is on `feat/autonomy`
and on the robot. One person holds the Unitree remote for every step that moves.

Console: http://localhost:3001 (`operador@c3po.local`). Robot in the gantry unless a step
says otherwise.

Before the session, on campus: `bun run eval:t0` against the 8011 stub
([eval/README.md](../eval/README.md) step 2). Nothing moves; it catches a prompt or catalogue
regression before the robot does.

## 0. Bring-up (2 min)

1. Robot hanging in the gantry, damped. Then `damp → prepare → start_walking_waist` from the
   console bring-up panel (**1 · Amortiguar**, **2 · Preparar**, **3 · Habilitar marcha**).
   Must end in **FSM 501** (`walk_waist`). **802 (R2+A on the remote) accepts velocity and
   ignores it** — the trap from 2026-11-03.
2. Topbar: no red **PARAR ACTIVO** chip. If there is one, press **Reanudar**.

## 1. PARAR now stops the agent too (5 min)

| Do                                                                         | Expect                                                                                    |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Chat: "caminá medio metro para adelante". While it walks, press **PARAR**. | Robot stops. The chat turn ends (aborted). Topbar shows **PARAR ACTIVO · Reanudar**.      |
| Chat: "girá a la izquierda".                                               | Refused: _"PARAR … is standing"_. The agent says the robot is stopped. **Nothing moves.** |
| Press **Reanudar**, ask again.                                             | Turns.                                                                                    |
| Say "para" out loud during a walk (voice session on).                      | Same as PARAR: stops, latches, voice turn aborted.                                        |

## 2. Go to an object — camera only, no Nav2 (10 min)

Robot on the floor with slack in the gantry, a chair 2–3 m ahead, nobody between.

| Say / type                                            | Expect                                                                                              |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| "¿Qué ves?"                                           | Lists the chair with a distance and side.                                                           |
| "Andá hasta la silla."                                | `go_to_object("silla")`: turns toward it while walking, stops ~0.7 m short. Watch it, remote ready. |
| "Mirá alrededor" then "andá a la silla" (out of view) | `look_around` then `go_to_object` from **memory** (`target.source: memory`).                        |
| A person steps in front during the walk.              | Slows under 1.5 m, stops under 0.5 m (person rule).                                                 |

No obstacle avoidance: it walks straight at the target. Refuses targets over 4 m.

## 3. Arms by voice/chat with the console confirm (15 min)

Robot standing in 501, gantry. Arms and right hand enabled on the robot (`.env`).

| Do                                                      | Expect                                                                                      |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Chat: "levantá el brazo derecho" (or "extendé la mano") | Agent calls `move_arm`/`reach` → refused → the tool card shows **Confirmar move_arm**.      |
| Click **Confirmar**.                                    | Agent retries by itself; the arm moves slowly (2 s blend in, 0.6 rad/s).                    |
| Ask again without clicking.                             | Refused again: each click is one move.                                                      |
| Someone stands within 0.8 m and confirm.                | Refused: _"inside arm's reach"_. (Range is approximate until the camera pitch is measured.) |
| Voice: same request.                                    | Same card in the voice transcript; click, then repeat the request within 30 s.              |
| "Soltá el brazo" / `release_arm_control`                | Arm hands back to the built-in controller over 2 s.                                         |

Watch the **torso**: with the waist-hold fix it must stay upright while the arm moves. If it
bends forward, PARAR and tell Claude — that is the sdk2_python #146 failure.

The waist is now held at its engage pose while arm_sdk is on. Standing, that is what
upstream does. **Walking with the headset's arms engaged has never been tried**: it
overrides the gait's own waist, so test it last, slowly, in the gantry, and stop if the
gait looks stiff or unbalanced.

`pick` / `place` only after `move_arm` and `reach` look right, and only with an object the
detector sees with depth.

## 4. Camera pitch (2 min, nothing moves)

A backpack on the floor exactly 1.0 m in front of the feet, robot **standing** in 501.
Claude reads `range_m` at pitch 0 and at 47.6°; keep whichever is closer to 1.0. The person
rule and arm-reach rule both depend on it.

## 5. LiDAR and Nav2 (when Claude says the lidar is publishing)

`c3po preflight` must show the LiDAR online. Then, gantry → 0.5 m → room, as in `nav.md`.

## If something goes wrong

- Remote first. Then PARAR. Then stop the chat/voice turn.
- A stuck **PARAR ACTIVO**: **Reanudar** (admin). Without a confirm secret: re-engage the headset.
- Tools refused with "needs an operator's confirmation": that is the T2 gate, press the card's
  **Confirmar**.

## Later (to-do)

- **Voice through the Meta Quest.** The operator speaks into the Quest's microphone and
  watches the robot do it, from the headset (camera view already reaches it through
  apps/back). Sketch: the headset page captures the mic with `getUserMedia` and streams it
  to apps/back's voice session (the realtime engine already takes raw PCM from a source;
  the legacy one listens on the robot's own mic), the transcript and the agent's tool calls
  show as a small panel in the immersive view, and **Confirmar** for T2 is a button there
  too. Needs: an in-headset PARAR (audit §1.4, there is none in immersive-vr today), the
  PARAR latch chip in the headset, and a push-to-talk so the room is not the prompt.
