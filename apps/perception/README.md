# apps/perception

The G1's onboard perception stack: LiDAR odometry, obstacle avoidance, object
detection, and the world-model feed the agent reasons over. It runs as two Docker
containers on the robot's Jetson (see `docs/ROBOT-HARDWARE.md` for the machine
itself) and talks to `apps/bridge` over an isolated DDS domain. The decisions it
implements are D2 (ROS 2 as a contained subsystem), D3 (the sensor pipeline),
D4 (Nav2) and D7 (the world-model contract) — rationale in `docs/DECISIONS.md`.

**The boundary, before anything else:** perception never drives the robot. Nav2's
velocity leaves as `/c3po/cmd_vel` (DDS `rt/c3po/cmd_vel`) — never a bare
`/cmd_vel` — on a domain nothing else shares, and the bridge is the sole thing
that can turn it into leg motion, through a gate that is closed by default
(D2.1; `apps/bridge/src/bridge/sdk/perception_link.py`).

## Data flow

```
eth0 (robot internal LAN)        domain 42 (lo, unicast)            domain 0 (eth0)
─────────────────────────        ───────────────────────            ───────────────
Mid-360 ──UDP──> [nav container]
                   livox_ros_driver2 (CustomMsg, xfer_format=1)
                   FAST-LIO2 ──/Odometry, /cloud_registered_body
                   g1_odom_tf ──/odom (odom→base_footprint, real twist)
                   pointcloud_to_laserscan ──/scan
                   Nav2 (DWB) ──/c3po/cmd_vel ────────────────────┐
                   world_model_publisher ──/c3po/world_summary ───┤
                   costmap_publisher ──/c3po/costmap (PNG in JSON) ───┤
D435i ──USB──> [vision container]                                 │
                   pyrealsense2 (V4L2) + YOLO11 TRT engine        │
                   ──/c3po/objects (String JSON, egocentric) ─────┤
                   ──MJPEG on 127.0.0.1:8081 (operator console)   │
                                                                  │
                                  [apps/bridge, host process] ◄───┘
                                    Domain(42, explicit xml) — reads summary + cmd_vel
                                    ChannelFactory(0) ──────────► SET_VELOCITY (7105)
```

## Two containers, not one

The split is forced by packaging, not preference. The Jetson's
nvidia-container-runtime csv mounts `libcuda.so.1.1` and nothing else — no
`libcudart`, no `libcudnn`, no `libnvinfer` — and JetPack 5's TensorRT Python
bindings hard-depend on Python < 3.9, so **any GPU container on this host is
Ubuntu 20.04 until JetPack 6**. The nav half is legal on Ubuntu 22.04 for exactly
one reason: it never touches CUDA — which buys Nav2 as binary debs in 90 seconds
instead of a ~2 h source build that OOMs. The full reasoning, with the exact
csv path and dependency strings, lives as header comments in
[`nav/Dockerfile`](nav/Dockerfile) and [`vision/Dockerfile`](vision/Dockerfile) —
those comments are the authority; read them before "simplifying" back to one image.

|          | `c3po-perception-nav`                                                  | `c3po-perception-vision`                               |
| -------- | ---------------------------------------------------------------------- | ------------------------------------------------------ |
| Base     | `ros:humble-ros-base-jammy` (22.04)                                    | `nvcr.io/nvidia/l4t-jetpack:r35.3.1` (20.04)           |
| ROS      | Humble from apt binaries                                               | **none at all**                                        |
| GPU      | no (`runc`)                                                            | yes (`--runtime nvidia`)                               |
| Owns     | Livox Mid-360                                                          | RealSense D435i                                        |
| Contains | livox_ros_driver2, FAST-LIO2, Nav2, pointcloud_to_laserscan, our nodes | pyrealsense2 (V4L2 wheel), TensorRT, CycloneDDS python |

The vision container has no ROS by design: it publishes already-resolved
egocentric detections as plain CycloneDDS JSON, holding the camera extrinsic as
a constant, so there is nothing for TF to do on that side (D2.2 option 1).

## The operator's picture comes from here, and it has to

`/dev/videoN` is a single-owner kernel resource, so the process holding the
D435i is the only one that can show a human what the robot sees. That is this
container. `vision/c3po_vision/stream.py` therefore serves the colour frame as
MJPEG — `/stream.mjpg`, `/frame.jpg`, `/status` — on **127.0.0.1:8081**, fed by
the same frames the detector grounds, decimated to 5 Hz and JPEG-encoded off the
tick (the encode runs on the HTTP thread; the tick pays only a memcpy).

Three things about it are load-bearing and are argued in the module header:

- **Loopback by default.** An unauthenticated view of a shared lab does not go
  on the school Wi-Fi because it was convenient. The console reaches it through
  the same SSH tunnel as the bridge (`-L 8081:127.0.0.1:8081`), and
  `C3PO_VISION_STREAM_HOST=0.0.0.0` stays a thing somebody types.
- **A frozen frame is not a live one.** A tick that failed offers nothing, so
  the frame ages; `/status` publishes that age and the server closes the stream
  once it crosses 1 s. This is the same rule as "no publish on a failed tick",
  applied to pixels — an `<img>` will otherwise display a dead camera forever.
- **It cannot take the detector down.** A bind failure logs and leaves the video
  off; obstacle reporting is the container's job and video is not.

`C3PO_VISION_STREAM=0` turns it off. `perception_up` sets the defaults and
prints the URL.

## DDS domain isolation

Everything perception publishes lives on **its own DDS domain (42), bound to
`lo`, unicast** — the control board, the gemm stack and the bridge's robot-facing
participant all share domain 0 and never see it. Unicast is not a tuning choice:
`lo` on this Jetson has no MULTICAST flag, so multicast discovery on it starts
cleanly and discovers nothing. The one shared config
([`config/cyclonedds-domain42.xml`](config/cyclonedds-domain42.xml), used by both
containers and mirrored in the bridge) carries the full explanation, including
why nothing large (PointCloud2) may cross this domain.

The bridge side **must** create the domain as `Domain(42, explicit_xml)` — a bare
`DomainParticipant(42)` inherits `connection.py`'s `<Domain id="any">` config
(unicast peer at the control board) and silently discovers nothing. See
`apps/bridge/src/bridge/sdk/perception_link.py` for the mechanics, the cmd_vel
gate, the clamps and the deadman.

The crossing is proven on this hardware: one process held
`ChannelFactoryInitialize(0)` and `Domain(42, xml)` simultaneously — 119 samples
on domain 42 alongside 31,337 `LowState_` samples on domain 0, undisturbed.
Registry egress from the Jetson is also verified (nvcr.io and Docker Hub answer
401 unauthenticated as expected, GitHub 200), so image pulls work from the
robot's network.

## Never auto-started

Perception is **never** started by `run_c3po`, by the boot unit, or by any other
automatic path. The sensors are shared with the other team (see
`docs/ROBOT-HARDWARE.md`), and claiming the Livox and the RealSense is a
different conversation from "the bridge is mine". A machine powering on is not
somebody asking for the sensors — hence `--restart no` on both containers
(gemm's `unless-stopped` is exactly the pattern this avoids), Nav2's lifecycle
`autostart: false`, and the bridge's cmd_vel gate defaulting closed. Starting
perception is one explicit command: `perception_up <stage>`. There is no flag,
no environment variable and no opt-in that makes `run_c3po` do it — an opt-in
var is one systemd `Environment=` line away from being an automatic path, so
"no unless a flag is set" is not the guarantee this section claims.

## Build and run

Both images build **on the robot** — its docker is the Ubuntu archive package
(classic builder only, no buildx/compose), so there is nothing to cross-build
from a laptop:

```bash
# These three are NOT symlinked onto PATH (only the four stack controls are) —
# invoke them by path from the checkout:
ssh c3po '~/c3po/scripts/robot/build_perception all'      # vision image + TRT engine + nav image + bench
ssh c3po '~/c3po/scripts/robot/perception_up <stage>'     # create + start containers, gate on the topic
ssh c3po '~/c3po/scripts/robot/measure.sh <label> [sec]'  # compute-budget harness, thresholds printed first
stop_c3po                            # on PATH onboard; also stops perception and verifies release
```

`build_perception` wraps the export/build/benchmark sequence and carries the
per-step timings, disk guards and assertions in its own comments.
`perception_up`'s stages, and what each claims:

| stage        | runs                                   | sensors claimed               |
| ------------ | -------------------------------------- | ----------------------------- |
| `fake`       | world model on synthetic input         | **none** — gemm keeps running |
| `odometry`   | livox + FAST-LIO + TF + `/scan`        | Livox                         |
| `perception` | + detector + world model               | Livox + RealSense             |
| `nav2`       | + Nav2 (lifecycle **not** autostarted) | Livox + RealSense             |

Stages that claim sensors stop gemm first; `perception_up` says exactly what it
is taking before it takes it, and rolls back (containers removed, sensors freed)
if `/c3po/world_summary` does not appear.

On the Mac, the Stage 0 harness runs with no robot, no DDS, no ROS:

```bash
cd apps/perception
bun run test     # uv run pytest — grounding maths + report-shape contract
bun run lint     # ruff, py38 syntax floor for the vision tree
```

[`pyproject.toml`](pyproject.toml) explains why this is not an installable
package and why the test subset must stay dependency-free.

## What the research refuted

Nine claims an adversarial verification pass refuted before any of this touched
hardware. Each correction is embedded as a comment in the exact file where the
mistake would otherwise be re-made — the file is the full argument; this table
is the index.

| Refuted claim                                                                            | Correction                                                                                                                                                                                                                                                                                                                                     | Full reasoning in                                                                              |
| ---------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| "`roll: 180` in the Livox config (+ FAST-LIO `extrinsic_R`) fixes the upside-down mount" | The driver rotates the **point cloud only**, never the IMU — rotate _nothing_: all-zero extrinsic, identity R, datasheet T, one static `odom→camera_init` TF with roll = π. Getting the flip wrong is a ~10 cm lever-arm error, not ~5                                                                                                         | `nav/ws/src/c3po_perception/config/MID360_config.json` notes + `fastlio_mid360_g1.yaml` header |
| "Use the deepglint G1-humanoid FAST-LIO fork — it handles the extrinsics"                | Its humble branch ships no livox driver (`mid360_g1.yaml` exists only on its ROS1 `main` branch), its README still says `catkin_make`, its `extrinsic_est_en: true` merely absorbs the sign error, its `open3d_loc` needs a precompiled Open3D 0.14.1 from Baidu Netdisk, and it is map-relative localization, not SLAM. Use hku-mars upstream | `nav/Dockerfile`                                                                               |
| "Synthesize the missing odometry twist by differentiating pose"                          | `Odometry.twist` is specified in `child_frame_id`; a world-frame derivative is a silent, heading-dependent sign bug. Patch FAST-LIO to publish its own IESKF state velocity, body-frame                                                                                                                                                        | `nav/patches/fastlio-publish-twist.patch`                                                      |
| "velocity_smoother `CLOSED_LOOP` re-syncs Nav2 after a bridge veto"                      | ~10 Hz odometry against a 20 Hz smoother is upstream's own named oscillation failure. `OPEN_LOOP`; veto re-sync comes from `SimpleProgressChecker` on `/odom`                                                                                                                                                                                  | `config/nav2_params.yaml`                                                                      |
| "MPPI runs at 15 Hz on the Orin NX"                                                      | Its speed relies on AVX2/MFMA, compiled out on aarch64 (NEON fallback), and the Humble arm64 deb SIGILLs on activation (navigation2#5061). DWB, diff-drive-only, for phase 1                                                                                                                                                                   | `config/nav2_params.yaml`                                                                      |
| "Pin domain 42 to loopback multicast; one `CYCLONEDDS_URI` file configures both domains" | `lo` has no MULTICAST flag, and the vendor SDK's inline config overrides `CYCLONEDDS_URI` entirely — unicast `lo` + explicit peer, and an explicitly-passed config on both sides                                                                                                                                                               | `config/cyclonedds-domain42.xml` + `perception_link.py`                                        |
| "RSUSB RealSense works with just `/dev/bus/usb` + a cgroup rule"                         | The PyPI wheel is a **V4L2** build (RSUSB needs a source build, root udev changes, and claims the whole device). Stock V4L2 wheel, pinned to the last cp38/aarch64 release                                                                                                                                                                     | `vision/Dockerfile`                                                                            |
| "The dustynv Humble monolith runs on this host / add the t234 repo for TensorRT"         | Never executed; TRT was already in it; the real cost is Humble-on-focal with no binary debs — the reason the split exists                                                                                                                                                                                                                      | `vision/Dockerfile` + `nav/Dockerfile` headers                                                 |
| "The full stack fits in ~12.6 GB and 8 cores"                                            | Unmeasured at the time; the 16 GB is **unified CPU+GPU**, `stop_gemm` leaves `gemm-ai.service` running, and FAST-LIO's ikd-Tree grows unbounded with mapped area — so it is measured, with thresholds fixed in advance                                                                                                                         | `scripts/robot/measure.sh`                                                                     |

Two load-bearing details that were not refutations: YOLO11 export must use
**opset ≤ 17** (ultralytics defaults to 20, which TRT 8.5's parser rejects — the
error message in `vision/entrypoint.sh` and `build_perception` repeats this),
and `livox_ros_driver2` must be pinned ≥ 1.2.6 with `-DDISTRO_ROS=humble` (the
older `-DHUMBLE_ROS` spelling is _silently ignored_; `nav/Dockerfile` asserts it).

## Staged bring-up

Stages 0–4 touch nothing the other team is using. Each stage has a rollback
that is one command.

- **Stage 0 — scaffolding + the DDS crossing** (no sensors). Everything in this
  directory, the bridge's `ros_idl.py`/`perception_link.py`/`world_model.py`
  additions, and the `scripts/robot` edits; verified by `bun run check-types`,
  both pytest suites, and the 30-second on-Jetson crossing test (bridge holds
  domain 0 + domain 42 in one process, `reports_received: 0` is correct with
  nothing publishing). **Landed.**
- **Stage 1 — vision image + TRT engine** (no sensors; GPU is free with gemm up).
  `build_perception vision && build_perception engine && build_perception bench`.
  **Landed 2026-08-20.** `c3po/perception-vision:r35.3.1`, 9.97 GB; engine
  `yolo11n.fp16.plan`, 7.9 MB, in volume `c3po-trt-engines`. Measured on this
  part at MAXN (`/var/lib/nvpmodel/status` = `pmode:0000`), not predicted:

  | | |
  | --- | --- |
  | Throughput | 203.9 qps |
  | GPU compute | median **4.75 ms**, mean 4.90, p90 5.25, p99 6.20 |

  Inside the 5–8 ms the research predicted. At the detector's 10 Hz that is
  ~5 % of the GPU, on a GPU measured at `GR3D_FREQ 0%` with gemm running — so
  the detector is not what makes this stack fit or not fit.
- **Stage 2 — nav image** (no sensors). `build_perception nav`.
  **Landed 2026-08-20.** `c3po/perception-nav:humble`, 3.38 GB; every assertion
  held — fast_lio, livox_ros_driver2, c3po_perception, nav2_controller,
  nav2_bt_navigator, pointcloud_to_laserscan. Ask the other team before hogging
  8 shared cores for ~35 min.

  It took four attempts, and each failure was a real fact rather than a flake:
  the FAST-LIO twist patch was rejected because upstream had moved
  (`publish_odometry` at 628 not 618, `SharedPtr` not `SharedPtr &`) — the hunk
  is now **generated** against the pinned ref, not hand-written; and the Livox
  pins were wrong on both sides at once. SDK v1.2.5 lacked
  `kLivoxLidarTypeMid360s`, while driver `master` needs `DoubleEcho` symbols
  that exist on **no SDK tag**, only SDK master. The pair is now SDK `v1.3.1` +
  driver `1.2.6`, both tags, mutually consistent, with an assertion on each
  side so a one-sided bump fails loudly.
- **Stage 3 — the crossing end-to-end on synthetic data** (no sensors).
  `perception_up fake` + `run_c3po`, then `describe_surroundings` from a Claude
  Code session. This is where "absent is not empty" is proven across a process
  boundary, a container boundary and a DDS domain: kill the synthetic publisher
  and the summary must flip to `detector: offline` with a plain-language note,
  never an empty scene.
- **Stage 4 — Nav2 in isolation** (no sensors). `perception_up nav2-fake`,
  transition the lifecycle manager by hand, send one goal **with the bridge's
  gate closed**:
  `dropped_while_disabled` climbs, `last_sent` stays `None`, `/c3po/cmd_vel`
  holds ~20 Hz. A SIGILL here means MPPI got installed by accident.

  `nav2-fake` is `nav2.launch.py sources:=fake` — Nav2 over the synthetic
  `/odom`, `/scan` and `/c3po/objects` from `fake.launch.py`, plus a static
  `odom -> base_footprint`. That TF is not decoration: costmaps place
  themselves from TF, never from the `/odom` message, so without it they simply
  never update and it reads as a Nav2 misconfiguration. Note `perception_up
  nav2` (no suffix) is the REAL pipeline and **claims both sensors** — this
  stage was specified as sensor-free and for a while the only nav2 stage
  stopped gemm, which would have made "Nav2 in isolation" the most invasive
  step in the plan.
- **Stage 5 — first shared-sensor window: record a bag** (~30 min, gemm stops).
  `perception_up odometry`, then
  `ros2 bag record /livox/lidar /livox/imu /Odometry /cloud_registered_body /scan /tf /tf_static`,
  plus ~5 min of RealSense color+depth to disk. One window feeds days of
  offline tuning. **Hardware verification gates, in order, before anything else:**
  1. `ros2 topic hz /livox/lidar` ≈ 10 Hz — silence means `host_net_info` is
     wrong (the driver connects and simply never publishes).
  2. `/livox/imu` `linear_acceleration.z ≈ −1.0` standing still — Livox
     publishes in **g**, not m/s² (the driver assigns raw values,
     `lddc.cpp:490-495`). Negative confirms the unit is mounted
     inverted and the Rx(180) static TF is right. **If it reads +1.0, stop:
     the unit is not inverted and every Rx(180) in the TF tree must come out.**
  3. `tf2_echo odom base_footprint` — z stays ~0, roll/pitch ~0.
  4. `/odom` `twist.twist.linear.x` non-zero while the robot is hand-walked —
     zero means the FAST-LIO twist patch did not apply.
  5. Standing level and still: mean acceleration in `base_link` =
     `(0, 0, +9.81)` within a degree or two — a 2° residual pitch is a
     3.5 cm/m height error across a room.
- **Stage 6 — offline iteration against the bag** (no sensors, gemm back up).
  Tune `blind`, `filter_size_*`, `cube_side_length`, the laserscan height band
  and `angle_increment` fill rate, `min/max_obstacle_height`, the detector's
  confidence/depth bands — and measure the two constants currently placeholders:
  `lidar_height_m`, `base_in_body_xyz`, plus the camera extrinsic the detector
  holds. `docs/ROBOT-HARDWARE.md` carries the vendor-published Livox extrinsics
  and gemm's `base_link → lidar_link` `z = 1.0` marked _"APROXIMADA"_ — the
  vendor transform's direction is ambiguous as stated, so validate against a
  real cloud rather than trusting either number.
- **Stage 7 — live perception, robot stationary then hand-walked** (~60 min
  window). `perception_up perception && run_c3po`, with `measure.sh` sampling.
  Pass/fail thresholds (memory, CPU, thermal, EMC, topic-rate floors) are fixed
  in `scripts/robot/measure.sh` and printed before every run — edit them only
  with the reason written down. Functional gates: `describe_surroundings` names
  real objects at plausible ranges; walk to the robot's **left** and confirm
  `bearing_deg` is **positive** (D7's sign convention, and `turn`'s). If memory
  or rates fail, drop YOLO input 640→480 and the detector to 5 Hz before
  concluding the stack does not fit.
  Evidence baseline, measured 2026-08-18 with gemm up and nothing of ours:
  `RAM 2534/15388MB`, swap 0, `GR3D_FREQ 0%`, `tj@43.7C`, MAXN — GPU entirely
  free, ~12.7 GB headroom.
- **Stage 8 — armed navigation, supervised** (~90 min window, physical e-stop in
  hand). Prerequisites, all of them: Stage 7 passed every threshold; the
  bridge's `arm_navigation` tool exists with a TTL (auto-disarm after ~60 s with
  no goal), disarm on `stop_everything`, and disarm on `/cmd_vel` stale > 0.3 s;
  and someone has read `warn_if_other_commander` output and confirmed nothing
  else can drive. First goal: **1 m straight ahead, in an empty room, e-stop
  held** — the only non-zero `SET_VELOCITY` ever executed on this robot was
  0.17 m gantry-loaded on 2026-08-15 (`docs/ROBOT-API.md` §5.4); it has never
  run free-standing. Rollback: `stop_everything` (unilateral, needs nothing
  from the containers; its disarm-the-gate step is part of the `arm_navigation`
  prerequisite above — verify it before the window), then `stop_c3po`.

## Decisions that still need a human

- **A written sensor-window agreement with the other team.** Named windows;
  their `cmd_vel_to_loco` and our `arm_navigation` never armed simultaneously,
  in writing; each stack's stop path knows the other's container names;
  `gemm-ai.service` out of scope for both stop scripts (see
  `docs/OPERATIONS.md`).
- **Does the Livox already point somewhere else?** The Mid-360 _stores_ its
  unicast destination persistently in the device. If gemm's driver reconfigures
  it at launch, our bring-up mutates a shared device's persistent state, not
  just borrows a sensor. Read the gemm workspace's `MID360_config.json` on the
  robot before the first sensor window.
- **`arm_navigation`'s TTL and clamps.** The clamp values in
  `perception_link.py` are reasoned defaults, not measurements — someone who has
  watched this robot walk should set them.
- **The pending `connection.py` fix.** `DDS_INTERFACE=eth0` is currently logged
  but never applied — the vendor SDK's inline config overrides `CYCLONEDDS_URI`,
  so the bridge runs on autodetermine and works only because `docker0` is DOWN,
  and every perception stage brings containers up. The fix (scope
  `<Domain id="any">` to `id="0"`, pass the interface through to
  `ChannelFactoryInitialize`; note `ChannelConfigHasInterface` carries no
  `<Peers>` block, which is acceptable — multicast on eth0 is how the control
  board publishes) must land in a supervised window, never as a side effect of
  landing perception.
- **COCO's vocabulary.** YOLO11n has no `door`, `doorway` or `stairs` class —
  and stairs are what a walking humanoid most needs to not be surprised by. The
  detector will be technically working and practically blind to this robot's
  specific hazards. Fine-tune vs. open-vocabulary must be decided early: it
  changes the labels file and therefore what the LLM ever gets to reason about.
- **JetPack upgrade** only if the detector is genuinely blocked (it is not:
  TRT 8.5 builds it at opset ≤ 17). JP6 would delete the container split, but it
  is an OTA on a shared robot running a vendor image, invalidates every TRT
  engine, and breaks the bridge's pinned CycloneDDS/SDK stack.

## Not yet exercised

Everything in the file map exists. The vision image now builds, imports
TensorRT 8.5.2.2 under `--runtime nvidia`, and its engine benchmarks — but
**nothing has yet been run against a camera, and nothing has ever published on
domain 42 and been read by the bridge.** That last one is the keystone: every
piece exists and no two of them have talked to each other. Stage 3 is what
closes it, and it needs no sensors.

One pin worth knowing about: the image originally carried **numpy 1.17.4**, not
the intended one. `pip install "numpy<2"` looks like a pin and is not —
`l4t-jetpack:r35.3.1` already ships 1.17.4, which satisfies it, so pip resolved
to "already installed" and changed nothing. Now pinned exactly to 1.24.4 (the
last release with cp38 wheels; cp38 is forced by JetPack 5's
`python3-libnvinfer`, which hard-depends `python3 (<< 3.9)`) with a build-time
assertion, because an ABI mismatch against pyrealsense2/pycuda surfaces as a
segfault inside `np.asanyarray()` on a camera frame rather than an import
error. **The rebuild carrying that pin has not run yet.**

- `vision/c3po_vision/detector.py` — the capture→TRT→grounding→DDS loop the
  vision image's CMD points at. Its synthetic path (`C3PO_VISION_FAKE=1`, and
  `C3PO_VISION_DRY_RUN=1` to print to stdout instead of publishing) runs on a
  laptop with no camera, no CUDA and no CycloneDDS, and drives the real
  grounding maths; the TensorRT and pyrealsense2 paths have never executed.
  Unproven specifically: the engine binding shapes, the `(1, 4 + nc, anchors)`
  head layout, and the aligned-depth frame indexing.
- `vision/c3po_vision/stream.py` — the operator's MJPEG feed off the same
  frames. Its staleness contract, the decimation and the loopback default are
  covered by `tests/test_stream.py` on a laptop; what has **not** run is the
  numpy branch of `encode_jpeg` against a real pyrealsense2 frame, and Pillow on
  aarch64/py38 (the pin is asserted at build time, not at runtime).
- `tools/export_yolo11_onnx.py` — the off-robot ONNX exporter. Its contract:
  `opset=16, dynamic=False, simplify=False, nms=False, imgsz=640, batch=1`;
  hard-refuse any opset domain > 17, any symbolic input dim, any non-`ai.onnx`
  domain, and any end-to-end NMS op left in the graph. `nms=True` parses but
  injects NonZero/GatherND/ScatterND needing `enqueueV3`, to buy ~1 ms — keep
  NMS on the CPU. Do **not** check `ir_version`: TRT 8.5's parser only logs it
  (`ModelImporter.cpp:698`; the "max IR version 8" hard error is an ONNX
  _Runtime_ message). The labels file must come from `model.names` of
  the same checkpoint, never a hardcoded COCO list — otherwise a fine-tune
  silently reports "person" for a traffic cone. **Both halves have now run**
  (2026-08-20): `yolo11n.pt` -> 11 MB ONNX + 80-class `labels.txt`, verified
  `ai.onnx@16`, static `[1,3,640,640]`, output `[1,84,8400]` (the raw head, not
  the `[1,300,6]` an `nms=True` export produces), and no NMS op in the graph.
  TensorRT 8.5.2.2 parsed it and built the plan on the first attempt.

## File map

```
nav/            Dockerfile + entrypoint, fastlio-publish-twist.patch,
                ws/src/c3po_perception: g1_odom_tf.py, world_model_publisher.py,
                configs (MID360, FAST-LIO, Nav2, pointcloud_to_laserscan),
                launch/{fake,odometry,perception,nav2}.launch.py
vision/         Dockerfile + entrypoint (builds the TRT engine on first start),
                c3po_vision: detector.py, grounding.py, ros_idl.py, stream.py
config/         cyclonedds-domain42.xml — shared by both containers and the bridge
tools/          export_yolo11_onnx.py — runs OFF-robot, never in an image
tests/          Stage 0 harness: test_grounding.py, test_report_shape.py,
                test_stream.py
```

The bridge-side counterparts live in `apps/bridge/src/bridge/`: `sdk/ros_idl.py`
(hand-written `Twist_`/`Vector3_` — the typename strings and field order are the
load-bearing part), `sdk/perception_link.py` (the domain-42 link and cmd_vel
gate) and `world_model.py` (`from_report()`, the D7 contract). Operational
scripts live in `scripts/robot/`: `perception_up`, `build_perception`,
`measure.sh`, plus the perception-aware `run_c3po`/`stop_c3po`/`_common.sh`.
