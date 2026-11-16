# C3PO: where the code stands (audit, 2026-11-02)

The plan document builds on this audit. It describes what the repo does today, so the plan can say what has to change to go from fixed voice skills to open-ended autonomous tasks. Nothing in the repo was modified.

## 0. Scope, and which branch counts as "today"

- **Audited checkout:** `c3po/` on `feat/real-g1-dispatch-and-hardening` @ `4a054ce` (2026-10-05). There are two uncommitted files: a camera-relay disconnect fix in `apps/bridge/src/bridge/mcp_server.py` (L2475-2500) and its test. The graphify graph was built from this checkout.
- **This branch is not the newest state.** Two other lines of work are ahead of it:
  - `origin/main` is 36 commits ahead.
  - `integrate/october` is 94 commits ahead. It is checked out as a worktree at `../c3po-integrate` @ `daa6b48` and is PR #3, which merges main into this branch.
  - These branches add three things that matter for this report: a second voice engine (OpenAI Realtime), free-form arm and hand skills (`move_arm`, `set_hand`, …), and a voice gate that blocks high-danger skills.
  - Every section below states the audited branch first. Deltas from `integrate/october` are marked **[integrate]**, with paths under `c3po-integrate/`.
- **Evidence tags used here:**
  - **[code]** means read in source.
  - **[doc]** means stated in the repo docs.
  - **[hw]** means the repo records it as observed on the robot.
  - **[inferred]** means my reading, not verified.
- **Ownership:** per the team's own note (not in the repo), the G1 has been C3PO-only since 2026-10-27. The `gemm` co-tenant's autostart is disabled, and its files must not be touched.

---

## 1. The voice path, end to end

### 1.1 What is wired (audited branch)

```
mic (multicast 239.168.123.161:5555, or a USB ALSA mic)
  -> bridge MicListener thread (apps/bridge/src/bridge/skills/listen.py)
       Vosk small-es, restricted grammar  -> stop phrase  (reported, optionally acted on)
       Vosk small-es, full vocabulary     -> utterance boundaries + short-utterance text
       Whisper (GPU in vision container, else CPU) -> text for utterances >= 2 s
  -> buffered; MCP tool `listen` drains it
  -> apps/back VoiceLoop polls `listen` every 1 s (apps/back/src/voice/loop.ts)
  -> runAgentChat(one user message)  -> TIC AI LLM with all bridge skills as tools
  -> tool calls -> bridge MCP over HTTP -> robot
```

**Speech goes to the LLM agent, not to a phrase-to-skill table.** I found no fixed mapping from phrases to skills anywhere in the audited code. The one fixed mapping is the stop phrase.

- **Audio source** [code]
  - `listen.py:51-52`: multicast group and port.
  - `iter_mic_frames` (L973): joins the group pinned to the eth0 address.
  - `describe_audio_source` (L691) and `default_source` prefer a real ALSA capture device (USB mic) over multicast. They filter out the Tegra `dlink`/`ADMAIF` pseudo-devices.
  - Override: `C3PO_AUDIO_SOURCE`.
- **Stop phrase** [code]
  - `DEFAULT_STOP_PHRASES = ["emergencia", "para para", "detener"]` (L84). It is matched as a substring on Vosk partial results (L120-137).
  - The file itself says the list is "NOT VALIDATED FOR SAFETY USE".
  - In the bridge the phrase is only _reported_ (MicListener docstring around L750). `VoiceLoop` in `apps/back` acts on it by calling `stop_everything` before the agent runs (`loop.ts:155-159`).
- **STT** [code]
  - Vosk `vosk-model-small-es-0.42` (39 MB, CPU) finds utterance boundaries (L208-232).
  - An utterance of 2 s or more (`WHISPER_MIN_SECONDS`, L280) is re-transcribed by Whisper. Shorter ones keep Vosk's text (`MicListener._run`, about L840-872).
  - The comment block at L259-280 records why: on synthetic audio, Whisper was 10-18x slower and _worse_ on short commands.
  - Whisper backend selection (`build_whisperer`, L424):
    - First choice is `POST /transcribe` on the vision container. That is whisper.cpp built with `GGML_CUDA=1`, model `ggml-base.bin`, port 8082 (`apps/perception/vision/c3po_vision/transcribe.py:42,168,229`).
    - Fallback is faster-whisper `base`/int8 on the CPU (L282-290).
    - Measured latency [hw/doc]: about 1.9 s on the GPU, 3.5-6.6 s on the CPU.
    - There is no Silero VAD. Vosk's endpointing is the only segmentation.
- **Voice loop** [code]
  - `apps/back/src/routes/voice.ts`: `POST /voice/start`, `/voice/stop`, `GET /voice/status`. All are auth-guarded and mounted at `apps/back/src/index.ts:55`. The loop is explicitly opt-in and never ambient.
  - `loop.ts:161-178` joins all pending utterances into one turn and awaits the agent, so only one agent run happens at a time.
  - Limitations in the audited branch:
    - **No dialogue history.** Each utterance is a fresh one-message conversation (`voice.ts:47-57`).
    - **The reply is not spoken** unless the model decides to call `say`. The system prompt never tells it to.
- **TTS** [code/hw]
  - `say(language="spanish")` uses Piper `es_AR/daniela`, resampled 22050 to 16000 Hz, sent over `PlayStream` (`skills/tts.py`; D6.2 in `docs/DECISIONS.md` around L430-450).
  - Confirmed audible on 2026-08-21.
  - `wait_for_completion` uses `rt/audio_msg` `play_state` so the robot does not hear itself (`docs/ARCHITECTURE.md` §4.1).

### 1.2 Wired, but never proven

- **The microphone.**
  - The `listen.py` header (L28-33) says "THE MICROPHONE IS NOT PROVEN".
  - `docs/ROBOT-HARDWARE.md` §8.2 (L1235-1346) records contradictory observations [hw]:
    - At rest: 0 packets.
    - Holding L1+L2 on the remote opens the feed. Live Spanish was transcribed through the full chain on 2026-08-21.
    - The same day, the feed was also seen free-running with no remote at all.
  - The working rule is "measure, don't assume". `listen` reports `audio_flowing` from packets actually observed.
  - D6.3 concludes that push-to-talk is the real interaction model, and that continuous listening needs a USB mic. None is fitted: the Jetson has no real capture device.
- **The voice loop end to end on the robot.**
  - `docs/OPERATIONS.md` §9 (about L715-726) lists "The voice loop end to end" under _"Written and never run on hardware."_
  - The pieces have run separately: the mic transcribed once, `say` was heard, and `apps/back` has listed tools through the tunnel.
  - Nobody has recorded a spoken sentence becoming a tool call on the real robot.
- **Doc drift.** `docs/ARCHITECTURE.md` §3 (L136, L158) still says voice is "designed, not built", while §4.1 (L223) and the code say it is built.

### 1.3 [integrate] What changed on the newer branches

- **Two voice engines**, chosen by `VOICE_ENGINE` (`c3po-integrate/apps/back/src/routes/voice.ts:86`; `.env.example:59-62`).
  - `legacy` (the default) is the pipeline above. It now goes through `VoiceConversation` (`voice/conversation.ts`):
    - bounded dialogue history;
    - sentence-by-sentence streaming to `say`;
    - `say` and `listen` withheld from the model.
  - `realtime` is the OpenAI Realtime API (`voice/realtime.ts:12-13,61-80`):
    - model `gpt-realtime-2.1`, voice `marin`;
    - `semantic_vad` turn detection, `gpt-4o-mini-transcribe` with language `es`;
    - audio piped through bridge routes `/telemetry/voice/audio/{input,output}` (L119, L263).
  - **The realtime engine is public cloud.** That contradicts D6.3 ("No cloud anywhere in the voice path"). The decision record has not been updated [inferred from the absence of a revision in the DECISIONS.md I read].
- **Voice cannot reach high-danger skills** in either engine (`agent/runtime.ts:134-156`, `offeredInMode`). Both `arm_navigation` and `move_arm` are high-danger, so a voice-only "fetch" task cannot arm navigation or move an arm freely.
- **A voice-specific prompt**, `VOICE_PREAMBLE` (L158-179):
  - converse naturally in Spanish;
  - only call tools for clear physical requests;
  - copy arguments exactly;
  - ask when something is ambiguous.

---

## 2. The agent

### 2.1 Model and provider

- **Runtime:** `apps/back/src/agent/runtime.ts`, using the Vercel AI SDK `streamText` with `@ai-sdk/openai-compatible`.
- **Provider:** TIC AI, ORT's on-campus OpenAI-compatible gateway.
  - Base URL (`AGENT_BASE_URL`, L64): `http://ia.ort.edu.ar/api/v1` on the audited branch. [integrate] changes it to `https://ia.ort.edu.ar/api/v1` (`runtime.ts:66`, `.env.example:50`).
  - `BRANCH-TEST-PLAN.md:56` instead names `https://ia-tic.ort.edu.ar/api/v1`. **The correct hostname is unresolved.**
- **Model:** `AGENT_MODEL` defaults to `tic-chat` (L68).
  - What `tic-chat` actually runs is unknown (D5.1, `DECISIONS.md:218-247`).
  - Its tool calling is confirmed exactly once (2026-08-18), with one trivial tool and two steps. _"Whether it holds up across a full multi-step skill loop is still open."_
  - There is no thinking/reasoning control (dropped when the Anthropic API was removed).
- **Rationale:** D5 (`DECISIONS.md:200-216`) rules out a local LLM on the Orin, because 3B INT4 to 8B Q4 is too weak and would contend with YOLO, and keeps the LLM out of every control loop.
- **Reachability:** the gateway is reachable only from the school network. On 2026-10-15 it failed until Cloudflare WARP was turned off (`c3po-integrate/docs/BRANCH-TEST-PLAN.md:56-58`).
- **Second, independent driver:** Claude Code or any MCP client can drive the bridge directly over MCP (`.mcp.json`; ARCHITECTURE §3). That path is separate from TIC AI and also first-class.

### 2.2 System prompt

`SYSTEM_PREAMBLE` (`runtime.ts:95-121`) says:

- call `get_state` to learn `env` (stub/isaac/real) before the first motion;
- report what actually happened;
- `stop_everything` exists;
- respect preconditions;
- confirm before high-danger skills;
- assume people may be near the real robot.

`buildSystemPrompt` (L125-138) then appends one line per skill: `name [classification/status/sim+real|real-only|…/danger:X] — description`.

**What the prompt lacks:**

- It never mentions `describe_surroundings`, the world model or landmarks, and gives no planning guidance. `describe_surroundings` appears 0 times in `runtime.ts`.

### 2.3 How tools, results and world state flow

- **Source of truth for the catalogue** [code]: the bridge's MCP `listTools`, with pydantic JSON Schema plus `_meta.c3po` (`apps/bridge/src/bridge/skill_meta.py`). `apps/back/src/skills/catalogue.ts` derives the catalogue from it and caches the last good copy.
- **Dispatch** (`runtime.ts:149-195`): each skill becomes an AI SDK `tool`, and `execute` calls `callTool` over MCP streamable HTTP (`apps/back/src/bridge/client.ts`).
  - Errors come back to the model as ordinary tool results (`{error: "tool_error: …" | "bridge_unavailable"}`).
  - Every call writes a `tool_call_log` row.
- **World state is pull-only.** The model sees the robot only through tool results: `get_state`, `describe_surroundings`, `list_active_tasks`, `recall_landmark`, `listen`. Nothing is injected into its context automatically, and no perception events push to it.
- **Loop limit:** `stopWhen: stepCountIs(MAX_STEPS)`, default 12 (L73-75, L238), with a guard against 0/NaN meaning "unbounded".
- **Long skills block.** `walk_to` and `turn` hold the MCP call until the motion finishes.
  - True fire-and-forget (return a `task_id`, poll progress) is "the planned next step" (`skills/task_runtime.py:1-20`; ARCHITECTURE §6).
  - The MCP SDK request timeout cut long walks off at 60 s on the audited branch; [integrate] commit `11c55d7` fixed that.
- **Direct invocation:** `apps/back/src/routes/skills.ts:133-137` lets only admins call skills directly. `stop_everything` (safety) is exempt. Everyone else must go through `/agent`.

### 2.4 Skill catalogue

The audited branch exposes **33 MCP tools**, the same set to Claude Code and to the internal agent. OPERATIONS §9 records "33 tools listed" through the tunnel. `works_real` is set true only where a human watched it work (`skill_meta.py:17-21`).

| Tool (mcp_server.py line)                                                                                        | Class         | Danger         | works sim/real | Notes                                                                                                                                            |
| ---------------------------------------------------------------------------------------------------------------- | ------------- | -------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `get_state` (128)                                                                                                | introspection | low            | Y/Y            | pose (odometry), battery, posture/FSM, faults, `env`                                                                                             |
| `walk_to` (190)                                                                                                  | locomotion    | medium         | Y/Y            | P-controller on odometry, world-frame XY; **no obstacle avoidance**; only hw evidence is 0.17 m on the gantry, 2026-08-15 (ROBOT-API §5.4, L984) |
| `turn` (293)                                                                                                     | locomotion    | low            | Y/Y            | closed loop measured 2026-08-26: 40° → about 37°, ±3.5° (docstring L335-366)                                                                     |
| `walk_velocity` (423)                                                                                            | locomotion    | medium         | N/N            | open-loop body velocity, ±0.3, ≤3 s; flag says "NOT YET LIVE-TESTED" (L418), see §3.5                                                            |
| `stop_everything` (538)                                                                                          | safety        | low            | Y/Y            | cancel tasks, zero-velocity burst, damp; also closes the Nav2 gate                                                                               |
| `damp` (603), `prepare` (630)                                                                                    | posture       | low            | N/Y            | FSM 1 / 4                                                                                                                                        |
| `start_walking_waist` (822)                                                                                      | posture       | **high**       | N/Y            | FSM 501, the walk program this 29-DoF body actually uses                                                                                         |
| `zero_torque` (729), `balance_stand` (860), `start_walking` (893), `sit_g1` (920), `lie_up` (947), `squat` (972) | posture       | low/med        | N/N            | accepted by firmware, never seen working                                                                                                         |
| `check_motion_mode` (755)                                                                                        | introspection | low            | N/N            |                                                                                                                                                  |
| `wave` (656), `shake_hand` (1005), `hug` (1031), `clap` (1058), `release_arm` (1084)                             | gesture       | low            | N/Y            | firmware preset arm actions (7106)                                                                                                               |
| `point_at` (690), `dance` (1114)                                                                                 | gesture       | low            | N/N            |                                                                                                                                                  |
| `say` (1152)                                                                                                     | speech        | low            | N/Y            | Piper Spanish → PlayStream                                                                                                                       |
| `listen` (1542)                                                                                                  | perception    | low            | N/Y            | drains the MicListener buffer                                                                                                                    |
| `arm_navigation` (1395) / `disarm_navigation` (1490)                                                             | locomotion    | **high** / low | Y/Y            | opens or closes the Nav2 `cmd_vel` gate (TTL 1-120 s)                                                                                            |
| `remember_landmark` (1692)                                                                                       | memory        | low            | Y/**N**        |                                                                                                                                                  |
| `recall_landmark` (1769), `list_landmarks` (1798), `forget_landmark` (1824)                                      | memory        | low            | Y/Y            |                                                                                                                                                  |
| `cancel_task` (1853), `list_active_tasks` (2047)                                                                 | task          | low            | Y/Y            |                                                                                                                                                  |
| `describe_surroundings` (2011)                                                                                   | perception    | low            | Y/Y            | the D7 snapshot (§3.3)                                                                                                                           |

**[integrate] adds 5 tools, for 38 in total**, all in `c3po-integrate/apps/bridge/src/bridge/mcp_server.py`:

| Tool (line)                  | Danger | works_real                  | What it does                                       |
| ---------------------------- | ------ | --------------------------- | -------------------------------------------------- |
| `gesture` (1167)             | low    | Y                           | any preset from the robot's 23-action table, by id |
| `move_arm` (1266)            | high   | **N** ("NEVER LIVE-TESTED") | joint-space arm pose over `rt/arm_sdk`             |
| `release_arm_control` (1356) | low    | N                           |                                                    |
| `set_hand` (1388)            | medium | N                           | BrainCo closure scalar                             |
| `open_hands` (1432)          | low    | N                           |                                                    |

### 2.5 Safety gates

The layers in ARCHITECTURE §7 (L336-386), plus a few others:

1. **The physical remote / e-stop** is authoritative.
2. **Firmware velocity deadman.** Every `SET_VELOCITY` carries `duration: 1.0` s (`skills/_locomotion.py`), so the robot stops within a second if the bridge dies.
3. **`stop_everything`** cancels all tasks, sends a zero-velocity burst, then damps. It also closes the perception gate.
   - It works across processes through an mtime file (`apps/bridge/src/bridge/estop.py:1-30`). That fix exists because teleop once ignored PARAR for 27°.
   - [integrate] `1e91aad`: PARAR now damps only if the burst was not acknowledged. Damping a walking robot would drop it.
4. **Cooperative cancel** via `task.cancel_event`.
5. **Link watchdog** (`watchdog.py`) is **disarmed by default** (`LINK_WATCHDOG=` empty, `apps/bridge/.env.example:65`). It cannot be armed usefully while tool calls block.
6. **Perception `cmd_vel` gate**, closed by default (`apps/bridge/src/bridge/sdk/perception_link.py`):
   - opened only by `arm_navigation`, with a TTL (`ARM_TTL_S = 60`, L123);
   - its own staleness deadman `CMD_VEL_DEADMAN_S = 0.3` (L99) plus a 0.5 s brake;
   - clamps vx [-0.20, 0.50], vy ±0.20, wz ±0.80 (L135). These are "reasoned defaults, not measurements" (perception README L325).
7. **Teleop hardware gates:** `TELEOP_ARM_ENABLED=1` (`teleop/arm_sdk.py:38,225`) and `TELEOP_HAND_ENABLED=1` (`teleop/hands.py:282`). Both default off (D9.1).
8. **Precondition and metadata checks:** FSM preconditions, `works_real` flags, the `teleop_conflict()` refusal in `walk_to.py`, and admin-only direct invocation.
9. **[integrate]** The voice-mode high-danger exclusion (§1.3).

**One-commander invariant.** Nothing technical arbitrates between C3PO and other DDS commanders: every vendor client uses `enableLease=false`. `scripts/robot/_common.sh` refuses to start `run_c3po` while a known other commander is alive (ARCHITECTURE L377-386).

---

## 3. Perception

### 3.1 Layout

Two containers run on the Jetson (`apps/perception/README.md` L45-68).

|            | `c3po-perception-nav`                                                   | `c3po-perception-vision`                                                                                                     |
| ---------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Base image | `ros:humble` on Ubuntu 22.04, no GPU                                    | `nvcr.io/nvidia/l4t-jetpack:r35.3.1`, **Ubuntu 20.04, Python 3.8**, `--runtime nvidia`, about 10 GB                          |
| ROS        | yes                                                                     | none                                                                                                                         |
| Contains   | Livox driver, FAST-LIO2, Nav2 (DWB), pointcloud_to_laserscan, our nodes | YOLO11n TensorRT detector, MJPEG stream (:8081), whisper.cpp STT server (:8082, a separate `stt` stage that opens no device) |

- **Domains:** perception publishes on DDS **domain 42**, unicast on `lo`. The bridge holds domain 0 (robot) and domain 42 in the same process. That crossing is verified on the robot.
- **Never auto-started:** perception only starts with an explicit `perception_up <stage>`. The stages are `fake`, `lidar`, `odometry`, `perception`, `nav2`, `nav2-fake` and `stt` (README L119-131, L150-168).

### 3.2 Vision: YOLO11n with TensorRT

- **Model** [code/hw]
  - Engine `yolo11n.fp16.plan` (7.9 MB) was built from ONNX opset 16, static `[1,3,640,640]`, with NMS on the CPU (README L380-392).
  - TensorRT **8.5.2.2**, CUDA 11.4.
  - Benchmark at MAXN: 203.9 qps, median 4.75 ms GPU compute, about 5% of the GPU at 10 Hz (README L210-223).
- **Classes:** the 80 COCO classes, read from `labels.txt` exported from `model.names` (`detector.py:148-152`).
  - COCO does include `cup`, `wine glass`, `bottle` and `dining table` [inferred from standard COCO; the labels file was not inspected on the robot].
  - It has **no door, doorway or stairs** (README L330-335). Fine-tuning versus open-vocabulary is listed as an open human decision.
- **Rate and parameters** (`detector.py`):
  - `TICK_HZ = 10` (L125), input 640 (L130), colour stream 640x480 @ 30 fps (L131-133).
  - Confidence 0.35 (L135), IoU 0.45 (L136).
  - Detections above 2.0 m height are dropped (L141). Depth window 0.2-10 m.
- **Getting a position from depth** (`grounding.py`, pure functions):
  1. Depth is aligned to colour.
  2. Take the **median depth over the inner 50% × 50% of the box**, subsampled, with zeros dropped.
  3. Deproject the pixel to the camera optical frame.
  4. Apply a **constant camera→base_link extrinsic** (`DEFAULT_CAMERA_EXTRINSIC`, L182-188: x 0.10, z 0.55, pitch 0). It is marked **UNMEASURED** (L176-181).
  5. Report `range_m` (horizontal), `bearing_deg` (0° ahead, +left/CCW), and also x, y, z, elevation and slant (`Grounded`, L191-206).
  - Only label, range, bearing and confidence are published on `rt/c3po/objects` at about 10 Hz, as JSON (`detector.py` header L1-20).
  - No object IDs, no tracking, no 3D boxes, no masks, no world-frame coordinates.
- **Hardware status:**
  - README "Not yet exercised" (L348-392) says nothing has run against a camera. **That section is stale.**
  - Commit `819fc14` (2026-08-21) records the detector "has been detecting since" on the robot. `BRANCH-TEST-PLAN.md:31-33` records the camera through `apps/back` passing with source `vision` on 2026-10-15.
  - Neither source records **detection accuracy, grounding accuracy or the extrinsic** as measured.
- **Camera ownership** (ROBOT-HARDWARE §6.6, L817-860): the head camera is the D435i at `/dev/video4`.
  - Either the vendor `videohub_pc4` (JPEG, **no depth**) or our vision container can own it, not both.
  - The bridge relay serves whichever is live. The detector needs the device.

### 3.3 Nav container and the world model

- **FAST-LIO2** (upstream hku-mars) runs a patch so it publishes body-frame twist.
  - It now consumes the **vendor LiDAR republish** (`/utlidar/cloud_livox_mid360`, PointCloud2 at 9.71 Hz with per-point `time` in **nanoseconds**) across a domain bridge, via `fastlio_utlidar_g1.yaml` (commits `e735b45`, `b8ba390`). It no longer claims the Livox directly.
  - An inverted-mount static TF corrects the mount.
  - `g1_odom_tf` publishes `/odom`, and `pointcloud_to_laserscan` publishes `/scan` (README L16-37).
- **Nav2:**
  - DWB controller, because MPPI throws SIGILL on aarch64 Humble.
  - `velocity_smoother` set to OPEN_LOOP.
  - Biped-specific behaviour trees: `nav2/ws/src/c3po_perception/behavior_trees/navigate_to_pose_biped.xml`.
  - Lifecycle autostart is off, and it publishes `rt/c3po/cmd_vel` at about 20 Hz.
- **Other publishers:** `world_model_publisher` sends `/c3po/world_summary` at 4 Hz. The costmap goes out as a PNG under 1 kB at 1 Hz, and a 120-bearing scan ring feeds the headset radar.
- **The D7 contract** (`DECISIONS.md:563-588`; `apps/bridge/src/bridge/world_model.py:1-140`), exposed as `describe_surroundings` and `/telemetry/surroundings`:

  ```
  {version:1, sources:{pose,detector,lidar: ok|stale|offline}, pose:{x_m,y_m,yaw_deg},
   objects:[{label,range_m,bearing_deg,confidence,age_s}] (max 8, nearest kept),
   objects_omitted, free_space:{ahead_m,left_m,right_m,behind_m}, landmarks:[…egocentric], notes:[…]}
  ```

  - The rules: egocentric; absent is not empty; truncation is declared; everything carries an age. Data goes `stale` after 2 s (`STALE_AFTER_S`, L49) and a report counts as offline after 2 s (`perception_link.py:89`). Budget is about 300 tokens.
  - `free_space` is deliberately just four sectors ("Nav2 owns real obstacle avoidance").
  - `surroundings_snapshot` (`mcp_server.py:1897-1996`) merges the container's report with landmarks computed against the bridge's odometry pose.
  - Its docstring (about L2024) still says "the perception stack … is not deployed", which is stale.

### 3.4 Has Nav2 driven the robot? **No.**

- **No agent tool sends a Nav2 goal.** A search for `navigate_to_pose`, `NavigateToPose` and `send_goal` across `apps/bridge/src`, `apps/back/src`, `apps/perception` and `scripts` finds only the BT XML and `nav2_params.yaml`.
  - D4 ("the agent issues goals, not velocities", `DECISIONS.md:179-198`) is therefore not implemented.
  - `arm_navigation` only opens the gate. A goal must be sent by hand, with `ros2 action` inside the container.
- **The staged bring-up has not reached driving.**
  - Stage 3 (synthetic crossing) and Stage 4 (Nav2 on fake data with the gate closed) have documented procedures (OPERATIONS L590-661). I found no result recorded for either.
  - Stage 8, "armed navigation, supervised", has the first goal "1 m straight ahead" and is **not reached** (README L302-312).
- **D3** still says "Nav2 has never driven this robot" (L154).
- **No persistent map and no map localization.** FAST-LIO odometry restarts with the container [inferred: no map save/load in the launch files I saw referenced].

### 3.5 `walk_to` vs `walk_velocity`

- **`walk_to`** (`skills/walk_to.py`)
  - A proportional controller on world-frame XY, using **vendor leg odometry** from `rt/odommodestate`, which drifts.
  - Gains and caps are fitted to the simulator.
  - No obstacle avoidance, no perception input, `stop_distance_m ≥ 0.3`.
  - Marked `works_real=True`. The only recorded hardware run is **0.17 m on the gantry** (ROBOT-API L984, L2021-2026).
  - Translation under-travels about 2.35x (ROBOT-API L32). Re-fitting the gains has not happened.
- **`walk_velocity`**
  - Open-loop body velocity, capped at ±0.3 m/s or rad/s and ≤3 s per call.
  - Flagged `works_real=False`, and its docstring (L457-470) says 7105 was never dispatched.
  - **Both claims are stale.** The console walk pad and the headset walk ride the same `SET_VELOCITY` path, and the console pad walked at ±0.2 m/s on 2026-10-15 (`BRANCH-TEST-PLAN.md:31-33`, using the `walk_velocity` rows in the audit log).
  - The same docstring's claim that `walk_to`/`turn` are "blocked on real hardware — no world-frame pose source" is also stale. Pose now comes from odometry.
- **Turning in place is real:** `turn` was measured on 2026-08-26.

---

## 4. Arms and hands

- **Preset arm actions** (arm service 7106).
  - The robot's own table is read live through `GetActionList` (`sdk/g1_protocol.py:378-421`): 23 actions plus `RELEASE_ARM=99`. Examples: `RIGHT_HAND_UP=23`, `BOTH_HANDS_UP=15`, `SHAKE_HAND=27`, `HUG=19`, `HIGH_FIVE=18`, `FORWARD_PUSH=36`, …
  - Gating comes from `ACTION_REQUIRES_FSM` and `ACTION_REQUIRES_MODE_MACHINE`. Taught actions (`TAUGHT_ACTIONS`, L438-443) are not wired.
  - On the audited branch only 7 are skills (`SKILL_REQUESTS`, L487-529): wave, point_at, shake_hand, hug, clap, release_arm, and dance (a sequence).
  - [integrate] `gesture` exposes all of them by id. Those presets are the "raise the right hand" level the team wants to move past.
- **Teleop** (D9, `DECISIONS.md:605-664`; `apps/bridge/src/bridge/teleop/`, about 2,500 LOC).
  - Quest 3 at `/vr-control` sends head yaw, both wrists and finger closure at 30 Hz to `bridge.teleop.server` on :8767.
  - Arm mapping is **direction and extension retargeting, not IK** (`retarget.py`). There is no G1 URDF or link-length data in the repo, so "the robot's hand does not end up where the operator's hand is, in metres".
  - `arm_sdk.py` blends into the motion controller through `rt/arm_sdk`:
    - weight ramp;
    - engagement that starts from the measured pose;
    - refusal while a gesture is running;
    - an unresolved risk of losing balance when driven _while walking_ (SDK issue #146).
  - **Both hardware paths are off by default** (`TELEOP_ARM_ENABLED`, `TELEOP_HAND_ENABLED`):
    - Arm joint signs are unverified; `arm_sign_check.py` is the check.
    - [integrate] `arm_pose.py:20-21` says the signs were "measured for the right shoulder/elbow on 2026-08-20, inferred elsewhere".
  - OPERATIONS L561: "**The teleop stream has never run against the robot.**" Headset sessions have walked and turned the robot, but through velocity, not the arm/hand stream [inferred from Latest.md L77-85 and OPERATIONS].
- **Hands:** two **BrainCo Revo2** (ROBOT-HARDWARE §7, settled by inspection on 2026-08-19).
  - They take a [0,1] closure scalar on `rt/brainco/{side}/cmd`, scaled ×1000.
  - **No firmware deadman.**
  - Open/closed polarity is unconfigured (`TELEOP_BRAINCO_OPEN_AT`).
  - ROBOT-HARDWARE L1207: "**No hand command has ever been sent.**"
- **[integrate]**
  - `move_arm` (high, untested) sets semantic joint angles within tight `retarget.LIMITS`, re-sending at 20 Hz.
  - `set_hand` and `open_hands` are untested.
  - Both inherit the teleop driver's gates.
  - There is still no Cartesian/IK control, no grasp planning, no wrist or hand camera, and no force or tactile feedback.

---

## 5. Landmarks, memory, the task runtime, the database

- **Landmarks** (`apps/bridge/src/bridge/skills/landmarks.py`)
  - Named `(x, y, yaw)` in the **odometry frame**, stored as a **JSON file on the bridge host** at `~/.c3po/landmarks.json` (L54-59), written atomically.
  - Each carries a control-board `tick` frame guard to detect a reboot (L14-31). That the tick resets on reboot is unverified.
  - `remember_landmark` is `works_real=False`.
  - `describe_surroundings` lists landmarks egocentrically.
  - The file says durable coordinates need SLAM-map anchoring (L33-36).
  - `Latest.md:20` claims landmarks were persisted to the DB. **The code says otherwise** (JSON file; `schema.ts` has no landmarks table).
- **Task runtime** (`skills/task_runtime.py`)
  - An in-memory, per-process `TaskRegistry`. A `Task` has id, status, progress, phase, result, error and cancel_event.
  - It is visible through `list_active_tasks` and `cancel_task`. Over HTTP, cancel can interrupt from another connection.
  - Fire-and-forget is not built.
- **Database** (Postgres on Neon, Drizzle, `apps/back/src/db/schema.ts`)
  - Better Auth and organisation tables, plus `chat` (L202), `chatMessage` (L227) and `toolCallLog` (L252).
  - [integrate] adds `chat.channel`, `tool_call_log.user_id` and `tool_call_log.source`.
  - **Designed but not built:** `sessions`, `landmarks`, `episodes`, `mcp_tokens`, and pgvector `vector(1024)` columns sized for Voyage (ARCHITECTURE L180-188).
  - There is no embedding or RAG code, no semantic or object memory, and no episodic memory.
  - `tic-embed` was advertised but not listed by `GET /models` (2026-08-18).

---

## 6. Hardware and software constraints on new models

- **Compute:** Jetson **Orin NX 16 GB, unified CPU+GPU memory**, 8 cores, run at MAXN/25 W.
  - Baseline with gemm up and nothing of ours: RAM 2534/15388 MB, GPU `GR3D_FREQ 0%` (perception README L298-300).
  - FAST-LIO's ikd-Tree memory grows with the mapped area.
  - The control board (PC1) cannot be programmed. Vendor services all run there and are reachable only over DDS (ROBOT-HARDWARE §1).
- **Software platform:** JetPack 5 / **L4T r35.3.1**, Ubuntu 20.04 host. The native ROS Foxy is EOL and segfaults.
  - Any **GPU container must be Ubuntu 20.04 with Python 3.8**: the TensorRT bindings depend on `python3 (<< 3.9)`, and the nvidia runtime mounts only `libcuda` (README L45-68).
  - **TensorRT 8.5.2.2, CUDA 11.4.** ONNX must be opset ≤ 17.
  - numpy is pinned to 1.24.4; `np.bool` has to be patched for TRT (commit `819fc14`).
  - The PyPI `ctranslate2` aarch64 wheel has **no CUDA**, which is why Whisper is whisper.cpp and not faster-whisper.
  - A **JetPack 6 upgrade** would remove the container split, but it means an OTA on a vendor image, invalidates every TRT engine, and breaks the pinned CycloneDDS/SDK stack. The team defers it (README L336-340).
  - Consequence: most modern VLMs and open-vocabulary detectors need new TRT export work, or must run off-board [inferred].
- **GPU contention:**
  - D6.3's original argument that faster-whisper never touches the GPU is **superseded**. Whisper now runs on the GPU through whisper.cpp in the vision image, as a separate `stt` container (DECISIONS L496-512; commit `f4e9183`).
  - YOLO uses about 5% of the GPU. whisper.cpp is invoked per utterance as a CLI (`transcribe.py:105`), which reloads the model on every call [inferred from the CLI invocation].
  - **Detector and STT running concurrently has never been measured** [not found in docs].
  - `measure.sh` holds pass/fail thresholds for the whole stack. A full-stack measurement (Stage 7) has not been recorded.
- **Network:**
  - The robot reaches the school LAN over Wi-Fi `EDU-Special`, which is MAC-whitelisted. The Jetson is `g1-orin.local`.
  - DDS to the control board is wired-internal only (192.168.123.0/24), so the bridge must run onboard.
  - `apps/back` (agent + Postgres client) runs off-robot, on a Mac or a LAN box. The planned LAN host `perrobot` is unreachable (OPERATIONS §9).
  - **TIC AI resolves only on campus.** WARP or VPN breaks it.
  - The robot's egress to model hosts measured about 7 KB/s (README L440), so models are side-loaded from a laptop.
  - On 2026-10-15 the working setup was a Mac on the robot's own Wi-Fi (`g1-robot`, 192.168.77.1) with internet over Ethernet (`BRANCH-TEST-PLAN.md:60`).
  - The robot holds **no model or DB credentials** (OPERATIONS §6), a deliberate property.
- **The `gemm` co-tenant** (ROBOT-HARDWARE §10.1):
  - `gemm-bringup` is a container with its own Nav2, a `realsense2_camera` node and foxglove, restart `unless-stopped`.
  - `gemm-ai.service` holds :8000, continuously Whisper-transcribes the mic, and writes to the voice service.
  - `cmd_vel_to_loco` is one launch argument away from driving the legs.
  - D1 bans using any gemm code.
  - Since 2026-10-27 the robot is C3PO-only and gemm's autostart is disabled, but **their files and containers remain and must not be modified**.
  - A stale Livox config left by their driver points the sensor at the Jetson (`~/gemm_ai/lidar/mid360s_g1.json`), which silences the vendor LiDAR republish. Changing it needs asking first.
  - `jetson-clocks-gemm.timer` is kept on purpose.
- **Locomotion envelope:**
  - Velocity is high-level `SET_VELOCITY` with a 1 s deadman. Walking needs FSM 501 with the feet loaded.
  - Nav2 clamps: vx up to 0.5 m/s forward. The `walk_velocity` cap is 0.3.
  - Real velocity scaling and response are not linear and not fitted (ROBOT-API L19-40).

---

## 7. What is missing for "bring me the glass on the table"

Ordered roughly along the task pipeline. Each gap names its location in the repo.

1. **Speech in, reliably.**
   - The mic is push-to-talk or ambiguous, and there is no USB mic (§1.2).
   - The spoken loop has never run end to end on hardware.
   - The audited branch has no dialogue history and no spoken reply. [integrate] fixes both, but its best-quality engine is public cloud, which contradicts D6.3, and TIC AI is campus-only.
2. **A planner that can carry a multi-step physical task.**
   - `tic-chat` multi-step tool use is unproven (§2.1), and there is a 12-step cap.
   - Nothing represents a plan, a task or a subgoal.
   - Blocking tool calls with no fire-and-forget mean no parallel "walk while watching".
   - The world model is pull-only and absent from the system prompt.
   - Nothing verifies success ("is the glass in my hand?").
   - **[integrate] the voice-mode high-danger filter blocks `arm_navigation` and `move_arm`.** A spoken fetch is impossible by policy until a confirmation flow exists.
3. **Finding "the glass on the table".**
   - YOLO11n/COCO can label `cup` or `wine glass` and `dining table` [inferred], but only as nearest-8 egocentric range/bearing.
   - Not available: instance IDs, tracking, 3D extent or pose, spatial relations ("on the table"), open-vocabulary queries, a VLM, object permanence, or a semantic map.
   - The camera extrinsic is unmeasured. Detector accuracy on this robot is unmeasured.
   - The head camera has a 69° FOV. There is no active search skill: "look around" exists only as `turn`, with no head or waist control.
   - The vendor camera and the detector are mutually exclusive.
4. **Navigating to it.**
   - No `navigate_to` or `go_to_object` tool. Nav2 goals cannot be sent from the agent (§3.4).
   - Nav2 has never moved the robot. Stages 5-8 are not done.
   - No saved map or relocalisation. Landmarks are drifting odometry with `remember_landmark` unverified.
   - `walk_to` has no obstacle avoidance, and its only hardware evidence is 0.17 m on the gantry.
   - Range/bearing must be converted to the world-frame XY that `walk_to` takes. That is exactly the trigonometry D7 meant the LLM to avoid.
   - The nav clamps are unmeasured.
5. **Grasping it.**
   - The hardest gap. There is no manipulation stack:
     - no URDF or kinematic model in the repo;
     - no IK or Cartesian control;
     - D9 retargeting is approximate by design.
   - `move_arm` and the hands are never live-tested. Joint signs are only partly measured. BrainCo polarity is unknown and the hands have no deadman.
   - No grasp-pose estimation, no hand-eye calibration, no wrist camera, no tactile or force feedback.
   - `arm_sdk` while walking has a known balance risk.
6. **Bringing it to "me".**
   - Person detection exists (COCO `person`), but there is no speaker localisation.
     - `rt/audio_msg` carries a DOA `angle` that nothing uses (ROBOT-HARDWARE §8.2 / ROBOT-API §7).
   - No person tracking or identity, and no handover behaviour. Carrying while walking is untested.
7. **Memory and learning.**
   - No episodes, semantic memory or embeddings (§5).
   - The `tool_call_log` audit trail is the only durable record of what the robot did.
8. **Safety for autonomy.**
   - The link watchdog is disarmed.
   - The one-commander invariant is social, not technical.
   - The spoken stop is "a convenience, never the safety story" (D6.3).
   - Every motion currently assumes a human with the e-stop in hand. There is no autonomy-grade supervisor (geofence, speed by proximity to people, a manipulation workspace limit).
9. **Platform headroom for new models.**
   - JetPack 5 means TRT 8.5, Python 3.8 GPU containers and 16 GB unified memory.
   - Any VLM or open-vocabulary model needs either TRT 8.5 export work or an off-board GPU. Off-board means campus networking, and D5/D6.3 cloud constraints apply.
   - The full stack under load (FAST-LIO + Nav2 + YOLO + Whisper) has never been measured (Stage 7).

### Doc and code drift found during the audit (worth fixing before the plan cites it)

- `apps/perception/README.md` "Not yet exercised" (L348-392) says nothing has run against a camera. Commit `819fc14` and the 2026-10-15 test plan show the detector and stream running on the robot.
- `walk_velocity` docstring and flag (`mcp_server.py:418,457-470`): the docstring says 7105 was never dispatched and that walk_to is blocked. Both are stale.
- `describe_surroundings` docstring (about `mcp_server.py:2024`): "perception … not deployed" is stale.
- `docs/ROBOT-API.md:25-28`: says `turn` is `works_real=False`. The code says True, measured 2026-08-26.
- `docs/ARCHITECTURE.md` §3: says voice is "designed, not built". Contradicted by §4.1 and the code.
- `Latest.md:20`: says landmarks are in the DB. They are in a JSON file.
- D6.3 "no cloud" versus [integrate]'s OpenAI Realtime engine.
- TIC AI hostname: `ia.ort.edu.ar` in the code versus `ia-tic.ort.edu.ar` in `BRANCH-TEST-PLAN.md`.
