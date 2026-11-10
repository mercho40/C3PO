# Back at the robot: what to test, in order

Written 2026-11-03 while the operators were away; last updated 2026-11-10. This is the one
dated sheet for the next robot session. `docs/SESSION-CHECKLIST.md` is the general order,
and the expo stand runs from `docs/DEMO-RUNBOOK.md`. Everything here is on `feat/autonomy`:
deploy it first, as in `docs/OPERATIONS.md` §5. One person holds the Unitree remote for every
step that moves.

Console: http://localhost:3001 (`operador@c3po.local`). Robot in the gantry unless a step
says otherwise.

Before the session, on campus (nothing moves, no robot needed):

- `bun run eval:t0` against the 8011 stub ([eval/README.md](../eval/README.md) step 2). It
  catches a prompt or catalogue regression before the robot does.
- The four stand tasks (314e6e6), against the same stub. Status: open; nothing has run them
  against a model yet. From `apps/back`:
  `BRIDGE_URL=http://127.0.0.1:8011/mcp bun scripts/eval-agent.ts --ids t1-saludar,t1-stand-sin-posturas,t0-no-sentarte,t1-veni-hacia-mi --expect-env stub`.
  **Expect:** a wave and no posture or walk for `t1-saludar`; no dance, squat, sit_g1 or
  lie_up for the other two; `go_to_object` on the person for `t1-veni-hacia-mi`. Record the
  run directory.
- The first GitHub runs of `nav-image.yml` (the nav image on an arm64 runner) and the
  `back-db` job (`chats.ts` on Postgres 16). Status: open. Neither has run on GitHub. A red
  nav build there is a finding about the Dockerfile, not about the robot.
- The console chat (3672bcd, 984bd5d). Status: open.
  - Type `Respondé solo con este texto: [abrir](javascript:alert(1))`. **Expect:** «abrir»
    shows as plain text, not a link, and nothing happens when you click it.
  - Send two typed messages, then reload the page. **Expect:** both questions and both replies
    come back, once each.
  - If a reply fails and the error banner offers **Reintentar**, press it. **Expect:** a new
    reply replaces the failed one, and the question is not shown twice.

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

Status: open. Built in d8d5db7, 56aae23 and cad99bb; never run on a Quest. Push-to-talk and
«confirmo» also check 984bd5d, which changed how `back` reads the chat history.

| Do                                                                                                                                                                                                                                                                           | Expect                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hold B or Y and speak («Hablar con el robot»).                                                                                                                                                                                                                               | The WAV goes to `/voice/transcribe` and runs as a voice-mode turn.                                                                                                                                                                                          |
| Ask for an arm move, then «Confirmar» in the VR menu.                                                                                                                                                                                                                        | The refused call runs once. A spoken «confirmo» does the same with one call shown.                                                                                                                                                                          |
| Hold both grips 300 ms.                                                                                                                                                                                                                                                      | PARAR: the robot stops and the latch stands.                                                                                                                                                                                                                |
| «Reanudar» in the VR menu.                                                                                                                                                                                                                                                   | The latch clears.                                                                                                                                                                                                                                           |
| In /chat on the Mac, ask _"¿Qué ves? Contame con detalle."_ While the reply is still coming, hold both grips 300 ms.                                                                                                                                                         | The chat turn ends (aborted) within about 0.5 s, although this stop never went through `back`. `back`'s log shows `[stop-watch] bridge stop at …: aborted 1 turn(s)`. «Reanudar» after.                                                                     |
| Set the same `TELEOP_SECRET` in `apps/back/.env` and the robot's `apps/bridge/.env`, restart `back`, and `c3po up teleop` again. Run `./scripts/preflight.sh` on the Mac (on the cable: `C3PO_ROBOT_HOST=192.168.123.164 ./scripts/preflight.sh`), then connect the headset. | `preflight.sh` says "teleop socket requires a token, and apps/back has TELEOP_SECRET". The headset connects and drives. `curl -s -o /dev/null -w '%{http_code}' http://192.168.123.164:8767/` from the Mac answers 401 (426 means the robot has no secret). |
| Set `HEADSET_SPEAK_REPLY=1` in `apps/back/.env` and restart `back`. Ask a question with B or Y.                                                                                                                                                                              | The robot says the reply out loud after it shows in the panel. A PARAR while it speaks cuts it off (`safety.md` step 12).                                                                                                                                   |

Also check, from BACKLOG §2: the mic inside immersive-vr, the B/Y and grip indices, panel
readability, and transcription quality.

## 7. Voice on the Realtime engine (10 min)

Status: open (d87836a). Needs `OPENAI_API_KEY` in `apps/back/.env`, and the robot's microphone
delivering audio (/voice warns when none has arrived). Nothing needs to move.

| Do                                                                                                                                             | Expect                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| On /voice choose «OpenAI Realtime (voz a voz)» and start. Talk while the robot answers, and ask _"¿qué ves?"_ and keep talking while it looks. | The session stays up. `back`'s log may show `[voice/realtime] recoverable error` with a code such as `conversation_already_has_active_response` or `rate_limit_exceeded`; the conversation goes on. Write down every code seen. |
| Any other error.                                                                                                                               | The session ends and /voice shows the error. Note the code: if it was routine, it belongs in `RECOVERABLE_ERROR_CODES` (`apps/back/src/voice/realtime.ts`).                                                                     |

## 8. Deploy records and a rollback, end to end (15 min, nothing moves)

Status: open (31b2d64, 462fe6d, 31697d9). Robot damped on the gantry. Each line runs on the
robot (`ssh -t`: install asks for the sudo password).

| Do                                                                                                                               | Expect                                                                                                                                              |
| -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| After the deploy: `cat ~/.c3po/deployed ~/.c3po/previous-deploy ~/.c3po/unit-backup-for`                                         | `deployed` is `<HEAD> <UTC time>`; `previous-deploy` is a bare sha (the commit before, if one was recorded); `unit-backup-for` is HEAD              |
| `sudo ls -l /var/lib/c3po/previous-c3po-bridge.service`, then `./scripts/robot/c3po install` again, then the same `ls` and `cat` | Nothing changed: same time on the backup, same three files                                                                                          |
| Roll back as in `docs/OPERATIONS.md` §5 ("Rollback"), then `c3po status`                                                         | The bridge answers on the older commit. `deployed` names it (if not, record it as the recipe says), and `previous-deploy` names the commit you left |
| Go forward again as in the same section                                                                                          | `deployed` names the newer commit again, the bridge answers, and `/safety/status` reads as it did                                                   |

## If something goes wrong

- Remote first. Then PARAR. Then stop the chat/voice turn.
- A stuck **PARAR ACTIVO**: **Reanudar** (admin), from the console chip or the VR menu.
  Re-engaging the headset no longer clears it. With no `SAFETY_CONFIRM_SECRET` in both
  `.env` files nothing clears it: set it, restart `back` and the bridge, then **Reanudar**.
- Tools refused with "needs an operator's confirmation": that is the T2 gate, press the card's
  **Confirmar**.
- A walk refused with `fsm_not_walkable`: the robot is not in 501 (802 after R2+A). Redo the
  bring-up. With `arm_held`: `release_arm_control` first. With `walk_task_active`: another
  walk runs; `cancel_task` it or wait.
