# Back at the robot: what to test, in order

Written 2026-11-03 while the operators were away. Everything here is on `feat/autonomy`
and on the robot. One person holds the Unitree remote for every step that moves.

Console: http://localhost:3001 (`operador@c3po.local`). Robot in the gantry unless a step
says otherwise.

Before the session, on campus: `bun run eval:t0` against the 8011 stub
([eval/README.md](../eval/README.md) step 2). Nothing moves; it catches a prompt or catalogue
regression before the robot does.

Status marks: ✅ verified on the date given · open · blocked (with the BACKLOG id).

## 0. Bring-up (2 min)

0. `docker ps`: gemm's containers must not be up. They held the D435 on 2026-11-06.
1. Robot hanging in the gantry, damped. Then `damp → prepare → start_walking_waist` from the
   console bring-up panel (**1 · Amortiguar**, **2 · Preparar**, **3 · Habilitar marcha**).
   Must end in **FSM 501** (`walk_waist`). **802 (R2+A on the remote) accepts velocity and
   ignores it** — the trap from 2026-11-03.
2. Topbar: no red **PARAR ACTIVO** chip. If there is one, press **Reanudar**.

## 1. Camera pitch (2 min, nothing moves)

Status: open.

A backpack on the floor exactly 1.0 m in front of the feet, robot **standing** in 501.
The default is now the URDF mount, 47.6° nose-down (`grounding.py`
`DEFAULT_CAMERA_EXTRINSIC`), but nobody has checked it with a tape. Claude reads `range_m`;
if it is more than ~5 cm off 1.0 m, put the measured pitch in `~/.c3po/perception.env`
(`C3PO_CAMERA_PITCH_DEG`) and read again. The person rule, the arm-reach rule and the
`go_to_object` stop all depend on it.

## 2. PARAR now stops the agent too (5 min)

| Do                                                                         | Expect                                                                                    | Status                                                                     |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Chat: "caminá medio metro para adelante". While it walks, press **PARAR**. | Robot stops. The chat turn ends (aborted). Topbar shows **PARAR ACTIVO · Reanudar**.      | ✅ 2026-11-03, but the turn aborted before the walk started; mid-walk open |
| Chat: "girá a la izquierda".                                               | Refused: _"PARAR … is standing"_. The agent says the robot is stopped. **Nothing moves.** | ✅ 2026-11-03                                                              |
| Press **Reanudar**, ask again.                                             | Turns.                                                                                    | ✅ 2026-11-03                                                              |
| Say "para" out loud during a walk (voice session on).                      | Same as PARAR: stops, latches, voice turn aborted.                                        | open (fixed in code since, 05b3d88 and e3bd90b; not run on the robot)      |

## 3. Go to an object — camera only, no Nav2 (10 min)

Robot on the floor with slack in the gantry, a chair 2–3 m ahead, nobody between.

| Say / type                                            | Expect                                                                                                                                                                 | Status                                  |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| "¿Qué ves?"                                           | Lists the chair with a distance and side.                                                                                                                              | open                                    |
| "Andá hasta la silla."                                | `go_to_object("silla")`: turns toward it while walking, stops with its front ~0.7 m from the chair's near side, and reports `clearance_m`. Check that gap with a tape. | ✅ 2026-11-03; the stop gap after §1    |
| "Mirá alrededor" then "andá a la silla" (out of view) | `look_around` then `go_to_object` from **memory** (`target.source: memory`).                                                                                           | open                                    |
| A person steps in front during the walk.              | Slows under 1.5 m, stops under 0.5 m (person rule).                                                                                                                    | open (after §1, a tape-measured person) |

No obstacle avoidance: it walks straight at the target. Refuses targets over 4 m.

## 4. Arms by voice/chat with the console confirm (15 min)

Robot standing in 501, gantry. Arms enabled on the robot (`.env`). Neither hand answers, so
the bridge runs `TELEOP_HAND_TYPE=simulated`: no finger moves (BACKLOG 5.1).

| Do                                                      | Expect                                                                                                 | Status                              |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------- |
| Chat: "levantá el brazo derecho" (or "extendé la mano") | Agent calls `move_arm`/`reach` → refused → the tool card shows **Confirmar move_arm**.                 | ✅ 2026-11-03                       |
| Click **Confirmar**.                                    | One press runs the refused call with the arguments shown; the arm moves slowly (2 s blend, 0.6 rad/s). | ✅ 2026-11-03 (`reach` within 1 cm) |
| Ask again without clicking.                             | Refused again: each click is one call.                                                                 | open                                |
| Someone stands within 0.8 m and confirm.                | Refused: _"inside arm's reach"_. (Range is approximate until §1 passes.)                               | open                                |
| Voice: same request.                                    | `/voice` lists the pending call with **Confirmar** / **Cancelar**; one press runs it.                  | open                                |
| "Soltá el brazo" / `release_arm_control`                | Arm hands back to the built-in controller over 2 s.                                                    | open                                |

Watch the **torso**: with the waist-hold fix it must stay upright while the arm moves. If it
bends forward, PARAR and tell Claude — that is the sdk2_python #146 failure.

The waist is now held at its engage pose while arm_sdk is on. Standing, that is what
upstream does. **Walking with the headset's arms engaged has never been tried**: it
overrides the gait's own waist, so test it last, slowly, in the gantry, and stop if the
gait looks stiff or unbalanced.

`pick` / `place` only after `move_arm` and `reach` look right, and only with an object the
detector sees with depth. With simulated hands nothing is gripped, and the result says so.

## 5. LiDAR and Nav2

Status: blocked. The Mid-360S scan module is faulted (BACKLOG 4.1).

`c3po preflight` must show the LiDAR online. Then, gantry → 0.5 m → room, as in `nav.md`.

## 6. The headset: voice, Confirmar and PARAR (15 min, on a real Quest)

Status: open. Built in d8d5db7, 56aae23 and cad99bb; never run on a Quest.

| Do                                                    | Expect                                                                             |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Hold B or Y and speak («Hablar con el robot»).        | The WAV goes to `/voice/transcribe` and runs as a voice-mode turn.                 |
| Ask for an arm move, then «Confirmar» in the VR menu. | The refused call runs once. A spoken «confirmo» does the same with one call shown. |
| Hold both grips 300 ms.                               | PARAR: the robot stops and the latch stands.                                       |
| «Reanudar» in the VR menu.                            | The latch clears.                                                                  |

Also check, from BACKLOG §2: the mic inside immersive-vr, the B/Y and grip indices, panel
readability, and transcription quality.

## If something goes wrong

- Remote first. Then PARAR. Then stop the chat/voice turn.
- A stuck **PARAR ACTIVO**: **Reanudar** (admin), from the console chip or the VR menu.
  Re-engaging the headset no longer clears it. With no `SAFETY_CONFIRM_SECRET` in both
  `.env` files nothing clears it: set it, restart `back` and the bridge, then **Reanudar**.
- Tools refused with "needs an operator's confirmation": that is the T2 gate, press the card's
  **Confirmar**.
