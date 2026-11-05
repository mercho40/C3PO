# C3PO backlog: everything open after 2026-11-03

Built from the morning audit (`audit/README.md`), the robot session of 2026-11-03, an
independent review of that day's code, and a research pass. Each item has a size (**S** <½
day, **M** 1–3 days, **L** a week or more) and says whether it needs the robot (🤖), a
headset (🥽), or neither.

Legend: ✅ done 2026-11-03 · 🔴 safety · 🟠 correctness · 🟡 capability · 🔵 research

## 0. Done on 2026-11-03 (for reference)

✅ PARAR stops the agent (bridge latch + `/safety/ack` + console «Reanudar», turns aborted)
· ✅ release freezes the arm · ✅ waist hold under arm_sdk · ✅ straight-line pick/place
inside URDF limits · ✅ stale perception aborts arm motion · ✅ back survives a failed `say`
· ✅ camera relay frees viewer slots · ✅ `zero_torque` never offered to the model, `damp` not
on voice · ✅ console **Confirmar** for T2 · ✅ `go_to_object` (camera-only) · ✅ YOLOE-26 live
(40 ms/tick) · ✅ Gemini via the Mac (`robot_internet.py`) · ✅ VR «Hablar con el robot» +
in-headset PARAR (not yet on a Quest) · ✅ the LiDAR is a Mid-360S; config fixed · ✅ gemm
autostart disabled · ✅ **review fixes:** an idle headset no longer clears PARAR (C1),
`look_around` is T1 and every velocity is zeroed while stopped (C2), `damp` is never
offered to a model, headset speech runs in voice mode, the VR menu keeps its selection by
name, a 300 ms grip chord, a stop voids pending grants, and the ack refuses a newer stop.

Verified on the robot: the PARAR latch and Reanudar, `go_to_object("silla")`, the URDF
sign check, a confirmed `reach` within 1 cm, the console Confirmar button, the YOLOE
engine, and Gemini `look`.

## 1. Safety 🔴

| #    | Item                                                                                                                                                                                                                                                                                                                                                                            | Size | Needs |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ----- |
| 1.1  | **Legacy voice: "para" still waits behind the agent turn.** `voice/loop.ts` handles events inside the same `for await` that awaits `runAgent`. Pump events separately and act on `stop` at once (audit §1.3).                                                                                                                                                                   | S    | —     |
| 1.2  | Arm the bridge's local stop phrase on the legacy engine too (it's armed only on realtime, `mcp_server.py`).                                                                                                                                                                                                                                                                     | S    | —     |
| 1.3  | `arm_navigation` and `start_walking_waist` are high-danger and still T1 in typed chat: the model can open the Nav2 gate unconfirmed. Route them through Confirmar when the caller is the agent.                                                                                                                                                                                 | M    | —     |
| 1.4  | **Bind back to loopback** (it listens on `*:3000`). Add `disableSignUp`, and an admin role check on `/agent` and `/voice` (audit #10).                                                                                                                                                                                                                                          | S    | —     |
| 1.5  | Teleop on `0.0.0.0:8767` with no auth: add an Origin allowlist and a token from back. Don't bind it to loopback, the headset uses the LAN (audit #9).                                                                                                                                                                                                                           | M    | —     |
| 1.6  | **The `.98` incident:** another machine with another key ran `rm -rf ~/gemm` and disabled the bridge. Audit `~/.ssh/authorized_keys`, rotate the `unitree` password, and agree who may operate.                                                                                                                                                                                 | S    | 🤖    |
| 1.7  | `bashrunner` accepts unauthenticated shell over DDS from the wired LAN (ROBOT-API §8). Switch it off with ServiceSwitch, the same way as `lidar_driver`.                                                                                                                                                                                                                        | S    | 🤖    |
| 1.8  | **Firmware ≥ 1.5.5.0.** CVE-2026-76639 (CVSS 8.8) is root RCE from the adjacent network via TCP 9991 on the control board; CVE-2026-76640 is root RCE over BLE. Both cover firmware ≤ 1.5.2. The researcher reports 1.5.5.0 blocks the chain but hasn't verified it. Read the version in the app, update, keep 192.168.123.0/24 off campus Wi-Fi, and pin `DDS_INTERFACE=eth0`. | S    | 🤖    |
| 1.9  | PARAR pressed before the console hydrates is lost: add a `<form action>` fallback (audit §5).                                                                                                                                                                                                                                                                                   | S    | —     |
| 1.10 | Battery: it went from 83% to 3% in one session. Show battery in the topbar, warn at 25%, and refuse long tasks or arms below 20%.                                                                                                                                                                                                                                               | S    | —     |
| 1.11 | A narrow passwordless sudo rule for `systemctl {start,stop,restart} c3po-bridge.service` only, in `c3po.sudoers`, so a restart isn't blocked on a password.                                                                                                                                                                                                                     | S    | 🤖    |
| 1.12 | Dependencies: `mcp` ≥ 1.30 < 2, `undici` ≥ 7.30.1, `bun audit fix`, and `uv sync --locked` in `bridge_sync`.                                                                                                                                                                                                                                                                    | S    | —     |

## 2. Robot tests still to run 🤖

- [ ] **Camera pitch:** feet → desk edge with the Measure app, then set 47.6° or the measured value and re-read. Every range is about 2× long today.
- [ ] `go_to_object` again after the pitch fix, with the default 0.7 m stop.
- [ ] PARAR **mid-walk**. Today the turn aborted before the walk started.
- [ ] Spoken "para" during a walk (after 1.1).
- [ ] VR «Hablar con el robot» on the Quest 🥽: the mic inside immersive-vr, B/Y and grip indices, panel readability, transcription quality.
- [ ] Walking with the headset's arms engaged while the waist is held. Never tried.
- [ ] `pick` / `place` end to end (needs the hand, §5).
- [ ] Person rule against a tape-measured person (after the pitch fix).
- [ ] `safety.md` step 3 (kill the bridge mid-walk) and step 4 (`soft_stop`).
- [ ] Re-run the nav image build (cut off by the battery).

## 3. Perception 🟠🟡

| #   | Item                                                                                                                                                    | Size |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 3.1 | Camera extrinsic: measure pitch, height and forward offset. Make it the default, or read it from the URDF `d435_joint` (audit §3.1).                    | S 🤖 |
| 3.2 | The plane fallback is inverted while the pitch is wrong (`grounding.py`): tables are rejected and walls accepted. Fixed by 3.1, then add a test.        | S    |
| 3.3 | Make YOLOE and the pitch persistent: `c3po up` should read `~/.c3po/perception.env`. Today `yoloe.env` must be sourced by hand after every boot.        | S    |
| 3.4 | Camera USB wedge (`VIDIOC_S_FMT errno=5`) after a container restart: do the `hardware_reset()` automatically on that error at detector start.           | S    |
| 3.5 | YOLOE false positives ("cooker", "toaster", "thumbtack"): tune the vocabulary and per-class thresholds; compare the prompt-free vs text-prompt engines. | M    |
| 3.6 | `world_model_publisher` stamps `stamp_unix=now` instead of the capture time.                                                                            | S    |
| 3.7 | Capture a YOLOE test set (`perception.md` §5) and record recall per class.                                                                              | M 🤖 |
| 3.8 | Range errors at the image edges (45–100% long, bearing off by up to 18°): undistort or use depth-to-point, not centre-ray range.                        | M    |

## 4. Navigation 🟡

| #   | Item                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Size   |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| 4.1 | **The Mid-360S scan module is faulted** (HMS `0x02100003`, SN `ARMCNAF0030677`). In order: (1) Livox Viewer 2 **v2.5.9** from a laptop on 192.168.123.x: read the firmware and HMS, flash **35.01.0113** (2026-10-30); (2) if it persists, bench-test the sensor on 9–27 V, off the robot; (3) RMA through Unitree or the seller (part G1-MID360L) with the SN, firmware and HMS log.                                                                    | — 🤖   |
| 4.2 | **Obstacle avoidance without the lidar** (the least work, per the research): G1 odometry (`rt/odommodestate`) as `odom→base_link`; Nav2 with a rolling `odom`-frame costmap fed by the **D435i PointCloud2 into the built-in VoxelLayer** (height band 0.05–1.3 m, raytrace clearing); **nav2_collision_monitor** with a stop polygon; 0.3 m/s cap. Skip `depthimage_to_laserscan` on a downward-tilted head. Check whether G1 odometry needs the lidar. | M      |
| 4.3 | `go_to_object` closed loop: re-detect and re-aim every second while walking, abort if the target is lost; use Nav2 when it's up.                                                                                                                                                                                                                                                                                                                         | M      |
| 4.4 | When the lidar is back: verify FAST-LIO with the Mid-360S (CustomMsg, IMU, upside-down TF), slam_toolbox mapping, Nav2 gantry → room (`nav.md`).                                                                                                                                                                                                                                                                                                         | M 🤖   |
| 4.5 | Persist the Mid-360S config: rebuild the nav image (`a0243e5`, plus slam_toolbox) through the proxy.                                                                                                                                                                                                                                                                                                                                                     | S 🤖   |
| 4.6 | Evaluate Unitree `slam_operate` (APIs 1801–1901) for built-in relocalisation (audit §7 #12).                                                                                                                                                                                                                                                                                                                                                             | M 🤖🔵 |
| 4.7 | Nav container → ROS 2 Jazzy (no GPU needed, independent of JP5): collision-monitor and MPPI fixes (audit §7 #11).                                                                                                                                                                                                                                                                                                                                        | L      |

## 5. Arms and hands 🟡

| #   | Item                                                                                                                                               | Size |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 5.1 | **Right BrainCo hand doesn't answer on any ttyUSB.** Check power and the wrist connector; try the official `unitreerobotics/brainco_hand_service`. | S 🤖 |
| 5.2 | Measure `TELEOP_BRAINCO_OPEN_AT` (read the state with the hand visibly open), then `set_hand`, then `pick`.                                        | S 🤖 |
| 5.3 | `pin` + `pink` + `coal`: 6-D IK with roll control and self/table collision checks (audit §4).                                                      | M    |
| 5.4 | MuJoCo Menagerie G1 twin as the arm test harness. It would have caught the release and sweep bugs before the robot did.                            | M    |
| 5.5 | Wrist joint signs are unverified. Confirm them with a firmware gesture plus `arm_state`, like today's shoulder check.                              | S 🤖 |
| 5.6 | Gestures from text (Kimodo-G1, offline, replayed arm-only through the arm_sdk caps).                                                               | L 🔵 |

## 6. Voice and VR 🟡

| #   | Item                                                                                                                                                                                                                  | Size |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 6.1 | The robot speaks the VR reply: Piper (**OHF-Voice/piper1-gpl** v1.8.0, GPL-3.0) with the **es_AR "daniela"** voice on the Orin CPU → `AudioClient.PlayStream` (16 kHz mono PCM).                                      | S    |
| 6.2 | VR audio: move from `ScriptProcessor` to an AudioWorklet. Quest Browser is Chromium 146+. Open the mic and resume the AudioContext from a tap **before** `requestSession`; keep one stream; handle `visible-blurred`. | S 🥽 |
| 6.3 | Better STT: first, client-side VAD (Silero in the worklet) so an utterance uploads the moment speech ends; later, true streaming (OpenAI Realtime transcription, proxied through back, or WhisperLive).               | M 🔵 |
| 6.4 | XR camera texture uploaded per eye per frame (~200 × 1080p/s): upload once per frame. Causes Quest judder (audit §5).                                                                                                 | S 🥽 |
| 6.5 | Camera over WebRTC (go2rtc/aiortc) instead of MJPEG.                                                                                                                                                                  | M    |
| 6.6 | Stand audio kit: a handheld or lav mic plus echo cancellation, for the expo (audit §7 #1).                                                                                                                            | M    |
| 6.7 | Benchmark STT on Rioplatense Spanish (OpenSLR 61): `tic-transcript` vs sherpa-onnx Parakeet.                                                                                                                          | M 🔵 |
| 6.8 | Show the headset conversation in the console Chat (persist it as a chat).                                                                                                                                             | S    |

## 7. Agent 🟡

| #   | Item                                                                                                                                                                                                                                                                                       | Size |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---- |
| 7.1 | AI SDK 7 native **`toolApproval`** (`'user-approval'` for walk, arm and hand tools; `needsApproval` is WorkflowAgent-only in v7), with **`experimental_toolApprovalSecret`** set, instead of refuse → Confirmar → retry: one turn, no 30 s window. Keep the bridge gate as the real limit. | M    |
| 7.2 | Telemetry: `experimental_telemetry` → Langfuse/OTel, so every turn's tools, latency and cost are visible.                                                                                                                                                                                  | S    |
| 7.3 | `pruneMessages`, to bound context in long voice sessions.                                                                                                                                                                                                                                  | S    |
| 7.4 | Eval harness: `tasks.yaml` `motion_tools` lacks `navigate_to_landmark` and `go_to_object`; run the T0 subset nightly against the sim.                                                                                                                                                      | S    |
| 7.5 | Prompt: teach the agent `go_to_object` vs `navigate_to`, and say "no obstacle avoidance" honestly when the lidar is offline.                                                                                                                                                               | S    |
| 7.6 | Memory: "where did you last see my keys?" (ReMEmbR pattern in Postgres + pgvector) (audit §7 #13).                                                                                                                                                                                         | M 🔵 |
| 7.7 | Gemini fallback when offline: degrade `look`/`find_object` to YOLOE-only with an honest note.                                                                                                                                                                                              | S    |

## 8. Ops and infrastructure 🟠

| #   | Item                                                                                                                                                                                                 | Size |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 8.1 | **Robot internet without the Mac:** the robot joins the school Wi-Fi as a client, with fallback to its hotspot if that fails within 60 s. Gives NTP, Gemini and apt; no cable pulled out by walking. | M 🤖 |
| 8.2 | A single `c3po session` on the Mac: tunnel + proxy + console + perception env, so a session doesn't take 15 commands.                                                                                | S    |
| 8.3 | Push `feat/autonomy` to GitHub and open a PR into `integrate/october` (mercho40 decides the merge).                                                                                                  | S    |
| 8.4 | Decide what to do with the uncommitted WIP in the main checkout (`feat/real-g1-dispatch-and-hardening`); it's ported to 516c5ec.                                                                     | S    |
| 8.5 | Delete or rotate the local `operador@c3po.local` account (its password was shown in a chat).                                                                                                         | S    |
| 8.6 | Wi-Fi / long-cable plan for walking tests; the robot pulled the cable out once.                                                                                                                      | S    |

## 9. Tests and CI 🟠

| #   | Item                                                                                      | Size |
| --- | ----------------------------------------------------------------------------------------- | ---- |
| 9.1 | The spoken-stop tests never run (all 13 skips are `test_listen.py`); make them run in CI. | S    |
| 9.2 | `test_mic_listener` timing flake under full-suite load.                                   | S    |
| 9.3 | CI for web, back, bridge and perception on every push, plus svelte-check.                 | S    |
| 9.4 | `graphify-out` is ignored only through `.git/info/exclude`; add it to `.gitignore`.       | S    |

## 10. Docs

- `ROBOT-API.md` §8: the ServiceSwitch key is `switch` (not `swit`), API version 1.0.0.2.
- `OPERATIONS.md`: cable + static IP, push to the robot, the proxy, the 501 vs 802 trap.
- The test-day docs: today's results, and the checklist order that worked.

## 11. From the 2026-11-03 code review (left after the fixes)

| #    | Item                                                                                                                                                                              | Size |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 11.1 | Bind a T2 grant to a hash of the tool arguments and the chat id. Today a grant is per tool name, so another caller within 30 s can use it with other arguments.                   | M    |
| 11.2 | The latch fails open if the sentinel write fails (`signal_stop` swallows OSError): keep an in-process flag in the bridge too, ORed with the file.                                 | S    |
| 11.3 | The headset PARAR depends on apps/back. Also send the stop on the teleop channel straight to the bridge.                                                                          | S 🥽 |
| 11.4 | Verify on a gantry whether an arm `request_release` after a `person_in_reach` abort still sweeps the arm (the 2 s blend back to firmware). If it does, hold instead of releasing. | S 🤖 |
| 11.5 | `go_to_object`: reject live detections older than ~0.5 s, or seen while the robot was turning.                                                                                    | S    |
| 11.6 | VR voice + locomotion: the teleop lease is renewed while a headset is connected, so "andá hasta…" from the headset is probably refused as "teleop is driving". Decide the rule.   | S 🥽 |
| 11.7 | `robot_internet.py` is an open relay: restrict it to ports 80/443 and refuse loopback and private destinations.                                                                   | S    |
| 11.8 | `done` for an agent turn is wired to `onEnd`/`onAbort` only; check that a stream error doesn't leak the controller.                                                               | S    |
| 11.9 | Tests: the `go_to_object` tool body (pose, frame, too_far), and that `runAgentChat` really calls `done`.                                                                          | S    |

## 12. Research findings to act on (2026-11-03)

- **Gemini Robotics-ER 2 data terms:** on the free tier Google may use prompts and images, and human reviewers may read them; users must be 18+; the robotics docs ask that people in view be notified, and suggest face blurring. Use a paid key or blur faces before upload. An unrestricted API key gets 403. The free-tier limits per model aren't published (the old figure for ER 1.5 was 10/min, 250/day).
- **ER 2 pointing:** ask for JSON `[{"point":[y,x],"label"}]` (0–1000, at most ~10 items), crop or zoom small objects, use `thinking_level: "medium"`, and lift each point to 3-D with the aligned D435i depth.
- **G1:**
  - The 802 trap is upstream bug unitree_sdk2_python #170 (firmware 1.5.3, still open); always confirm with `GetFsmId`.
  - Arm weight 1.0 in running mode 801 makes the G1 ignore velocity.
  - Outstretched arms make it shuffle forward: the policy doesn't know about the arms.
  - The official waist hold is `g1_arm7_sdk_dds_example` at kp 60 / kd 1.5, which is what we ship.
- **Livox:**
  - Mid-360S support arrived in livox_ros_driver2 1.2.5 and SDK2 1.3.0; current versions are 1.2.8 and 1.5.2. Pin a driver commit, SDK ≥ 1.3.3.
  - Open bugs to watch: SDK2 #125 (control commands time out on Mid-360S) and driver #240 (no topics after an upgrade; fixed by lidar firmware).
  - FAST-LIO `ROS2` branch: `lidar_type 1`, `scan_line 4`, `timestamp_unit 3`, CustomMsg; raise `blind` to filter out the robot's own body.
- **VLA models (GR00T, pi0, OpenVLA) are not realistic on Orin NX with JetPack 5.** GR00T-WBC needs JP6 and TRT 10.7. Realistic: train ACT or SmolVLA with unitree_lerobot for one arm task, and run it on a laptop GPU off the robot.
- **Isaac ROS / nvblox are not available on JP 5.1.1.** Their last JP5 releases (2.0/2.1) target 5.1.2. Re-flashing the G1's Orin to JP6 (NVIDIA's GR00T-WBC guide) is a large change; consider it only for VLA work.
- **Safety architecture:** remote/damp, then a deterministic bridge monitor (caps, geofence, heartbeat watchdog, `GetFsmId` after `SetFsmId`), then fixed skills, then the LLM as planner. That is our shape; add a geofence and a heartbeat watchdog. References: RoboGuard (arXiv 2503.07885), BTGenBot-2.
