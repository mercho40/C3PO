# Deep audit: bugs, risks, and what to adopt (2026-11-03)

This audit covers branch `feat/autonomy` at commit 3b0cae6. Nine read-only auditors ran in parallel, one per area:

1. bridge safety and concurrency
2. robot SDK/DDS, compared with Unitree's upstream code
3. kinematics and manipulation, checked numerically against pinocchio
4. apps/back agent and voice
5. security
6. apps/web console
7. perception
8. technology scouting
9. tests, CI, and drift between docs and code

The **[V]** mark means the main session re-read the cited code and confirmed the finding. The other findings come from the auditor and cite file:line; they have not been re-checked.

No code was changed. The scripts that produced the numbers below are in [`evidence/`](evidence/).

**Bottom line.** The architecture is sound and the test suites are green: bridge 924, back 164, web 598, perception 314. The FK and camera maths match pinocchio to 1e-16.

The serious problems sit in three places:

1. **What happens after a stop.** The robot can move again, or keep moving, after PARAR. §1 covers this.
2. **Who is allowed to move the robot.** Open sign-up, an unauthenticated headset socket, and tools that are not gated. §2 covers this.
3. **The camera pitch.** Every range, plane, and person-distance check is built on it. §3 covers this.

All three can be fixed in small amounts of code.

---

## 0. Before the robot moves tomorrow

These are ordered by risk. Each one is a small change, and none of them weakens the e-stop.

| #   | Fix                                                                                                                                                     | Where                                                        | Why                                                                                                           |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| 1   | `request_release()` sets `self._target = list(self._current)`                                                                                           | `teleop/arm_sdk.py:334` **[V]**                              | An abort or stop does not freeze the arm (§1.2)                                                               |
| 2   | Refuse T1/T2 tools while `estop.stop_is_standing()`, and add an operator ack route                                                                      | `mcp_server.py:121` `GovernedMCP.call_tool` **[V]**          | After PARAR, the agent's next step moves the robot again (§1.1)                                               |
| 3   | `C3PO_CAMERA_PITCH_DEG=47.6` as the default, or read it from URDF `d435_joint`                                                                          | `apps/perception/vision/c3po_vision/grounding.py` **[V]**    | The person rule is blind at 0.5–0.8 m, and the glass/plane fallback is inverted (§3)                          |
| 4   | `danger_level == "high"` makes a tool T2 for every classification, plus a test. Also T2: `damp`, `zero_torque`, `arm_navigation`, `start_walking_waist` | `skill_meta.py:65` **[V]**                                   | The LLM can open the cmd_vel gate or drop the robot with no confirmation                                      |
| 5   | Hold waist slots 12–14 at their measured q (mode 1, kp 60 / kd 1.5) while arm_sdk is engaged                                                            | `teleop/arm_sdk.py:465` **[V code]**                         | Every upstream example does this; without it the torso bends forward (sdk2_python #146)                       |
| 6   | Catch the `speechTail` and `this.tail` rejections                                                                                                       | `voice/conversation.ts:157` **[V]**, `voice/realtime.ts:126` | One failed `say` kills apps/back, and console PARAR goes with it                                              |
| 7   | Treat stale perception as an abort reason for arm motion, and check the abort during the `hand`/`wait` steps                                            | `skills/manipulation.py:243,318` **[V]**                     | If the detector dies, the arm keeps going                                                                     |
| 8   | Pick and place: Cartesian sub-waypoints about 1 cm apart, plus clamping to `retarget.LIMITS`                                                            | `skills/manipulation.py:325`                                 | The 10 cm approach sweeps sideways (median 5.3 cm, max 15.8 cm); 122/162 waypoints exceed the teleop envelope |
| 9   | `TELEOP_HOST` defaults to `127.0.0.1` and `serve(..., origins=[...])`                                                                                   | `scripts/robot/run_teleop:59` **[V]**                        | Anyone on the Wi-Fi can drive the base                                                                        |
| 10  | `disableSignUp: true`, an admin role check on `/agent` and `/voice`, and bind back to loopback unless needed                                            | `apps/back/src/lib/auth.ts:31`, `index.ts:84` **[V]**        | Anyone can sign up and drive the robot through the agent                                                      |

**Status 2026-11-03 (pre-robot):** #1, #6, #7 done (uncommitted). #2 and #4 as written are unsafe to ship alone: every console and headset skill click goes through the same `call_tool` gate, so #2 would refuse the headset bring-up after any console PARAR until an ssh ack, and #4 would make `damp`/`start_walking_waist` (the headset's wake-up sequence, and damp is the safe state) need a T2 confirm that no UI can give. Both need the console ack/confirm buttons first. #9 conflicts with the headset's direct LAN connection (`run_teleop`: "Direct LAN access is the onboard policy"); use an Origin allowlist instead of a loopback bind.

If only some of these get done, do #1–#3 and keep the arm tools off with `TELEOP_ARM_ENABLED=0` until #5, #7 and #8 land. Add #5, #7 and #8 to `test-day/manipulation.md` as preconditions.

---

## 1. Stopping: the robot can move after PARAR

### 1.1 The e-stop latch is not enforced where the agent calls tools [V] (high)

- `stop_everything` latches the stop (`estop.signal_stop()`). Only `teleop/server.py` and `look_around` (`mcp_server.py:2907`) read the latch.
- `GovernedMCP.call_tool` never checks `stop_is_standing()`.
- apps/back keeps no registry of running turns, so PARAR aborts nothing (`routes/skills.ts:233`, `agent/runtime.ts:396`). The cancelled tool returns `cancelled`, and the system prompt says "retry a step at most twice".
- Scenario: in the middle of an errand the operator presses PARAR. The walk is cancelled. The model's next step calls `walk_to` again, and the robot walks.

Fix, in two parts:

- **Bridge:** refuse every tool except T0 while the stop is standing. Add `POST /safety/ack`, admin-only, to clear it.
- **Back:** keep a `Set<AbortController>` of active turns, and abort them from every stop path: the skills invoke for SAFETY_SKILLS, `/tasks` estop, and voice stop. Have the bridge answer `reason: "operator_estop"`, and add a prompt rule to never retry after it.

### 1.2 Releasing the arm keeps it moving for up to about 69° [V] (high)

- `arm_sdk._run` freezes the target only when not releasing (`:487-497`), and `_slew()` runs every tick.
- During the 2 s weight ramp the arm keeps slewing toward its old waypoint at 0.6 rad/s, so up to 1.2 rad. Meanwhile the blend pulls it toward the firmware posture.
- This affects every stop path: stop_everything, cancel, and `aborted:person_in_reach`.
- It contradicts the `release()` docstring, which says "Holds the last commanded pose throughout".
- The tests miss it because `FakeArm.request_release` only sets a flag.
- Fix: a one-line change, plus a test using a fake that slews.

### 1.3 Legacy voice: "para" waits behind the whole agent turn (high)

- `voice/loop.ts:172-205` awaits `runAgent` inside the event `for await`. A stop event therefore waits for LLM steps, a `walk_to` of up to 300 s, and a blocking `say`.
- `/voice/stop` awaits the same task.
- The bridge's local stop phrase is armed only on the realtime path (`mcp_server.py:3898`).
- Fix: pump events separately and dispatch `stop` immediately, aborting the turn. Also arm the bridge `on_stop` from the legacy engine.

### 1.4 Other stop-path issues

| Severity | Issue                                                                                                                                                                                  | Where                                                             | Fix                                                                                |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| high     | **No in-headset e-stop in the default `immersive-vr` mode.** The DOM overlay is not composited, and neither the WebGL menu nor the controllers call `estop.press()` [V]                | `web/lib/webxr/xr-teleop.ts:747-784`, `vr-control.svelte.ts:1116` | Map B/Y and the grips to `estop.press()`; make PARAR menu item 0                   |
| medium   | One sport client with a 10 s timeout carries the 0.4 s zero burst, Damp, velocity, and the FSM poller. One lost reply stalls the stop for 10 s [V]                                     | `sdk/g1_rpc.py:40`                                                | A separate stop/velocity client with ~0.3 s timeout, or `_CallNoReply`             |
| medium   | A blocking `urlopen` (1.5 s) runs on the bridge event loop from async camera routes, which stalls `stop_everything` over HTTP                                                          | `sdk/camera_relay.py:64`                                          | `asyncio.to_thread`                                                                |
| medium   | `gesture.py` and `_g1_request.py` catch only `Exception`. A `CancelledError` pins the task as "running" forever and blocks move_arm/pick. stop_everything never sends RELEASE_ARM (99) | `skills/gesture.py:193`, `_g1_request.py:117`                     | One shared task-lifecycle context manager                                          |
| medium   | T2 grants are keyed by tool name only, not arguments, and survive stop_everything                                                                                                      | `safety.py:251`                                                   | Key on `(tool, sha256(args))`, clear on stop, show the pending call in the console |
| medium   | No cross-process owner for `rt/arm_sdk`. The MCP and teleop processes each have their own driver. The Nav2 forwarder and arm_navigation ignore the teleop lease                        | `arm_sdk.py:552`, `perception_link.py:781`                        | One "motion owner" lease per resource                                              |
| medium   | arm_sdk never re-checks FSM or lowstate after engaging, and never turns off `g1_arm_example`. It keeps publishing w=1 into Damp or a fall                                              | `arm_sdk.py:297-305,486`                                          | Release when FSM leaves {4, 500, 501} or lowstate goes stale                       |
| medium   | The Nav2 collision monitor (Humble) fails open on stale `/scan`, and the bridge gate ignores `lidar_online`                                                                            | `nav2_params.yaml:413`, `perception_link.py:440`                  | Brake or close the gate when `lidar_online: false`                                 |
| medium   | The e-stop POST shares the HTTP/1.1 limit of 6 connections with MJPEG and the pollers (suspected)                                                                                      | `web/lib/robot/estop.svelte.ts:92`                                | Pause pollers while `estop.busy`; HTTP/2 proxy or WebRTC camera                    |
| low      | stop_everything relaxes the hands (first call builds the publisher, no timeout) _before_ the zero burst. The Damp fallback is not wrapped in try                                       | `skills/stop_everything.py:228,262`                               | Burst first, or `asyncio.gather` with timeouts                                     |
| low      | The watchdog sets an `asyncio.Event` from a thread and does not close the Nav2 gate                                                                                                    | `watchdog.py:200`                                                 | `call_soon_threadsafe`; `get_link().disable()`                                     |
| low      | The `/tasks` estop has no 8 s race (it can wait for `CALL_TIMEOUT_MS`)                                                                                                                 | `back/routes/tasks.ts:47`                                         | Reuse `SAFETY_TIMEOUT_MS`                                                          |
| low      | The walk_to deadline uses `time.time()`, and the Jetson has no RTC                                                                                                                     | `skills/walk_to.py:128`                                           | `time.monotonic()`                                                                 |

---

## 2. Who can move the robot (security)

| Severity | Finding                                                                                                                                                                                                                                                                                   | Who can exploit it                  | Where                                                       |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------- |
| high     | **The teleop WebSocket is open to the LAN with no auth and no Origin check.** Anyone on the Wi-Fi can walk the base (capped at 0.3 m/s) or take the session slot [V]                                                                                                                      | LAN                                 | `run_teleop:59`, `teleop/server.py:916`                     |
| high     | **Sign-up is open and `/agent` only requires a session** (no role). Back listens on all interfaces. `messages: t.Any()` lets a client forge assistant and tool history [V]                                                                                                                | LAN, any user                       | `back/lib/auth.ts:31`, `index.ts:84`, `routes/agent.ts:141` |
| high     | **The admin bootstrap promotes unverified emails.** Whoever signs up first with an `ADMIN_EMAILS` address becomes admin at restart [V]                                                                                                                                                    | LAN                                 | `back/lib/admin-bootstrap.ts:64`                            |
| high     | **The tier rule** (see §0 #4): `damp`, `zero_torque`, `arm_navigation`, `hug`, `dance`, `walk_to` all have no human gate. The person rule only covers T2. The only defence against prompt injection is the prompt text. Voice mode offers `damp` and `zero_torque` to anyone near the mic | the LLM via scene text, a bystander | `skill_meta.py:65`, `safety.py:279`, `runtime.ts:191`       |
| medium   | The bridge's HTTP `custom_route`s have no auth. The mcp DNS-rebinding guard covers `/mcp` only. The audio-output POST is CORS-simple                                                                                                                                                      | local processes, tunnel             | `mcp_server.py:3910`                                        |
| medium   | `javascript:` links in LLM markdown are not stripped (svelte-exmarkdown uses `normalizeUri`). A click can POST `/confirm` (suspected)                                                                                                                                                     | the LLM via scene text              | `web/.../response/response.svelte:51`                       |
| low      | Better Auth rate limiting is off outside production. The confirm route can be hit by CSRF from same-site localhost pages (bodyless POST, Lax cookie)                                                                                                                                      | LAN                                 | `back/package.json:14`, `routes/skills.ts:281`              |

**No console UI for T2 [V].** Nothing in apps/web calls `POST /skills/:name/confirm`. Every T2 call dead-ends, which pushes people toward `SAFETY_T2_CONFIRM=off`.

The fix: show an admin-only "Autorizar (30 s)" button on the refusal in the chat tool part, showing the exact pending args, plus `tier` in the catalogue. AI SDK 7's `needsApproval` (already installed, `ai@7.0.140`) does this inside the stream, so the agent waits instead of being refused.

**What is already right:**

- The T2 secret is compared timing-safe (`hmac.compare_digest`), the grant is single-use and has a monotonic TTL, and an empty secret fails closed.
- The bridge binds to loopback, and a test enforces it.
- CORS is limited to `/camera/*`.
- The cmd_vel gate is closed by default.
- No SSRF.
- No secrets committed.

**Dependencies.**

- `bun audit` reports 79 advisories, all transitive. Reachable: `undici` 7.29 (fixed in ≥7.30.1) and `fast-uri`. Most are fixed by `bun audit fix` within existing ranges.
- Bridge pip-audit:
  - `mcp 1.27.1` needs **≥1.30** (session routing; CVE-2026-59951 is a memory DoS from stateful sessions on the Jetson).
  - `starlette 1.0.0`: Host header issue.
  - Also `pyjwt`, `python-multipart`, `cryptography`.
  - Keep `mcp` below 2: 2.3.0 is a major release (§6).
- Perception: clean.

**Robot firmware and UniBLEed.**

- `docs/ROBOT-API.md:118` records firmware **1.5.3.8**, not ≤1.5.2. Confirm which is true.
- Things to check on the robot:
  - TCP **9991** (CVE-2026-76639, the WebRTC→DDS bridge, fixed after 1.5.2)
  - BLE provisioning (CVE-2026-76640, no fixed version)
  - default `unitree` credentials
  - teleimager on 55555/60000 bound to 0.0.0.0
- Set `DDS_INTERFACE=eth0`. If left blank, autodetect can bind wlan0, and DDS domain 0 accepts any publisher on that segment.

---

## 3. Perception geometry

### 3.1 The camera pitch is wrong, and more depends on it than we thought [V] (high)

The vision container assumes pitch 0°, but the D435 is mounted 47.6° nose-down. The URDF says so, and pinocchio confirms it. Three things go wrong as a result:

- **The person rule is blind at 0.5–0.8 m.**

  | True distance | Reported `range_m` | Result      |
  | ------------- | ------------------ | ----------- |
  | 0.3 m         | 0.64 m             | abort fires |
  | 0.5 m         | 0.99 m             | no abort    |
  | 0.8 m         | 1.25 m             | no abort    |

  The threshold is 0.8 m. Script: `evidence/kinematics/person_range.py`.

- **The glass/hole plane fallback is inverted** (`grounding.py:637`, `plane_tilt_deg` uses the configured pitch). At the real mount, a real table reads 47.6° and is rejected because the limit is 45°. A wall facing the robot reads 42.4° and is accepted as a "table". So a glass on a table gets the wall's depth, and `pick` reaches for the wrong point.
- **Errors at the image edges.** Range is 45–100 % too long and bearing is off by up to 18°.

Fix:

- Make 47.6° the default, or derive it from URDF `d435_joint`.
- Add plane tests at the real pitch; every test today uses `LEVEL_CAM`.
- Optionally, compute person range bridge-side from `cam_xyz` with `kinematics.camera_to_torso`.

### 3.2 Other perception findings

| Severity           | Finding                                                                                                                                                                                                                                                                                                     | Where                                              |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| high (suspected)   | The Mid-360 pitch (URDF 0.051 rad, vendor attitude −2.3°) is not modelled: `odom→camera_init` is roll π only and FAST-LIO does no gravity alignment. The floor would cross `min_height 0.08` at about 2 m, giving a phantom arc in `/scan`. **Robot check:** `ros2 topic echo /scan --once` in an open room | `g1_odom_tf.py:168`                                |
| medium             | Detection age is lost: the detector stamps at publish time, and the world-model publisher re-emits objects with `stamp_unix=now` for up to 1.5 s. A pick can use a `cam_xyz` about 1.7 s old [V]                                                                                                            | `detector.py:1178`, `world_model_publisher.py:250` |
| medium             | The TRT engine cache is keyed only by filename. Re-exporting under the same name means wrong labels, silently. A corrupt plan means the container stays down (`--restart no`)                                                                                                                               | `vision/entrypoint.sh:53`                          |
| medium             | The nav heartbeat pose is never aged (`lookup_transform(..., Time())`). If FAST-LIO dies, a frozen pose is published as "now" and landmarks are saved at it                                                                                                                                                 | `goal_relay.py` `_pose()`                          |
| medium (suspected) | slam_toolbox localisation always starts at the dock (`map_start_at_dock: True`) and nothing publishes `/initialpose`                                                                                                                                                                                        | `nav2.launch.py`                                   |
| low (suspected)    | The colour optical origin may sit about 15 mm from `d435_link`: a 1.5 cm lateral bias against a 1 cm IK tolerance                                                                                                                                                                                           | `kinematics.py:386`                                |

---

## 4. Arms (Phase 5): verified maths, unsafe execution

**Numbers** (`evidence/kinematics/`):

- **FK and camera mount vs pinocchio:** max error 3.5e-16 m over 100 random full-range configurations per arm. Joint limits and chain order match exactly.
- **IK:** 195/200 position+direction, 200/200 position-only, 0 limit violations. Median 4 ms, max 188 ms on an M-series Mac.
- **Plan success:** 430/1170 on the workspace-box grid (37 %). The box is much larger than the reachable set.

| Severity            | Finding                                                                                                                                                                                                                                                                                                                          | Fix                                                                                                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| high                | **The approach is not a straight line.** Per-joint rate limiting (`arm_sdk.py:452`) means short joints finish first. Deviation from the pre-grasp→grasp segment: median 5.3 cm, max 15.8 cm. Rest→pre-grasp passes within 5 cm of the object in 14 % of plans. Between waypoints, joints jump by up to 5.3 rad (IK branch flips) | Cartesian sub-waypoints about 1 cm apart, each seeded from the previous (gives 0.1 cm), and reject jumps over 0.3 rad. Or synchronise the joints in the driver (gives 0.7 cm) |
| high                | **Manipulation bypasses `retarget.LIMITS`**, which every other arm path clamps to. 122/162 waypoints exceed it, by up to 65° on wrist_roll. The wrist signs are unverified [V: no clamp in `manipulation.py:325`]                                                                                                                | Clamp to the LIMITS-derived bounds (pass `joint_limits=` to `solve`) until `arm_sign_check` covers the wrists                                                                 |
| high                | Waist slots 12–14 are left uncommanded (§0 #5)                                                                                                                                                                                                                                                                                   | Hold at the measured q                                                                                                                                                        |
| med-high            | Stale perception during motion means the arm continues (`fresh and ...` in `_abort_reason`) [V]                                                                                                                                                                                                                                  | `not fresh` becomes an abort, except during handover                                                                                                                          |
| medium              | No abort check during the `hand` and 0.8 s `wait` steps [V]                                                                                                                                                                                                                                                                      | Poll the abort inside the wait                                                                                                                                                |
| medium              | IK runs synchronously on the event loop (~190 ms for an unreachable target on a Mac, slower on the Orin) and stalls the 50 Hz arm loop while holding                                                                                                                                                                             | `asyncio.to_thread(plan_pick, ...)`                                                                                                                                           |
| medium              | Palm roll is free (−102° to +113° across seeds) and the TCP sits on the finger axis. The 0° horizontal approach wins 75 % of plans and has no table model, so short objects mean fingers hit the table                                                                                                                           | Constrain the palm normal; prefer 30–60° from above when `depth_source="plane"`; measure the TCP                                                                              |
| low-med             | "Settled" compares the command, not lowstate `arm_q`, so the hand may close before the arm has arrived                                                                                                                                                                                                                           | Also gate on measured q                                                                                                                                                       |
| known (a), deepened | URDF `right_shoulder_roll` is −129°/+91°. The large range is abduction, which is independent evidence that −roll = away. That contradicts `JOINT_SIGNS`. `manipulation.md` §1 stays mandatory                                                                                                                                    |                                                                                                                                                                               |

**Recommendation:**

1. Fix execution first, in the existing pure-Python code (S).
2. Then add `pin` + **pink** + **coal** to the bridge (M). They are one wheel set, give 6-D frame tasks with real roll control, and add self- and table-collision checking. FK is already proven identical.
3. Use **MuJoCo Menagerie `unitree_g1`** as the arm test harness, which would have caught §1.2 and the sweep.
4. Borrow xr_teleoperate's IK cost terms (`50·pos + rot + 0.02‖q‖² + 0.1‖q−q_last‖²`, TCP 0.05 m).

---

## 5. apps/back agent, voice, and eval

Positives: no motion call is ever retried, tool schemas come live from the bridge (no drift), and unoffered tools are refused.

| Severity | Finding                                                                                                                                                                                                                                                                                                                                     | Where                                                    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| high     | Unhandled rejections exit Bun in two places, reproduced in `evidence/back/conv.ts` and `sink.ts` [V]. There is no `unhandledRejection` handler anywhere                                                                                                                                                                                     | `voice/conversation.ts:157`, `voice/realtime.ts:126-148` |
| medium   | Any Realtime `error` event is fatal. `response.create` is sent before `response.done`, which is the documented cause of `conversation_already_has_active_response`                                                                                                                                                                          | `voice/realtime.ts:327,447`                              |
| medium   | An odd-length PCM chunk throws and ends the session                                                                                                                                                                                                                                                                                         | `voice/pcm.ts:13`                                        |
| medium   | The abort signal never reaches tool `execute`. One `walk_to` (300 s) can outlast the 240 s turn budget                                                                                                                                                                                                                                      | `agent/runtime.ts:302`                                   |
| medium   | Chat history is unbounded (the full client history every turn, plus the describe_surroundings JSON). Use `pruneMessages`                                                                                                                                                                                                                    | `agent/runtime.ts:404`                                   |
| medium   | **The eval does not measure what PLAN asks.** It scores tool _names_, so an errored or refused call passes. It records no model. PLAN's metrics are missing (interventions, e-stops, gate trips, p95). `motion_tools` lacks `navigate_to_landmark` and `release_arm_control` and lists `explore`/`follow_person`, which don't exist yet [V] | `scripts/eval/suite.ts:124`, `eval/tasks.yaml:30`        |
| low      | Two operator turns can drive the robot at the same time (no per-robot lock) (suspected)                                                                                                                                                                                                                                                     | `routes/agent.ts:101`                                    |
| low      | The PCM resampler interpolates linearly with no low-pass filter (24→16 kHz aliasing)                                                                                                                                                                                                                                                        | `voice/pcm.ts:21`                                        |

### Web console, non-safety

These are on top of the high items above (no headset PARAR, no T2 UI):

- The headset radar's `fetch` has no timeout and keeps looking fresh after the Wi-Fi drops (`lib/robot/scan.svelte.ts:51`). Move it onto `Poller`.
- The XR camera texture is re-uploaded per eye per frame, roughly 200 × 1080p uploads per second (`webxr/camera-layer.ts:485`). That causes Quest judder, and head pose drives yaw.
- A PARAR press before hydration is lost (suspected). Fix: a `<form action>` fallback.
- Otherwise the console is well hardened: the poller policy, hold-to-confirm, and SSR safety are all fine. Do not adopt TanStack Query.

---

## 6. Tests, CI, and drift

| Severity | Finding                                                                                                                                                                                                                    | Fix                                                                |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| high     | **The spoken-stop tests never run, in CI or locally.** All 13 bridge skips are `test_listen.py` (they need vosk/piper/whisper), and CI syncs without `--extra voice`                                                       | A voice CI job that fails on skips                                 |
| high     | The eval's motion list is wrong (see §5)                                                                                                                                                                                   | Generate it from the catalogue (`tier != T0`) plus a contract test |
| medium   | `graphify-out/` is ignored only through the local `.git/info/exclude`: another clone would commit 18 MB, and `prettier --check` fails on 104 files                                                                         | Add it to `.gitignore` and `.prettierignore`                       |
| medium   | `scripts/robot/bridge_sync:41` runs `uv sync` without `--locked`, and `mcp[cli]>=1.10` has no upper bound (2.3.0 is out)                                                                                                   | `--locked`, `mcp[cli]>=1.30,<2`                                    |
| medium   | Turbo `"test": {}` misses inputs from other packages (tasks.yaml, the perception XML, `_common.sh`), so a local cached pass can be stale. `@repo/bridge#build` is cached without `.venv`                                   | `$TURBO_ROOT$` inputs; `cache: false` on the bridge build          |
| medium   | `mjpeg-camera.test.ts` uses real timers: 27–112 s. CI runs the web tests twice                                                                                                                                             | Fake timers; drop the duplicate step                               |
| low      | Perception decode tests are skipped (numpy is not in the dev group). There are no tests for `world_model_publisher`, `goal_relay`, or `g1_odom_tf`                                                                         | Add numpy to dev                                                   |
| low      | Stale test counts in `test-day/README.md:40` (906) and `memory-eval.md` (841/155). STT port documented as `:8081`, actually 8082. CI never builds the apps. The eval writes into `docs/`                                   | Say "0 failed"; fix the port; add a build step                     |
| low      | About 40 env vars are read but undocumented (`OBJECT_MEMORY_PATH`, `PIPER_*`, `VOSK_MODEL`, `WHISPER_*`, `C3PO_STT_*`, ~25 `C3PO_VISION_*`). `DEEPGRAM`/`CARTESIA`/`WAKE_WORD_MODEL`/`LOG_LEVEL` are documented but unread | One generated table                                                |

**Python 3.8 in the containers.** Run the pure-Python perception tests under `uv run --python 3.8` in CI. Ruff's py38 target misses newer stdlib APIs. Also pin the base image by digest; `FASTLIO_REF=ROS2` is a moving branch.

---

## 7. Technology worth adopting

These are ranked by payoff for effort. Each repo was checked to exist (with its date and licence) on 2026-11-03.

| Rank | What                                                                                                                                                       | Gives C3PO                                                                                                                                         | Fit                   | Effort |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- | ------ |
| 1    | **Stand audio kit:** a wireless handheld/lav mic, a local PA, and AEC (`livekit rtc.AudioProcessingModule` or PipeWire `module-echo-cancel`)               | Voice that works at a noisy expo, the biggest demo risk                                                                                            | LAN PC                | S      |
| 2    | **OpenSLR 61**: 5,739 Buenos Aires utterances, CC BY-SA                                                                                                    | An immediate Rioplatense STT benchmark for PLAN V.5                                                                                                | offline               | S      |
| 3    | **AI SDK 7 `needsApproval` + `ToolLoopAgent` + `pruneMessages` + `experimental_telemetry`** (→ Langfuse/OTel)                                              | An in-stream T2 approval UI, bounded context, and the eval metrics PLAN wants                                                                      | already installed     | S–M    |
| 4    | **Short-timeout / `_CallNoReply` velocity client**, plus a hand-written `SportModeState_` IDL on `rt/sportmodestate`                                       | A faster e-stop; the FSM as a stream instead of a 0.5 s RPC poll                                                                                   | bridge                | S      |
| 5    | **pin + pink + coal**                                                                                                                                      | 6-D IK with roll control, velocity limits, self/table collision                                                                                    | aarch64 Py3.12 wheels | M      |
| 6    | **sherpa-onnx + Parakeet-TDT-0.6B-v3** (es WER 3.45 % FLEURS)                                                                                              | One local runtime for VAD, ASR, and TTS; cp38 aarch64 wheels, so it runs on JP5 today. Keep Vosk for the stop phrase until Spanish KWS is verified | Orin JP5 / LAN PC     | S–M    |
| 7    | **MuJoCo Menagerie G1 / LeRobot G1 sim** (`is_simulation=true`)                                                                                            | A laptop twin for arm execution and look→walk→verify loops                                                                                         | laptop                | M      |
| 8    | **WebRTC for the real camera** (go2rtc/aiortc; the sim already uses WebRTC)                                                                                | Fixes the XR texture load and the HTTP/1.1 contention, plus real frame-freshness                                                                   | Jetson                | M      |
| 9    | **Official BrainCo stack** (`unitreerobotics/brainco_hand_service`, pinned; `BrainCoTech/brainco-description` Revo2 URDF→MJCF; the SDK is now MIT, Py≥3.9) | Revo2 models for the sim twin; a second state/current reader                                                                                       | Humble / Py3.9+       | S–M    |
| 10   | **Kimodo-G1** text-to-motion, generated offline and replayed **arm joints only** through arm_sdk caps                                                      | Gestures from a prompt ("hacé una reverencia") for the stand                                                                                       | offboard GPU          | M      |
| 11   | **Nav container → ROS 2 Jazzy** (no GPU, so independent of JP5)                                                                                            | Collision-monitor and MPPI fixes; avoids the Humble arm64 SIGILL (nav2#5061)                                                                       | Docker                | M      |
| 12   | Evaluate **Unitree `slam_operate`** (APIs 1801–1901, relocalisation)                                                                                       | Built-in relocalisation, which fixes "always starts at the dock"                                                                                   | vendor                | S      |
| 13   | **ReMEmbR pattern** in the existing Postgres + pgvector                                                                                                    | "When/where did you last see my keys?"                                                                                                             | cloud LLM             | M      |
| 14   | NanoOWL (query-time labels), EfficientSAM/NanoSAM masks, Depth Anything V2-S (hole filling)                                                                | A better "find X" fallback, grasp masks, glass depth                                                                                               | TRT on JP5            | M      |
| 15   | Azure `es-AR-ElenaNeural`/`TomasNeural` TTS (unverified on Microsoft's docs); Chatterbox V3 voice clone                                                    | An Argentine-accented or branded voice                                                                                                             | cloud / LAN GPU       | S / M  |
| —    | Gemini Robotics 2 / On-Device 2 trusted-tester waitlist                                                                                                    | A possible VLA in the ER ecosystem. Don't plan around it                                                                                           | —                     | S      |

**Corrections to our research and PLAN:**

- **PLAN 7.3:**
  - JP6.2 is Ubuntu 22.04 / Humble.
  - Jazzy, Isaac ROS 4.6+, and GR00T need **JetPack 7.2** (Ubuntu 24.04, CUDA 13.2, now supports Orin NX). GR00T dropped JP6.
  - NVIDIA's G1 JP6.2 guide runs `flash_nx_module.sh`, which touches the module's QSPI, so **the old JP5 SSD may no longer boot**. This is unverified. Decide before buying the SSD.
  - nvblox via Isaac ROS 2.1 needs JP **5.1.2** (L4T 35.4.1), not our 35.3.1.
- **Research 04:**
  - The BrainCo SDK is now MIT, not proprietary.
  - BeyondMimic is archived; use **mjlab** instead.
- **Piper** has moved to `OHF-Voice/piper1-gpl` (GPL-3.0).
- **SDK constants** 7108/7109 in `g1_protocol.py:170` are **H2-only**. Don't wire 7109 as "enable arm_sdk" on a G1.

**Don't bother:**

- any VLA onboard on JP5
- HOVER, GMT, AMO (stale)
- FALCON
- Genesis, ManiSkill
- the third-party Unitree MCP servers and nav2_mcp_server (no safety model; our bridge is ahead)
- RoboGuard code (read the paper only)
- Moshi, Qwen-Omni (no Rioplatense)
- cuRobo and GraspGen on this Jetson (Docker/Py3.10, unsupported grippers)

---

## 8. Not checked

- **Needs the robot:**
  - the waist hold
  - whether RELEASE_ARM interrupts a firmware gesture
  - the LiDAR tilt arc
  - real arm slew under blend
  - Orin IK timing
  - whether arm_sdk at w=1 makes the G1 ignore velocity
  - the firmware version and ports
- **Needs a device or browser:** the Quest e-stop gap, XR judder, the HTTP/1.1 queueing, and the `javascript:` link.
- **Not reviewed:**
  - `listen.py` voice internals
  - `teleop/protocol.py` frame validation
  - `db/chats.ts`
  - MPPI/BT XML tuning
  - Docker image CVEs
  - whether Docker `--memory` counts nvmap
- **Unverified scout claims:** Azure es-AR voices, sherpa Spanish KWS, the GR00T Orin benchmark (AGX vs NX), the ReMEmbR licence, and Chatterbox Spanish quality.
- **Out of scope:** the user's uncommitted WIP on `feat/real-g1-dispatch-and-hardening` and commits b08dc40/4a054ce.
