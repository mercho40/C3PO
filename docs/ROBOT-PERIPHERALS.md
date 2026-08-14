# G1 Peripherals — the devices, who owns them, and what they publish

Companion to `ROBOT-INVENTORY.md`, which covers compute, networking, DDS and the loco API.
This one covers the **physical devices**: how each attaches, how it is addressed, what state
it was found in, **who holds it**, what it publishes, and what it would cost us to consume.

Verified against the robot on **2026-08-13/14**. The robot became unreachable at the end of
that session, so **nothing here can be re-checked until the next window** — every "right now"
below is a snapshot of that session, not a live fact.

Timestamps quoted from the robot are in its own clock, `Asia/Shanghai` (CST +0800), which
reads about a day ahead of local expectation. That is timezone, not skew.

Tags, same convention as `ROBOT-INVENTORY.md`:

- **[live]** — observed directly on the robot
- **[src]** — read from source, config or binaries on the robot
- **[web]** — from published documentation. **Two bodies, both unverified against this
  robot**: `G1-WEB-RESEARCH.md` (third-party, banner-marked unverified) and, folded in
  **2026-08-13**, **Unitree's own official G1 developer documentation** (45 pages). Prefer
  `[src]`/`[live]` over both, always — official is not the same as correct, and several G1
  pages are demonstrably copy-pasted from other robots. Citation rules and the wrong-robot
  list are in `ROBOT-API.md`'s legend; official pages are cited here by slug + vendor date.
- **[?]** — believed but _not_ verified; do not build safety-critical logic on these

---

## 0. The devices at a glance

| Device                     | Attaches via                                         | Address / node                          | Held by, at snapshot                 | Health                                     |
| -------------------------- | ---------------------------------------------------- | --------------------------------------- | ------------------------------------ | ------------------------------------------ |
| Livox Mid-360 LiDAR        | Ethernet, robot internal LAN — **also republished on DDS**, §1.5 | `192.168.123.120`, `0c:9a:e6:87:5c:4a`  | **not the Jetson** — some other host | alive, 2.5 ms RTT; not streaming to us     |
| Intel RealSense D435i      | USB 3.0 hub `2-2`, port 3 → `2-2.3`, `8086:0b3a`     | `/dev/video0–5`, IIO accel+gyro         | `teleimager.image_server` (video4)   | healthy; **depth and IR unclaimed**        |
| G1 "head camera"           | **is the D435i colour node** — see §3                | `/dev/video4`                           | nobody (`master_service` stopped)    | stopped, recoverable                       |
| G1 chest camera            | would be `/dev/video10`                              | —                                       | nobody                               | **absent** — no such device electrically   |
| Hands                      | RS485 behind FTDI FT4232H `0403:6011`; a Dex3 pair would be served from the **control board**, §4.2 | `/dev/ttyUSB0–3`                        | `brainco_hand_server` (ttyUSB1)      | one right hand answering; identity **[?]** — settle with §4.3a |
| Mic array (4 mics)         | control board, **not** the Jetson                    | UDP mcast `239.168.123.161:5555`        | `gemm-ai.service` joined the group   | live, continuously transcribed             |
| Speaker + RGB LED          | control board                                        | `voice` RPC service (`vui_service`)     | shared, **no arbitration, 3 writers** | live                                       |
| Body IMU                   | inside `LowState_`                                   | `rt/lf/lowstate`                        | shared (DDS)                         | live, ~20 Hz                               |
| Battery / BMS              | own DDS topic                                        | `rt/lf/bmsstate`                        | shared (DDS)                         | live, ~20 Hz — **never actually read**     |
| Wireless controller (R3)   | control board radio                                  | **`LowState_.wireless_remote[40]`** — `rt/wirelesscontroller` is Go2-only, §6.3 | shared (DDS)                         | unverified this session                    |

Two structural facts govern everything below.

**The Jetson carries almost no vendor payload — by design, and Unitree says so.**
`/unitree/module/` holds exactly two modules, `master_service` and `video_hub_pc4`, and the
vendor's own install bundle at `/home/unitree/g1plus_pc4_unitree_install/` confirms that is the
complete "pc4" payload — its `module/` contains only `master_service`. **[live]** Unitree states
the rule outright: *"**Unitree does not deploy services on the NVIDIA Jetson Orin module**"* and
*"PC1 is dedicated to the Unitree motion control program and is **not open to the public**.
Developers can only use PC2 for secondary development."* **[web]** (`about_G1` 2026-05-06,
`architecture_description` 2025-04-30. PC1 = `192.168.123.161`, PC2/NX = `192.168.123.164`.)

So every motion, audio, hand and state service (`sport`, `arm`, `voice`, `motion_switcher`,
`robot_state`, and a Dex3 resident driver if one exists) runs on the control board, which has no
SSH. **Those we can only reach over DDS, never by reading files** — which means a
filesystem-wide search on the Jetson is evidence about the Jetson and about nothing else. §4.2
records where that bit us.

**Firmware is `1.5.3.8`, product string `G1_Edu+`.** **[live]** Read from the staged OTA
directory `/unitree/ota/update/1.5.3/package_1.5.3.8_G1_Edu+_upk`; `version.json` itself has an
empty `Package` field and only stamps per-module versions (`master_service_pc4 1.0.0.2`,
`unitree_patch_pc4 1.0.0.6`, `video_hub_pc4 1.0.2.3`). This is the first hard firmware
identifier we have, and it is what makes the SDK version-skew trap in §7 legible.

---

## 1. Livox Mid-360 LiDAR

### 1.1 Addressing and ports

A plain network peer on the robot's internal wired LAN. From the Jetson's `eth0`
(`192.168.123.164/24`): `ping` 3/3, RTT 1.902/2.470/3.600 ms, `ttl=255` — a `ttl` of 255 is
characteristic of an embedded stack, not Linux. ARP: `192.168.123.120 lladdr
0c:9a:e6:87:5c:4a`. **[live]**

Ports, read from the deployed driver config rather than from memory — and note there are
**five**, not the four `ROBOT-INVENTORY.md` §4 records: **[src]**

| Purpose      | LiDAR-side | Host-side |
| ------------ | ---------- | --------- |
| Command      | 56100      | 56101     |
| Push message | 56200      | 56201     |
| Point data   | 56300      | 56301     |
| IMU data     | 56400      | 56401     |
| Log data     | 56500      | 56501     |

**Trap: the host side is `+1`.** A `tcpdump` filter on `udp port 56300` will see nothing while
`56301` sees everything. The same numbers appear in the upstream vendor default, so this is the
stock Mid-360 scheme, not a Unitree customisation.

Default Livox addressing is `192.168.1.1XX` where `XX` is the serial's last two digits **[web]**;
ours is `192.168.123.120`, so Unitree changed both subnet and host.

### 1.2 The driver config actually on the robot

Deployed: `/home/unitree/gemm/ros2_ws/src/gemm/gemm_bringup/config/mid360_config.json`
(md5 `c55200ef4aafec7af163a70c9edc699e`), verbatim: **[src]**

```json
{"lidar_summary_info":{"lidar_type":8},
 "MID360":{"lidar_net_info":{"cmd_data_port":56100,"push_msg_port":56200,"point_data_port":56300,"imu_data_port":56400,"log_data_port":56500},
           "host_net_info":{"cmd_data_ip":"192.168.123.164","cmd_data_port":56101,"push_msg_ip":"192.168.123.164","push_msg_port":56201,"point_data_ip":"192.168.123.164","point_data_port":56301,"imu_data_ip":"192.168.123.164","imu_data_port":56401,"log_data_ip":"","log_data_port":56501}},
 "lidar_configs":[{"ip":"192.168.123.120","pcl_data_type":1,"pattern_mode":0,
                   "extrinsic_parameter":{"roll":180.0,"pitch":-2.3,"yaw":0.0,"x":0,"y":0,"z":0}}]}
```

`lidar_type: 8` = Mid-360. `pcl_data_type: 1` = `kLivoxLidarCartesianCoordinateHighData` (32-bit
mm cartesian, the normal SLAM choice). `pattern_mode: 0` =
`kLivoxLidarScanPatternNoneRepetive` — the non-repetitive rosette that grows coverage with
integration time.

**Three config paths look plausible and are wrong.** Getting this wrong costs an afternoon:

- `…/build/gemm_bringup/config/mid360_config.json` **exists as a directory entry** but is a
  dangling symlink to `/ws/ros2_ws/src/…` — `/ws` is the gemm container's mount point and does
  not exist on the host, so any `cat`/`stat` from the Jetson returns ENOENT and the file reads
  as "not there". **[live]**
- `…/install/gemm_bringup/share/…` genuinely does not exist, even though `livox.launch.py`
  resolves its `user_config_path` through `FindPackageShare('gemm_bringup')`. The launch only
  works from inside the container's install space. **[live]**
- `src/external/g1pilot/config/livox_mid.json` has the right LiDAR IP but a `host_net_info`
  pointing at `192.168.123.123`, an address on **no** interface of this robot. Its symptom, in
  the gemm authors' own words: *"cero paquetes, en silencio."* **[src]**

### 1.3 The single-host unicast constraint — and why the LiDAR cannot be shared

This is the most consequential fact about the sensor, and it is not an OS-level lock.

`host_net_info` is **not** read by `livox_ros_driver2`. Its
`src/parse_cfg_file/parse_livox_lidar_cfg.cpp` parses only the `lidar_configs` array; grep for
`host_net_info` there returns nothing. The same JSON path is handed to the SDK at
`src/lds_lidar.cpp:142` (`LivoxLidarSdkInit(path_.c_str())`), and
`/usr/local/lib/liblivox_lidar_sdk_shared.so` contains the `host_net_info` literals and the
error *"Parse host net info failed, has not host_ip or cmd_data_ip."* The SDK pushes those
addresses to the sensor via `SetLivoxLidarPointDataHostIPCfg` / `…ImuDataHostIPCfg` /
`…StateInfoHostIPCfg`, and `/usr/local/include/livox_lidar_def.h` shows where they land: **[src]**

| Register | Field                                    |
| -------- | ---------------------------------------- |
| `0x0006` | `HostPointIPInfo pointcloud_host_ipcfg`  |
| `0x0007` | `HostImuDataIPInfo imu_host_ipcfg`       |
| `0x0008` | `LivoxIpCfg ctl_host_ipcfg`              |

**The destination lives in the sensor's own flash-backed config, not in the client.** There is
no multicast and no second stream. The sensor cannot be shared — only handed over. Whoever
starts a driver **last** re-points it for everyone, and killing that process does not hand it
back, because the address stays written.

The gemm authors state the same constraint in `livox.launch.py`'s docstring: *"OJO: el Mid-360
unicastea el point data al host configurado. Al levantar este driver, el LiDAR pasa a mandarnos
el stream a nosotros; mientras corre, el SLAM del vendor puede quedar sin datos. Es esperado y
reversible (el vendor lo reconfigura la próxima vez que arranca)."* **[src]** Their
"reversible" claim is explicitly untested and should not be relied on.

Handing it back is worse than an IP change, because the driver also **writes sensor settings**
on every discovery: `livox_lidar_callback.cpp`'s `LidarInfoChangeCallback` calls
`SetLivoxLidarPclDataType`, `SetLivoxLidarScanPattern`, `SetLivoxLidarBlindSpot`,
`SetLivoxLidarDualEmit`, `SetLivoxLidarInstallAttitude`, `SetLivoxLidarWorkMode(kLivoxLidarNormal)`
and `EnableLivoxLidarImuData`. The next owner inherits all of that unless it sets its own. **[src]**

Two escape hatches exist in Livox-SDK2's README but not in the ROS driver's: `"master_sdk":
false` makes the SDK listen-only, and `"multicast_ip"` fans the stream to a group — a two-line
JSON edit, since the driver passes the config straight through to SDK2. **[web]** Untested here,
and a slave only receives if someone already configured the group as master.

### 1.4 Nothing was publishing it, and not to us

Four independent checks, all negative: **[live]**

- No driver process (`ps aux | grep -iE 'livox|fast_lio|point_lio|lio'` → empty).
- No containers at all — `gemm-bringup`, where the driver would run, is `Exited (137)`.
- No socket bound to any `56[1-5]0[01]` port.
- Passive `/proc/net/snmp`: UDP `NoPorts` flat at 2378 across 9 s while `InDatagrams` rose ~15k
  (~1.6k pkt/s, the control board's DDS multicast). If the sensor were unicasting to
  `192.168.123.164` with nothing bound, `NoPorts` would climb fast. It does not.

So the Mid-360's current unicast target is **some host other than the Jetson** — most likely the
control board with the vendor `lidar_driver` owning it, or `point_send_en` (register `0x0003`)
disabled outright. Unresolved.

### 1.5 The sharing-friendly path: the vendor `lidar_driver` service

There is a second route that does **not** steal the unicast: the control board keeps the sensor
as its peer and republishes over DDS. Measured off two rosbags recorded from this very robot,
and **now confirmed by Unitree's own most recent LiDAR page** (`lidar_services_interface`,
2026-06-04): **[live]** + **[web]**

| Topic                           | Type                                    | Rate (measured / documented) | Frame        |
| ------------------------------- | --------------------------------------- | ---------------------------- | ------------ |
| `rt/utlidar/cloud_livox_mid360` | `sensor_msgs::msg::dds_::PointCloud2_`  | 9.94 / 9.82 Hz — **10 Hz**   | `livox_frame`|
| `rt/utlidar/imu_livox_mid360`   | `sensor_msgs::msg::dds_::Imu_`          | 199.6 / 198.2 Hz — **200 Hz**| `livox_frame`|

**This settles the sharing question outright, and it is the route we should take.** The raw
Livox UDP path is single-destination *by construction* (§1.3) — one `host_ip` in the sensor's
own flash-backed config, and writing it steals the feed from whoever holds it. The DDS
republish is an ordinary pub/sub topic that any number of subscribers can join. **A
`describe_surroundings` LiDAR source should subscribe `rt/utlidar/cloud_livox_mid360` and MUST
NOT touch the Livox config.** Two known blockers remain, both already ours: `PointCloud2_` is
in our venv but `sensor_msgs::msg::dds_::Imu_` is not, so the 200 Hz IMU topic needs
hand-written IDL; and the topics only exist while `lidar_driver` is switched on — now a
**documented dependency**, not an inference, and switchable via `robot_state` 1001 as well as
via the App.

**Extrinsics, newly documented** (nothing in our docs had these): **[web]**

- Mid-360 mounted **in the middle of the head**. IMU-to-lidar offset `(0.011, 0.02329,
  −0.04412)` m with **no rotation**.
- Lidar relative to the robot frame: `(−0.0, 0.0, −0.47618)` m, *"placement method is
  inverted"*, pitch-axis inclination **−2.3°** — which matches the deployed
  `roll 180.0, pitch −2.3` extrinsic in §1.2 exactly. ⚠️ The z sign is wrong for a
  head-mounted sensor as literally stated, so **treat the direction of that transform as
  ambiguous and validate against a real cloud** before using it. It is still the only measured
  alternative to gemm's `z = 1.0` marked *"APROXIMADA: medir en el robot"* (§1.7).
- SLAM output frame: **X forward, Z up** (hence Y left if right-handed), origin at the
  Mid360-IMU.

⚠️ **Hardware change we sit on the wrong side of:** *"The lidar of G1 produced after **April
2026** has been changed to '**Mid360s**'. For self-developed users, LIVOX official Livox-SDK2
and livox_ros_driver2 need to be updated."* **[web]** This robot arrived **2026-08-04**, i.e.
after the cutover, so it may carry a Mid360s and **any pinned Livox-SDK2 / livox_ros_driver2
version in `DEPLOYMENT.md` §3's ROS 2 container may be too old.** This is an argument for the
DDS republish rather than a blocker — but check the sensor model before anyone stands up
`livox_ros_driver2`.

Also newly recorded: Unitree's own pre-use check — *"confirm there are no abnormalities in the
raw point clouds and IMU data, and the head is fixed vertically upward without looseness. If
the front and rear point cloud frames are obviously stratified when the lidar illuminates the
same static vertical plane in a static state, or the IMU values are significantly abnormal,
contact sales for lidar repair."* **[web]**

Gated on the vendor `lidar_driver` service being switched on (version ≥ 1.0.0.5, confirmed by
the vendor page). Toggled with
no vendor SDK: publish `unitree_api::msg::dds_::Request_` on `rt/api/robot_state/request`,
api_id **1001 `ServiceSwitch`**, parameter `{"name":"lidar_driver","switch":0|1}`; **1003
`ServiceList`** (parameter `{}`) enumerates every service as `{name, status, protect}`. The
gemm client is `gemm_bringup/tools/g1_service.py`. **[src]**

**Trap, and it already burned them once:** a 2026-08-07 conclusion that "these topics do not
exist in any DDS domain" was wrong — it came from looking with **RELIABLE** QoS at a service
that was switched **off**. Their note: *"son BEST_EFFORT, y `ros2 topic hz`/`echo` con el
default no ve nada aunque estén fluyendo."* **[src]** Except the bag metadata *they* produced
records those publishers as **RELIABLE**, KEEP_LAST depth 1, VOLATILE. **[live]** The prose and
the metadata contradict each other; trust the metadata, but verify before relying on either.
Depth-1 KEEP_LAST also means a slow subscriber silently drops rather than queues.

Python consumability is asymmetric and decides the work: `unitree_sdk2py` **does** ship
`PointCloud2_` and `PointField_`, but **not** `sensor_msgs::msg::dds_::Imu_` — so the cloud is
consumable today and the LiDAR IMU needs a hand-written IDL. **[src]**

### 1.6 Message layouts if we run our own driver

Selected by the ROS param `xfer_format` (`lddc.h:41-47`): `0` = `sensor_msgs/PointCloud2`,
`1` = `livox_ros_driver2/CustomMsg`, `2` = PCL XYZI; `3` is internal. Driver default in code is
`0`, but gemm's launch **overrides it to 1**. Topic is `/livox/lidar` either way
(`multi_topic` pinned to 0). IMU on `/livox/imu`, `sensor_msgs/Imu`, with **no rate control at
all** — one message per received packet, so the ROS rate equals the sensor's 200 Hz push. **[src]**

`CustomMsg`: `header`, `uint64 timebase`, `uint32 point_num`, `uint8 lidar_id`, `uint8[3] rsvd`,
`CustomPoint[] points`, where `CustomPoint` = `uint32 offset_time` (ns from timebase),
`float32 x,y,z`, `uint8 reflectivity`, `uint8 tag`, `uint8 line`.

`PointCloud2` path: 7 fields, `point_step = sizeof(LivoxPointXyzrtlt)` — x/y/z FLOAT32 at
0/4/8, intensity FLOAT32 at 12, tag UINT8 at 16, line UINT8 at 17, timestamp FLOAT64 at 18;
`height=1`, unorganised. `line` ranges 0..3 (`comm.h:82 kLineNumberMid360 = 4`) — that 4 is the
value FAST-LIO-family configs want for `scan_line`/`N_SCANS`.

### 1.7 The inverted-mount IMU trap

The G1 mounts the Mid-360 **upside down**. The deployed extrinsic is `roll 180.0, pitch -2.3,
yaw 0.0`, and it is pushed **into the sensor** (`SetLivoxLidarInstallAttitude`, register
`0x0012`). Host-side compensation is off — `pub_handler.cpp:132` sets
`packet.extrinsic_enable = false` and the per-point extrinsic branches are gated on that flag. **[src]**

**It is not applied to the IMU on either side.** With the sensor inverted, gravity reads
negative, `gravity_align_en` aligns against a flipped vector, and LIO diverges **without
throwing any error**. deepglint had to patch the driver for exactly this. The check gemm
prescribe, and it is the right one: with the robot standing still, look at the **sign of
`/livox/imu` linear_acceleration.z**. Related **[web]** claim worth testing at the same time:
the driver publishes IMU acceleration in **g, not m/s²** (no 9.8 factor anywhere; upstream
issue #157 open since 2024-12).

Frame naming also splits: the driver publishes `livox_frame` while gemm's Nav2 expects
`lidar_link`, bridged by a separate `static_transform_publisher base_link → lidar_link` with
`z = 1.0` marked in-file as *"APROXIMADA: medir en el robot."* No measured value exists anywhere
on the box. **[src]**

### 1.8 What this costs us

Our bridge does not consume the LiDAR at all: `mcp_server.py:815` passes `lidar_online=False`
unconditionally, and `world_model.py:171-173` already refuses correctly — *"LiDAR is OFFLINE —
free-space distances are unavailable, not infinite."* The only other reference is a fault label
(`faults.py:89-91`, "400 — Radar / LiDAR", "400_2 — PointCloud data abnormal"). So integrating
it is greenfield; there is nothing to unpick. **[src]**

No FAST-LIO2, Point-LIO or FAST-LIVO2 exists anywhere on this robot — an exhaustive search over
`/home/unitree`, `/unitree` and `/opt` returned zero. `DEPLOYMENT.md` §3 lists FAST-LIO2 in the
unbuilt perception container; that remains accurate, and nothing has been staged for it. **[live]**

Published specs worth carrying into any SLAM design **[web]**: 905 nm, 40 m @10 % / 70 m @80 %
reflectivity, 0.1 m blind zone, 360° × −7°…+52° FOV (asymmetric, biased **downward** once
mounted inverted), 200 000 pts/s, 10 Hz, ICM40609 IMU, 6.5 W avg / 14 W peak, IP67. Two
consequences: a single 10 Hz frame is **sparse** — do not evaluate it as a 32-beam spinner, and
prefer LIO that integrates per-point timestamps over frame-wise ICP; and the 0.1 m blind zone
plus that FOV means the floor and the robot's own arms will appear, so self-filtering is
mandatory. Also **[web]**: the unit stops operating automatically above ~80 °C shell
temperature, which would look exactly like a network fault.

---

## 2. Intel RealSense D435i

### 2.1 Attachment and node map

`8086:0b3a`, serial `255323064200`, USB path `2-2.3` (hub `2-2` = `0bda:0411` 4-port USB 3.0),
driver `uvcvideo`, kernel 5.10.104. It is the **only** camera physically attached to this
Jetson: `v4l2-ctl --list-devices` returns exactly two entries, the RealSense and the Tegra CSI
capture path (`/dev/media0`, **zero** `/dev/video` nodes bound — no MIPI/CSI camera). **[live]**

**Officially confirmed as the G1's head sensor, and officially confirmed to have no DDS
path.** *"The G1 robot depth camera is located overhead and is a **realsense D435i** … binocular
infrared camera (global shutter), laser transmitter, RGB camera (rolling shutter), and 6-axis
IMU."* **[web]** (`depth_camera_instruction`, 2025-11-17.) The entire page covers only
`realsense-viewer`, `realsense-ros` and librealsense — **it names no DDS topic and no Unitree
service.** That settles the asymmetry between the two head sensors: **the LiDAR is republished
on DDS and is multi-consumer (§1.5); the D435i is not, and stays a single-owner V4L2/USB
device** exactly as `DEPLOYMENT.md` §2 says. Any C3PO vision source must negotiate with
whoever holds the node, not expect a topic. It also resolves `G1-WEB-RESEARCH.md` §4.3's
uncertainty between a UVC binocular head camera and a D435i, in favour of the D435i for this
generation.

Vendor reference configuration worth matching (**[web]**): depth `640×480 Z16 @60`, both IR
`640×480 Y8 @60`, **colour `848×480 BGR8 @60`** (note 848, not 640), gyro 400 Hz, accel 200 Hz;
stereo `VISUAL_PRESET = HIGH_ACCURACY`, auto-exposure on, emitter on, laser power 150;
temporal filter with `HOLES_FILL 6`, `SMOOTH_ALPHA 0.4`, `SMOOTH_DELTA 20`. And a timestamping
rule stated as a `// do not use` comment against `frame.get_timestamp()`: **the vendor uses
`clock_gettime(CLOCK_REALTIME)` instead** — which is precisely why §8's time-sync question
matters if we ever fuse frames with DDS state. USB 2.0 needs a hub adapter and degrades
resolution/frame rate.

The six nodes resolve unambiguously by USB interface: **[live]**

| Node          | USB iface  | Role                       | Formats                                   |
| ------------- | ---------- | -------------------------- | ----------------------------------------- |
| `/dev/video0` | `2-2.3:1.0`| Depth capture              | `Z16`                                     |
| `/dev/video1` | `2-2.3:1.0`| Depth metadata             | —                                         |
| `/dev/video2` | `2-2.3:1.0`| Infrared / stereo capture  | `GREY`, `UYVY`, `Y8I`, `Y12I`             |
| `/dev/video3` | `2-2.3:1.0`| IR metadata                | —                                         |
| `/dev/video4` | `2-2.3:1.3`| **Colour capture**         | `YUYV`, `BYR2`                            |
| `/dev/video5` | `2-2.3:1.3`| Colour metadata            | —                                         |

Resolutions, from `--list-formats-ext`: **[live]**

- **Depth `Z16`** — 256×144 @300/90; 424×240, 480×270, 640×360, 640×480, 848×480 @90/60/30/15/6;
  848×100 @300/100; 1280×720 @30/15/6. Depth at 720p tops out at 30 fps.
- **IR** — same ladder for `GREY`/`UYVY`/`Y8I`; `Y8I` adds 1280×800 @30/15 (native stereo
  resolution); `Y12I` only 640×400 and 1280×800 @25/15. `Y8I`/`Y12I` are **interleaved
  left+right** — librealsense splits them; there is no second node for the right imager.
- **Colour `YUYV`** — 320×180 up to **1920×1080 @30/15/6**. `BYR2` (Bayer, experimental) at
  1920×1080 @30 only. This is the node every consumer on this robot fights over.

### 2.2 The IMU is not a V4L2 device

The `i` variant is confirmed: two IIO devices hang off the RealSense HID interface `2-2.3:1.5` /
`0003:8086:0B3A.0001` — `iio:device0` = `accel_3d`, `iio:device1` = `gyro_3d`, each with its own
trigger. `in_accel_scale = 0.009806650` (m/s² per LSB, i.e. g/1000);
`in_anglvel_scale = 0.001745329` (rad/s per LSB = exactly 0.1 °/s). Idle sampling frequencies
read accel 10 Hz, gyro 0 Hz, and neither device exposes `sampling_frequency_available`, so the
selectable rate list cannot be read from sysfs. **Those are idle HID defaults, not what
librealsense would configure** — treat them as **[?]**. **[live]**

The useful consequence: the IMU surfaces through **HID/IIO, not a `/dev/video` node**, so an
IMU-only consumer does not contend for V4L2 at all. **[live]** + **[web]** The sensor itself is a
Bosch BMI055, **not factory-calibrated** (non-zero angular velocity at idle, gravity ≠ 9.80665),
with a depth-to-IMU extrinsic that is precalculated and cannot be modified. **[web]**

### 2.3 Exclusivity, and who held it

`/dev/videoN` is a single-owner kernel resource — but **per node, not per camera**. At the
snapshot, `lsof /dev/video*` returned exactly one holder, and only on `video4`: **[live]**

```
python  5850  unitree  15u  CHR  81,4  /dev/video4
```

PID 5850 = `…/xr_teleoperate/envs/tv/bin/python -u -m teleimager.image_server`, PPID 5848 =
`…/xr_teleoperate/scripts/_image_service_watchdog.sh` (itself PPID 1, setsid-detached), cgroup
`user.slice/user-1000.slice/session-27.scope` — **a human-launched SSH-session process, not a
service**. Neither is recorded anywhere in `ROBOT-INVENTORY.md`. See §7.

**`/dev/video0` (depth) and `/dev/video2` (IR) had no holder at all.** That is our opening:
Intel's own doc says multiple librealsense clients can coexist *"as long as no two users try to
stream from the same camera endpoint"*, with Depth, Colour and Motion as independent endpoints
**[web]**. So C3PO may be able to take depth without disturbing anyone — but the same doc is
RS400-era and does not say whether D435i depth and IR share an endpoint. **Untested.** A
collision looks like `xioctl(VIDIOC_S_FMT) failed … Device or resource busy`.

### 2.4 What is actually streaming today

`teleimager.image_server` is the **only live camera feed on the robot**: JPEG frames over a
ZeroMQ `PUB` socket, plus a config REQ/REP socket. **[live]**

| Property   | Value                                                            |
| ---------- | ---------------------------------------------------------------- |
| Image PUB  | `tcp://0.0.0.0:55555`                                            |
| Config REP | `tcp://0.0.0.0:60000`                                            |
| Wire format| JPEG bytes — `cv2.imencode(".jpg", bgr_numpy)` then publish       |
| Geometry   | 540×960 @ 30 fps, monocular, `type: opencv`, `video_id: 4`        |

**It binds `0.0.0.0`, not `127.0.0.1` as its own config comment claims.** That makes it a plain
TCP feed reachable from the Mac over Wi-Fi — unlike every DDS multicast path, which needs the
wired robot LAN (`ROBOT-INVENTORY.md` §1, SPEC §10.2). If we ever need a camera on the Mac in a
hurry, this is the one transport that already crosses that boundary. **[live]**

Two teleimager details worth knowing before reusing it: its `type: uvc` branch **silently
ignores `video_id`** (it resolves only via `physical_path` or `serial_number`, with no
fallback), so a uvc-typed camera configured with only `video_id` is never constructed and the
server runs publishing nothing, with no fatal error — hence the working config uses
`type: opencv`. It also ships a native `type: realsense` driver (`--rs` flag + serial number)
that can emit **depth** over ZMQ, which is the shortest path to depth-over-TCP if we want it. **[src]**

The colleague's ROS node is a different consumer of the same node, and it was **down**:
`gemm-bringup` is `Exited (137)` (SIGKILL/OOM) from ~24 h before the snapshot. The container is
`--network host`, `Privileged=true`, bind-mounts all of `/dev:/dev`, and has
`restart=unless-stopped` — so it **will** reclaim `/dev/video4` on the next docker daemon
restart or reboot and fight teleimager's watchdog. Its live profile is deliberately colour-only
(`enable_depth=false`, `align_depth.enable=false`, `enable_gyro/accel=false`), contract topic
`/camera/camera/color/image_raw/compressed`; depth, aligned depth and a fused IMU
(`unite_imu_method=2`) appear only in `record.launch.py`. `ROBOT-INVENTORY.md` §4's "publishes 5
`/camera/*` topics" could not be re-verified — the container is exited and `ros2 topic list`
segfaults on this box (§2 of that doc). **[live]** + **[src]**

### 2.5 Jetson-specific pitfalls

- **`pyrealsense2` is a per-interpreter problem, not an impossibility.** The on-robot comment
  claims the aarch64 PyPI wheel needs GLIBC ≥ 2.32 and this Jetson has 2.31. The GLIBC part is
  true (`ldd 2.31-0ubuntu9.16`), the conclusion is too broad: `pyrealsense2 2.55.1.6486` **is**
  installed under `gemm_ai`'s Python 3.8 venv and imports successfully, and native
  `librealsense 2.50.0` is present from ROS Noetic at
  `/opt/ros/noetic/lib/aarch64-linux-gnu/librealsense2.so.2.50.0`. What is missing is a wheel for
  the teleop py3.10 venv and for **our bridge's py3.12** venv. A working aarch64 binding exists
  on the box to copy the approach from. **[live]**
- **Node numbers are enumeration-order dependent — use the stable symlinks.** `/dev/v4l/by-path/`
  gives `platform-3610000.xhci-usb-0:2.3:1.0-video-index0` → depth, `…:1.0-video-index2` → IR,
  `…:1.3-video-index0` → colour; `/dev/v4l/by-id/` keys on the serial. This solves the
  "video_id may shift after reboot" problem the colleague documents. **[live]**
- **Permissions are already fine.** `crw-rw-rw-+ root:plugdev` on all six nodes, from
  `/lib/udev/rules.d/60-ros-noetic-realsense2-camera.rules` (`idProduct=="0b3a"`, `MODE:="0666"`,
  `GROUP:="plugdev"`); `unitree` is in `video` and `plugdev`. teleimager's `setup_uvc.sh` was
  **never run** here (`/etc/udev/rules.d/10-libuvc.rules` absent, `/etc/sudoers.d/` holds only
  `README`), which is why its log shows `sudo modprobe -r uvcvideo` failing with *"a terminal is
  required to read the password"*. That failure is benign. **[live]**
- **No calibration files exist on disk, and that is correct.** No `camera_info` YAML, no
  intrinsics or extrinsics, no depth-to-colour alignment table. The D435i stores intrinsics,
  stereo extrinsics, depth scale and IMU calibration in on-camera flash; librealsense reads them
  at runtime. Consequence: **any consumer wanting aligned depth must go through
  librealsense/realsense2_camera or read intrinsics off the device — they cannot be picked up
  from a file.** The only calibration JSON on the box is a D500-series *example*, not applicable. **[live]**
- **[web] risks not yet tested here:** RSUSB vs V4L-native backend on Jetson (verified boards are
  AGX-class; Orin **NX** is not on the list, and an OTA that bumps the L4T kernel silently
  unloads a hand-patched module); and `librealsense` moved org to `realsenseai/`, dropping the
  "Intel" prefix from device names, so code matching `"Intel RealSense D435I"` may break across
  an upgrade.

---

## 3. The G1's own cameras via `video_hub_pc4` — DEGRADED

### 3.1 The correction that matters most: the head camera is the D435i

`ROBOT-INVENTORY.md` §4 lists "Intel RealSense D435i" and "G1 head/chest cameras" as two
separate peripheral rows. **For the head, on this unit, that is wrong.**
`/unitree/etc/master_service/service/video_hub_pc4` `Start.Cmd`, verbatim: **[src]**

```
export CYCLONEDDS_URI=/unitree/module/video_hub_pc4/cyclonedds.xml;
/unitree/sbin/start-stop-daemon --start --background --make-pidfile \
  --pidfile=/unitree/var/run/videohub_pc4.pid \
  --exec /unitree/module/video_hub_pc4/videohub_pc4 /dev/video4
```

The camera argument is literally `/dev/video4` — the D435i colour node — and the binary embeds
the same string as its default. This matches the Weston Robot note already in
`G1-WEB-RESEARCH.md` §4.3 that later G1 batches connect the D435i to the development computer.

**Consequence: three independent consumers target one V4L2 node** — vendor `videohub_pc4`, the
colleague's `realsense2_camera_node`, and `teleimager`. Only one can win, and there is no
arbitration beyond `open()` returning EBUSY.

### 3.2 What is broken, and how far the root cause was established

**Head node — cause fully established. [live]** `videohub_pc4` is not running because
`master_service.service` is `inactive (dead) since Fri 2026-08-14 01:40:34 CST`, with
`Process: 4600 ExecStop=/etc/init.d/master_service stop (code=exited, status=0/SUCCESS)` and
journal lines `Stopping LSB: master service init script…` / `master_service.service:
Succeeded.` That is a clean, deliberate `systemctl stop`, **not a crash and not an OOM**. It is
an LSB init script wrapped by `systemd-sysv-generator` with `Restart=no` and
`RemainAfterExit=yes`, so it will **not** self-recover — but it does start at boot, and did
(01:34:49). Timeline: 01:34 boot → both videohubs start → heartbeats every 5 s → 01:40:34
stopped → 01:42/01:43 teleimager takes `/dev/video4`.

Who stopped it and why is documented, and it is the sanctioned workaround: the colleague's
`xr_teleoperate/scripts/start_image_service.sh` prints, verbatim, *"Si es 'videohub_pc4'
(servicio propio de Unitree), pararlo con: sudo systemctl stop master_service"* — the same thing
a Unitree maintainer sanctions **[web]**. So a human ran exactly that to free the camera. **[src]**

**Chest node — root cause NOT established.** `video_hub_pc4_chest` is configured for
`/dev/video10`, which does not exist. What we established: **[live]**

- The config is **stock and unmodified** — `module.json` version 1.0.2.3, commit
  `1899ba6f9237dd2c323d5feb9877bb540e57ca61`, all files dated 2025-04-30, installed 2025-05-19.
  No recent OTA touched it. The chest node is doing exactly what Unitree ships it to do.
- The highest `/dev/video` node on this box is `video5`; all six belong to the one RealSense;
  `/dev/v4l/by-id` and `by-path` list only RealSense entries; both USB hubs enumerate healthily
  **with free ports**. **There is simply no second UVC camera presenting to this Jetson.**
- This **refutes** `G1-WEB-RESEARCH.md` §4.3's hub-event hypothesis (*"all G1 cameras hang off
  one USB-C hub in the neck, so a hub-level event takes out several `/dev/video` nodes at
  once"*): if a shared neck hub had dropped, the RealSense on `2-2.3` would have gone with it,
  and it did not.

What we could **not** establish is whether a chest camera was ever fitted, or is unplugged, or
is dead. That needs the kernel's enumeration history, and `dmesg` is root-restricted here
(`/proc/sys/kernel/dmesg_restrict=1`, `sudo` needs a password). **Do not write "the chest camera
failed" anywhere — the honest statement is "no second camera is electrically present, cause
unknown."**

### 3.3 The trap: "alive" never meant "producing frames"

`master_service` reported the chest service **alive** for its entire 6-minute life — heartbeats
`child service is alive, name:video_hub_pc4_chest` every 5 s — while `/dev/video10` did not
exist. Two reasons: `start-stop-daemon --status` only checks pidfile + exec path, and the binary
contains a retry loop with the strings `check video device loop` / `video device not work, wait
30 seconds, and then try again` / `video device works normal`. **[live]**

You will see a healthy service in the supervisor log and conclude the chest camera works. It
does not, and it never did on this boot. `ROBOT-INVENTORY.md` §4 recorded that node as running,
which was true and also meaningless.

### 3.4 What the vendor path would give us, if restarted

This contradicts `G1-WEB-RESEARCH.md` §4.3's *"no published DDS image topic for the G1 and no
RTSP endpoint anywhere."* The web doc is right about RTSP and **wrong about the DDS topic**.
`strings` on `/unitree/module/video_hub_pc4/videohub_pc4` (leaked source path
`/home/unitree/sjy/g1_videohub_nx/videohub_pc4.c`): **[src]**

| Mechanism         | Detail                                                                                          |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| DDS stream topic  | `rt/frontvideostream`, type `unitree_go::msg::dds_::Go2FrontVideoData_`                          |
| DDS request/reply | `rt/api/videohub/request` / `…/response` (+ internal `rt/videohub/inner`), `Request_`/`Response_` |
| RTP multicast     | `…rtph264pay ! udpsink host=230.1.1.1 port=1720 multicast-iface=eth0 sync=false`                  |

The GStreamer pipeline takes 1920×1080 YUY2 @15 fps and produces three outputs, all NVENC-
accelerated: **720p H.264 @8 Mbps**, **1080p JPEG**, **360p H.264 @800 kbps**.

The chest binary is a reduced variant: its API topics are `rt/api/videohub_chest/{request,
response}`, its pipeline is JPEG-only, and it has **no `rt/frontvideostream` writer** — so the
chest camera would only ever have been reachable as a request/response snapshot, never as a
continuous stream. **[src]**

**The client already exists in our bridge venv.** `unitree_sdk2py/go2/video/video_api.py`
defines `VIDEO_SERVICE_NAME = "videohub"`, `VIDEO_API_VERSION = "1.0.0.1"`,
`VIDEO_API_ID_GETIMAGESAMPLE = 1001`, and `VideoClient.GetImageSample()` calls
`_CallBinary(1001, [])`. The service name maps exactly onto the binary's
`rt/api/videohub/request|response`, so this Go2-labelled client **should** address the G1 head
videohub unchanged; a chest client would need service name `videohub_chest`. Untested — the
service is stopped and an RPC is a write. **[src]**

One mismatch to expect: the shipped `Go2FrontVideoData_` IDL is
`{time_frame: uint64, video720p, video360p, video180p}`, but the G1 pipeline has **no 180p
appsink** — so `video180p` is presumably always empty. **[?]**

### 3.5 Two operational hazards from this module

**An unowned shared dependency.** `videohub_pc4` embeds the rpath
`/home/unitree/cyclonedds_ws/install/cyclonedds/lib` and links `libddsc.so.0` — the *same*
third-party CycloneDDS 0.10.2 tree in a user home directory that our `CYCLONEDDS_HOME` points at
(`ROBOT-INVENTORY.md` §2). Deleting or moving that directory breaks a **root-owned vendor
firmware service**, not just C3PO. Its config `/unitree/module/video_hub_pc4/cyclonedds.xml` is,
in full: `<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eth0"
priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>` — which
independently corroborates our `DDS_INTERFACE=eth0` + 0.10.2-schema decision: the vendor pins
`eth0` too. **[src]**

**It comes back on reboot and will fight.** `master_service` starts at boot and re-grabs
`/dev/video4`; teleimager's watchdog respawns up to 20 times, 3 s apart. Whoever wins is a race.
Service control is `/unitree/sbin/mscli` (`startservice`, `stopservice`, `restartservice`,
`listservice`, `getservice`, `reloadservice`, `removeservice`), with definitions in
`/unitree/etc/master_service/service/` — only three exist: `ota_pipe`, `video_hub_pc4`,
`video_hub_pc4_chest`. `mscli` needs root. **[src]**

Side effect worth knowing: `/unitree/etc/master_service/cmd/am-init` is
`/usr/bin/amixer set Speaker 75%`, so the Jetson's boot-time speaker volume is set by
`master_service` — while it is dead, that has not been applied. **[src]**

---

## 4. The hands — **unresolved contradiction**

`ROBOT-INVENTORY.md` §4 records **Dex3-1** hands on RS485 behind an FTDI FT4232H. A
**`brainco_hand_server`** was found running, which implies **BrainCo** hands. Both are supported
by real evidence. **We are not picking a winner here**, because the wrong choice silently
mis-specifies every hand skill we build.

**But the question is now a multiple choice, and one zero-cost probe answers it.** See §4.0
for Unitree's own variant table, §4.2 for the inference we had to withdraw, and §4.3a for the
probe.

### 4.0 The vendor's G1-EDU variant → hand table

From `get_sdk` (vendor-updated 2026-07-20), verbatim apart from the vendor's own typos
("Andvanced", "DFQ" for the RH56DFX): **[web]**

| Variant | Configuration |
| ------- | ------------- |
| G1-EDU **Standard** | 23 DoF, **no hands** |
| G1-EDU **Advanced** | 29 DoF, **no hands** |
| G1-EDU **Ultimate A** | 29 DoF + 2 × **Dex3-1** three-finger hands (**no** tactile) |
| G1-EDU **Ultimate B** | 29 DoF + 2 × **Dex3-1** (**tactile included**) |
| G1-EDU **Ultimate C** | 29 DoF + 2 × **Inspire DFX** (RH56DFX) |
| G1-EDU **Ultimate D** | 29 DoF + 2 × **Inspire FTP** |

**BrainCo appears in no factory variant at all.** That reframes the whole argument: a running
`brainco_hand_server` is evidence of a **retrofit**, not of the factory build — which fits
perfectly with it living under `xr_teleoperate/vendor/`, a third-party stack a colleague
installed. The `brainco_hand` page (2026-03-25) reinforces the asymmetry: BrainCo is a
build-it-yourself GitHub service you `cmake` and launch with `sudo` and an explicit
`--serial`, whereas *"G1 internally provides a **resident service program** that communicates
with Dex3-1 and converts to DDS messages."*

Two more constraints narrow it further:

- **`mode_machine = 5` = 29 DoF** (`ROBOT-API.md` §4.2). **[live]** + **[web]** So this unit
  is **not** G1-EDU Standard — it is Advanced or one of the four Ultimates.
- `about_G1`'s maximum-configuration arithmetic is built on a **Dex3-1 pair**: 23 base + 2
  waist + 2×2 wrist = 29, then 29 + 2×7 = **43 DoF**. No 6-DoF hand (BrainCo Revo2 or either
  Inspire) reaches 43 — those give 41. **[web]** So if this unit's paperwork ever states 43
  DoF, that is a Dex3 pair. (Our OTA package name gives only `G1_Edu+`, no DoF count, and that
  string maps to **none** of Standard / Advanced / Ultimate A–D.)

### 4.1 The case for BrainCo — live process evidence

`brainco_hand_server` (pid 5923, started 01:43, ~8 % CPU sustained,
`--network_interface eth0`), from `…/xr_teleoperate/vendor/brainco_hand_service/bin/`, holding
`/dev/ttyUSB1` (fd 12 in `/proc/5923/fd`). Its log records the full probe and bind: **[live]**

```
Available Serial Ports: /dev/ttyUSB3, /dev/ttyUSB2, /dev/ttyUSB1, /dev/ttyUSB0
  left hand probe, all four ports:  "Failed to get device info"
  right hand probe, ttyUSB3/ttyUSB2: "Failed to get device info"
Hand hardware_type: 6      Hand sku_type: 1
Hand firmware_version: 1.0.22.U
Hand serial_number: BCXTR2124J2600024
right hand bound to /dev/ttyUSB1 port
Starting worker for right (slave 127)
```

`sku_type: 1` = `SKU_TYPE_MEDIUM_RIGHT` (`stark-sdk.h:154-162`). So: **exactly one hand
answered this probe** — right, medium, Modbus RTU slave `0x7f` at **460800 baud**, polled at
100 Hz. **No left hand answered on any port** — which, per §4.3, is not the same as "nothing
is attached there".

**Two vendor confirmations, and one thing they change.** `brainco_hand` documents the launch
as `sudo ./brainco_hand --id 126 --serial /dev/ttyUSB0 # 126: left hand, 127: right hand`
**[web]** — so our live log's "Starting worker for right (slave 127)" / slave `0x7f` reading
is exactly right. But note the second half: **the service is started by hand with an explicit
`--serial` per hand.** "No left hand answered" therefore reflects *how it was launched* as
much as what is plugged in — whoever started it chose one port.

Its interface is nothing like Dex3's: **[src]**

| Property   | Value                                                                            |
| ---------- | -------------------------------------------------------------------------------- |
| Topics     | `rt/brainco/{left,right}/cmd`, `rt/brainco/{left,right}/state`                     |
| Types      | `unitree_go::msg::dds_::MotorCmds_` / `MotorStates_` (bare sequences)              |
| Entries    | **6**, order `[Thumb, Thumb_aux, Index, Middle, Ring, Pinky]`                      |
| Cmd scale  | `positions[i] = clamp(q, 0, 1) × 1000`, `speeds[i] = clamp(dq, 0, 1) × 1000`       |
| State scale| `q = positions/1000`, `dq = speeds/1000`, `tau_est = currents/1000` (amps)         |
| Ignored    | `kp`, `kd`, `tau` — the fields exist in the message, the server does not read them |

Positions and speeds are **normalised to [0,1]**, not radians. Their README recommends setting
all finger speeds to 1.0.

**Unitree's own page confirms this line for line** — topics, the `[0,1]` normalisation, the
speed-1.0 recommendation, the 6-DoF count and the exact finger order
`[Thumb, Thumb_aux, Index, Middle, Ring, Pinky]`. **[web]** What it never states: the **DDS
message type**, the baud rate, or anything about force or tactile sensing. So the
`MotorCmds_`/`MotorStates_` typing and the ×1000 wire scaling stay **[src]**-only from the
server binary's source, and our 460800 baud / slave `0x7f` stay live-only. ⚠️ **And it never
says which end of [0,1] is *open*** — Inspire DFX explicitly maps 1.0 = open, `hand_sdk` says
positive tau closes, and BrainCo says nothing. **Do not write a BrainCo "open hand" preset
until that is read out of the server source or observed.**

### 4.2 The case for Dex3-1 — configuration and bus evidence

- The FT4232H is present and healthy: `0403:6011`, `bNumInterfaces = 4`, device serial
  `FTA9IWAI`, `ftdi_sio 1-2.2:1.0..1.3` → `/dev/ttyUSB0..3`, all four present as
  `crw-rw---- root:dialout 188,0..3`. That is exactly the bus a Dex3 **pair** would sit on. **[live]**
- `g1pilot` ships a URDF for *this* robot named `g1_29dof_dx3.urdf` — "dx3" = Dex3. **[src]**
- `xr_teleoperate`'s assets include `g1_body29_hand14.urdf` — 29 body DoF + 2 × 7 hand DoF, i.e.
  a **two-Dex3** configuration. **[src]**
- `xr_teleoperate` ships a working `Dex3_1_Controller` (100 Hz, `kp=1.5 kd=0.2`, XR retargeting
  through dex-retargeting DexPilot), and both SDKs ship `g1_dex3_example` binaries. **[src]**

But: a filesystem-wide search for any `*dex3*` artifact outside vendored SDK source trees
returns **nothing** — no Dex3 service binary, no systemd unit, no `/unitree/module` entry.
**[live]**

⚠️ **The conclusion we drew from that has to be withdrawn.** This section previously read
*"even if a Dex3 were plugged in, nothing on this Jetson would publish `rt/dex3/*/state` or
consume `rt/dex3/*/cmd`"*, and used it as evidence against Dex3. Unitree states plainly:
*"**Unitree does not deploy services on the NVIDIA Jetson Orin module.**"* **[web]**
(`about_G1`, 2026-05-06.) And the Dex3 driver is described as *"a **resident service program**"*
the robot provides itself — i.e. it runs on the control board at `192.168.123.161`, which has
no SSH. **Our search was aimed at the wrong host.** The observation stands; the inference does
not, and it was doing real work in the argument.

The same page also weakens the bus argument in the other direction: **no vendor page
associates the Dex3-1 with a PC2 USB-serial dongle.** The two pages that *do* describe
USB-serial dongles on PC2 are the **Inspire** ones, and BrainCo documents one USB-serial
device per hand. So the FT4232H's four ports fit "two retrofit third-party hands" at least as
well as "a Dex3 pair". **[web]**

### 4.3 Why the evidence we have cannot settle it

The BrainCo probe speaks **only** Modbus RTU at 460800 baud. A Dex3 or Inspire hand would not
answer that probe. So *"no left hand answered on any port"* is **not** evidence that nothing is
attached to the left wrist, and ttyUSB0/2/3 sitting idle is equally consistent with "nothing
there" and "something there that does not speak BrainCo".

**The single observation that settles it: look at the robot.** A Dex3-1 is a **7-DoF,
three-finger** hand (thumb ×3, index ×2, middle ×2). A BrainCo Revo2 is a **6-DoF, five-finger**
hand, per its own finger order. An Inspire DFX or FTP is **6 DoF, five-finger** too — so a
five-fingered hand narrows to {BrainCo, Inspire} rather than settling it. One glance at each
wrist plus §4.3a's passive subscribe resolves the whole section. Second best: trace which
FT4232H channel each physical wrist cable lands on.

**Do not settle it by opening the serial ports.** That means driving an RS485 bus attached to an
unknown device on a powered, standing robot.

### 4.3a The probe that settles it, and costs nothing

**Passively subscribe, for a few seconds, with zero writes:**

| Topic | Type | In our venv? | Proves |
| ----- | ---- | ------------ | ------ |
| `rt/dex3/left/state`, `rt/dex3/right/state` | `unitree_hg::msg::dds_::HandState_` | **yes** | Ultimate A/B — a Dex3 pair, resident service running on the control board |
| `rt/inspire/state` | `unitree_go::msg::dds_::MotorStates_` | **yes** | Ultimate C — Inspire DFX (12 entries, both hands) |
| `rt/inspire_hand/state/r` | `inspire::inspire_hand_state` | **no** — needs hand-written IDL | Ultimate D — Inspire FTP |
| `rt/brainco/{left,right}/state` | `unitree_go::msg::dds_::MotorStates_` | **yes** | The retrofit BrainCo, if its server is running |

The first two need no new types and no new dependencies. **This is now the highest-value
action in §4** — it also decides the newly-raised possibility that a Dex3 resident service is
running on PC1 where we cannot look (§4.2).

⚠️ **Settle §4 *before* the next locomotion window, not after.** Unitree's own warning:
*"During development, it is not recommended to perform overly intense actions, such as
**running or balance tests**, while the dexterous hand is attached"*, plus *"when using the
dexterous hand, always ensure that the robot's movements do not cause interference between the
hand and the main body"* and a separate warning against booting in the lying or squatting
positions with hands fitted. **[web]** "Running or balance tests" is *exactly* the class of
experiment queued up for `ROBOT-API.md` §11.

### 4.4 Dex3-1 reference, if that is what is fitted

All **[src]**, correct for the product Unitree ships, unreachable on this machine as configured.

| Direction | Topic                             | Type                                   |
| --------- | --------------------------------- | -------------------------------------- |
| Command   | `rt/dex3/{left,right}/cmd`         | `unitree_hg::msg::dds_::HandCmd_`      |
| State     | **`rt/dex3/{left,right}/state`**   | `unitree_hg::msg::dds_::HandState_`    |
| State (lf)| `rt/lf/dex3/{left,right}/state`    | same — decimated mirror                |

`rt/dex3/*/cmd` is unanimous across all three vendor clients, and both official prose pages
agree on both topics and both types. **[web]** The `lf` variant being a decimated mirror was
previously **[?]** inference from naming; **upgrade it** — Unitree's topic table applies the
identical "- low-frequency mode" convention to `lowstate`, `secondary_imu` and `odommodestate`
pairs, and to `rt/lf/dex3/right/state` explicitly. ⚠️ That table's Info column is scrambled for
the two *left*-hand rows (the lf/non-lf labels are swapped, and `rt/dex3/right/state` is
described as "left"), so read the convention, not the individual cells. **Subscribe the bare
name** — it is what both official prose pages use exclusively.

```
HandCmd_   : motor_cmd  sequence<MotorCmd_>   # 7 entries
             reserve    uint32[4]
HandState_ : motor_state        sequence<MotorState_>       # 7
             press_sensor_state sequence<PressSensorState_> # 9 (C++) / 7 (Python default)
             imu_state          IMUState_
             power_v, power_a, system_v, device_v  float32
             error   uint32[2]     reserve uint32[2]
```

**Our venv's IDL and the older `dexterous_hand` page (2025-02-10) match field for field.**
**[web]** The newer `basic_services_interface` (2025-10-21) publishes a *different, shorter*
`HandState_` with `press_sensor_state` and `imu_state` **swapped** and `system_v`, `device_v`
and `error[2]` missing entirely, and a `HandCmd_` without the `reserve[4]`. **Ignore it** — a
swapped struct in DDS does not error, it silently mis-decodes. General lesson for this doc
set: **recency is not a proxy for correctness on struct layouts.** Field meanings, newly
documented: `power_v`/`power_a` are the hand's total input supply voltage and current,
`system_v` its internal supply, `device_v` the output of its step-down module, `error` its
error code — i.e. **a fitted Dex3 gives us per-hand health telemetry we have no equivalent of
anywhere else.**

**Joint order — the contradiction is settled, and we now know what caused it.** Two official
pages give the identical IDL order for **both** hands: **[web]**

| IDL index | 0 | 1 | 2 | **3** | 4 | 5 | 6 |
| --------- | - | - | - | ----- | - | - | - |
| Joint | `thumb_0` | `thumb_1` | `thumb_2` | **`middle_0`** | `middle_1` | `index_0` | `index_1` |

So **slot 3 is `middle_0` on both hands**, which is what `hand_retargeting.py` said and what
`Dex3_1_Right_JointIndex` / `unitree_dex3.yml` contradicted. The trap that produced the
disagreement is visible in `dexterous_hand`'s per-side URDF cross-tables: **the left hand's
URDF link names run out of numeric order** — `left_hand_five` → IDL 3, `left_hand_six` → IDL
4, `left_hand_three` → IDL 5, `left_hand_four` → IDL 6 — while the right hand's run straight
through. **Anyone converting left-hand URDF indices to IDL indices by sorting names swaps
index and middle.** Adopt the IDL order above; keep one-slot-at-a-time verification as the
first thing done on real hardware, since the robot still wins.

**Units: radians, and this is the number-one mixing hazard across the four hand types.**
`MotorState_.q` is documented as *"joint feedback position (**rad**)"*, and the vendor's own
Dex3 example normalises to [0,1] **only for printing**, using joint limits. **[web]**

> **Dex3 = radians. BrainCo = [0,1]. Inspire DFX = [0,1] with 1.0 = open.** A
> `set_hand_pose(side, q[7])` written for Dex3 and pointed at a BrainCo topic sends 1.7 where
> the driver expects ≤ 1.0; the reverse sends 0.5 rad where 90° was meant.

(Note the vendor example's variable names `maxTorqueLimits`/`minTorqueLimits` are misleading —
they hold joint **position** limits, and they are per-side.)

Per-motor `mode` byte is bit-packed: `RIS_Mode_t { id:4, status:3, timeout:1 }`, i.e.
`mode = (id & 0x0F) | ((status & 0x07) << 4) | ((timeout & 0x01) << 7)` — **confirmed verbatim
by Unitree**, including `status` 0 = Lock, 1 = FOC and *"timeout: master→motor 0 = disable
timeout protection, 1 = enable (**default 1 s timeout**)"*. **[web]** So `timeout = 1` is a
**firmware-side 1 s deadman on the hand motors** — the same free-safety pattern as
`SetVelocity`'s `duration` and `rt/hand_sdk`'s auto-fallback. **Any hand skill we write should
set `timeout = 1` and re-publish, never `timeout = 0`.** Note also that the same
`unitree_hg::msg::dds_::MotorCmd_` struct carries a completely different `mode` encoding
depending on whether it rides in a `LowCmd_` (0 = Disable, 1 = Enable) or a `HandCmd_`.

⚠️ **TRAP: the `dexterous_hand` page splices the RS485 wire protocol into a DDS document.**
Under a heading reading "IDL Data Format" it shows a 20-byte packed struct with `head[2]`,
`CRC32`, and integer scale factors — *"`tor_des`: 256 represents 1 mNm; `pos_des`: 32768/2π
represents 1 rad; `k_pos`: 1280 represents 1 mN·m/rad"* — and a `uint16`-based tactile struct.
**None of that is the DDS type.** The real `MotorCmd_` is `{uint8 mode; float32 q, dq, tau, kp,
kd; uint32 reserve}`, all plain floats in SI, and the real `PressSensorState_` is
`{float32 pressure[12]; float32 temperature[12]; uint32 lost; uint32 reserve}` — as shipped in
our venv. Those scale factors describe what the **resident service** speaks to the hand over
RS485, downstream of DDS. Anyone implementing from that page will scale `q` by 32768/2π into
an integer field that does not exist and drive the hand to a wildly wrong position. (Internal
proof it is the wrong struct: the page's own stated sensor values, "valid when data ≥ 100000",
do not fit a `uint16`.)

**Use the URDF joint limits, not the example's hard-coded clamps** — the examples exceed the
URDF on `thumb_1` for both hands (left max 1.05 vs URDF 0.920; right min −1.05 vs −0.920). Left
joints 3–6 are negative-only and right joints 3–6 positive-only, so a shared "close the hand"
pose must be sign-flipped per side. **The spec sheet now explains the discrepancy**: `about_G1`
gives the thumb as `0°…+100°, −35°…+60°, −60°…+60°` and index/middle as `0°…+90°` and
`0°…+100°`. **[web]** The examples' symmetric ±1.05 is exactly the spec's +60° = 1.0472 rad —
but the spec range is **asymmetric** (−35°/+60°), so the examples are wrong on the negative
side regardless. **Keep our rule: use the URDF limits, they are the conservative ones.** In
radians the spec gives thumb_0 0…+1.745, thumb_1 −0.611…+1.047, thumb_2 −1.047…+1.047, and
index/middle 0…+1.571 and 0…+1.745 — though the page never says which of the two
index/middle ranges is the proximal `_0` and which the distal `_1`, so half that table is
still ambiguous.

Other spec-sheet figures: RS485 control interface, 7 DoF, **24 V rated** (the electrical table
says "operating voltage 12–58 V"), 9 array sensors, **tactile perception range 10 g – 2500 g**.
**[web]**

Tactile: `press_sensor_state` carries `pressure[12]` **and** `temperature[12]` float arrays per
pad plus a `lost` counter, 9 pads per C++ example (108 taxels/hand) vs 7 in the Python default.
The "no units, no calibration" gap is now **partly** filled: **[web]**

- **Value semantics** — *"valid values: data ≥ 100000; invalid / no value: data = 30000. It is
  recommended to scale down 100000 to 10.0000 for display and calculation."* So treat `30000`
  as no-reading, `≥ 100000` as valid, and divide by 10000 to display.
- **Geometry** — *"a 3×4 array sensor at each fingertip position (6 locations in total)"*,
  which conflicts with `about_G1`'s "number of array sensors: 9". Six fingertips plus three
  palm pads would reconcile them, but **no source says so**. Pad count remains contested.
- **The pad→finger map still does not exist in text.** It lives only in two images on the
  vendor page that the markdown conversion dropped
  (`doc-cdn.unitree.com/static/2024/12/26/…_5000x2812.png`). If tactile ever matters, fetch
  those two images.

### 4.5 The other two hand families, and `rt/hand_sdk`

Relevant mainly as **discriminators** for §4.3a, and as a warning about how differently these
four products behave.

**"Inspire hand" is two incompatible products in Unitree's own docs.** **[web]**

| | **Inspire DFX** (RH56DFX, Ultimate C) | **Inspire FTP** (Ultimate D) |
| --- | --- | --- |
| Topics | `rt/inspire/{cmd,state}` | `rt/inspire_hand/{ctrl,state,touch}/{r,l}` (`*` defaults to `r`) |
| Types | `unitree_go::msg::dds_::MotorCmds_` / `MotorStates_` | custom `inspire::inspire_hand_{ctrl,state,touch}` — **not in `unitree_sdk2py`**, would need hand-written IdlStructs |
| Entries | **12 — BOTH hands in one message, right occupies 0–5**, left 6–11 | **6 — one hand per topic suffix** |
| Joint order | `[pinky, ring, middle, index, thumb_bend, thumb_rotation]` | same |
| Control | **only `q` is meaningful**, everything else reserved | a 4-bit `mode` bitmask: 1 angle, 2 position, 4 force, 8 speed, combinable 0–15 |
| Polarity | **1.0 = open, 0.0 = closed** (`{"open", Ones()}, {"close", Zero()}`) | — |
| Transport | serial | Modbus RTU over RS-485 **and** Modbus TCP (left `192.168.123.210`, right `.211`, port 6000); *"only one device per bus at about 20 Hz"* on RS-485 |
| Tactile | — | **17 sensors**, `fingerone` = **pinky** … `fingerfive` = **thumb**, plus `palm_touch`. On RS-485 the touch topic **publishes nothing**, so silence there is not evidence of a non-tactile hand |

⚠️ **The DFX polarity is the opposite of the intuitive reading** and opposite in spirit to
`hand_sdk`'s "positive tau closes". If Inspire hands turn out to be what is fitted, a
wrong-polarity default would **slam the hand shut on power-up**.

⚠️ **The Inspire DFX page is H1-contaminated**: on a page filed under `G1_developer` it says
*"**H1** provides a USB to serial module, which can be plugged into the **H1** Development
Computing Unit (PC2) … usually named `/dev/ttyUSB1` (left hand), `/dev/ttyUSB2` (right
hand)"*, links to `H1_developer` pages, and its changelog reads *"Initial Release (**Reuse the
H1 routine**)"*. The DDS half is consistent with the `rt/inspire/*` naming used elsewhere and
is probably fine; **the ttyUSB1/ttyUSB2 assignment is an H1 claim and must not be used to
reason about our FT4232H port layout.** It also conflicts with the FTP page's own
`/dev/ttyUSB0` default on the G1. **[web]**

**`rt/hand_sdk` is not a generic hand interface.** *"The Hand SDK is an external control
interface provided by the `ai_sport` module … By publishing to `rt/hand_sdk`, a user process
can inject commands for the 4 hand motors into `ai_sport`"* — scoped explicitly to the
**Dex2-5 five-finger 2-DoF hand and the Dex1-1 parallel gripper**, *neither of which appears
in the G1-EDU variant table* (§4.0). **[web]** It is an `ai_sport` blending injection,
categorically different from the per-hand driver topics above, which talk to a serial driver.
`unitree_go::msg::dds_::MotorCmds_`, 4 motors,
`Motor_real = weight × Hand_SDK + (1 − weight) × G1_Cmd` with all five of `kp/kd/q/dq/tau`
participating. Three details worth carrying:

- ⚠️ **Weight encoding trap:** `weight` is an **integer 0–100 stored in `cmds[0].mode`**
  (`weight = cmds[0].mode / 100.0`); the other three motors' `mode` is unused. That is
  structurally the same idea as `rt/arm_sdk`'s `motor_cmd[29].q` blend weight but a
  **different encoding in a different place** — the two are not interchangeable.
- **Preconditions:** `ai_sport` running, robot **not** in damping, a compatible hand
  installed. *"While the robot is in the damping state, user commands are not forwarded … after
  leaving damping the user must re-set `weight` explicitly."* So on 2026-08-14, with no
  controller loaded, this path would have been **inert** — a poor diagnostic and a bad first
  hand experiment.
- **Auto-fallback on timeout**: stop publishing and `ai_sport` resumes its default behaviour,
  no cancel call needed. Third instance of the firmware-deadman pattern (§4.4, `SetVelocity`).
  Also: *"ramp `weight` smoothly"* — stepping 0 → 1 can cause abrupt motion.

Our Isaac sim profile's `rt/dex1/{left,right}/{cmd,state}` with `MotorCmds_` is the **Dex1-1
gripper**, i.e. the same family `hand_sdk` names.

### 4.6 Corrections our repo needs regardless of which hand is fitted

- `apps/bridge/src/bridge/sdk/g1_protocol.py:97-98` has
  `dex_left_cmd="rt/api/dex3/left/request"` / `dex_right_cmd=…`. **The hands are not an RPC
  service.** There is no api_id, no JSON envelope, and no `rt/api/dex3/*` topic in any vendor
  source on this robot — and none in any of the six official hand-related pages either. It is a
  raw `HandCmd_` publish on `rt/dex3/{side}/cmd`. **[src]** + **[web]**
- `SPEC.md` §17.5 gives the state type as `MotorStates_`. For Dex3 it is **`HandState_`**.
  (`MotorStates_` is right for BrainCo, Dex1 grippers and Inspire **DFX** — which is probably
  where the confusion came from. Inspire **FTP** is neither; it uses custom `inspire::*`
  types.) **[src]** + **[web]**
- **`/api/dex3_msg_controller`**, cited in `ROBOT-INVENTORY.md` §4 and `MENTAL-MODEL.md`, appears
  in **no** vendor source, binary or config anywhere on this robot. Its only occurrences are our
  own doc files. Treat it as unsourced; strike it unless someone can point at the observation it
  came from. **[live]**
- **`ROBOT-API.md` §6.8's "9 pressure pads"** should read `pressure[12]` per pad array; the pad
  *count* is contested 6 vs 9 (§4.4).
- **Any hand skill must carry the units per hand type** — Dex3 radians, BrainCo [0,1], Inspire
  DFX [0,1] with 1.0 = open — and set the Dex3 `timeout` bit so the firmware deadman applies
  (§4.4).

---

## 5. Audio — mic array, speaker, and whether TTS exists

### 5.1 None of it is on the Jetson

Confirmed four independent ways: **[live]**

- `/proc/asound/cards` → exactly two: `0 [HDA]` (NVIDIA Orin NX HDA) and `1 [APE]` (Tegra APE).
- `aplay -l` → card 0 devices 3/7/8/9 = **HDMI only**, no analog out. `arecord -l` → only the
  APE `tegra-dlink-N XBAR-ADMAIFn` endpoints, which are the AHUB's internal DMA endpoints, not a
  physical capture path.
- `amixer -c 1 controls` → 1535 controls, all generic SoC blocks (DSPK, ADX, AMX, SFC, MVC,
  DMIC, I2S), with **no external codec name anywhere**.
- `lsusb` → no USB-audio-class device. PulseAudio → one sink, **zero real sources**.

`ROBOT-INVENTORY.md` §4's "Jetson Orin NX APE (capture), HDA/HDMI (playback)" is accurate but
describes SoC plumbing **with nothing wired to it**. The G1's 4-mic array and speaker belong to
the control board at `192.168.123.161` and are reached two ways.

### 5.2 Path 1 — raw mic over UDP multicast (not DDS)

Unitree's own C++ SDK example (`unitree_sdk2/example/g1/audio/g1_audio_client_example.cpp`)
hardcodes it, which upgrades this from community lore to **[src]**:

```c
#define GROUP_IP  "239.168.123.161"
#define PORT      5555
#define WAV_LEN_ONCE (16000 * 2 * 160 / 1000)   // 5120 B = 160 ms
```

Format is **16 kHz, mono, signed 16-bit LE PCM, one pre-mixed channel**. Four mics physically,
one channel on the wire — beamforming and AEC happen on the control board and we get no
per-element or DOA access **[web]**.

**The interface pin is load-bearing.** The vendor example walks `getifaddrs` for a
`192.168.123.*` address and sets `mreq.imr_interface` to it. Joining on `INADDR_ANY` lets the
kernel's default route pick `wlan0` or `docker0`, and you get **zero packets, silently**.

**This is invisible to our DDS config, by design.** `apps/bridge/src/bridge/sdk/connection.py`
writes `<AllowMulticast>false</AllowMulticast>` plus a single unicast
`<Peer address="192.168.123.161"/>`. That is fine for the `voice` RPC service — the peer is
exactly the control board that hosts it — and **irrelevant** to the mic, which is a plain UDP
socket. A future `listen()` skill must open its own socket and `IP_ADD_MEMBERSHIP` with
`imr_interface` = eth0's address (`192.168.123.164`), independently of CycloneDDS. **[src]**

At the snapshot the group **was** joined on eth0: `/proc/net/igmp` shows `A17BA8EF`
(= 239.168.123.161) with 1 user, `/proc/net/dev_mcast` shows the derived MAC `01005e287ba1`, and
`ss -ulnp` shows `0.0.0.0:5555` held by pid 2239 = `gemm-ai.service`. The DDS group
`239.255.0.1` is present at the same time with 6 users — two different groups, both live. **[live]**

**Joined ≠ flowing.** Whether packets are actually arriving was *not* established, and proving
it means recording whoever is standing next to the robot — that needs explicit consent, not a
command.

### 5.3 Path 2 — the `voice` DDS RPC service

Service name is literally **`voice`** (not `audio`, not `vui`), api version `1.0.0.0`, topics
`rt/api/voice/request` / `…/response`. Constants and parameter shapes are identical in the
firmware-matched `unitree_ros2` header, the newer C++ SDK, and the Python SDK: **[src]**

| api_id | Call           | Parameter                                                       |
| ------ | -------------- | --------------------------------------------------------------- |
| 1001   | `TTS`          | `{"index": uint, "text": "<utf8>", "speaker_id": 0\|1}`          |
| 1002   | `ASR`          | registered by every client, **called by none** — purpose unknown |
| 1003   | `START_PLAY`   | `{"app_name": "…", "stream_id": "…"}` **plus raw PCM in `binary`** |
| 1004   | `STOP_PLAY`    | `{"app_name": "…"}`                                              |
| 1005   | `GET_VOLUME`   | empty → `{"volume": uint8}` — **range 0–100**                    |
| 1006   | `SET_VOLUME`   | `{"volume": uint8}` — **clamp to 0–100**                         |
| 1010   | `SET_RGB_LED`  | `{"R": uint8, "G": uint8, "B": uint8}` — 0–255 each, **≥ 200 ms between calls** |

Only one service-specific error code is declared: **100 "Invalid parameter"**, and the official
docs add none.

PCM for `START_PLAY` must be **16 kHz mono 16-bit** — both vendor examples hard-reject anything
else, and Unitree independently warns *"the device only supports mono audio; stereo may cause
playback issues"*. **[web]** Our "96000 bytes (3 s) with roughly 1 s spacing" is an
**on-robot-example convention, not a protocol requirement**: the official example passes an
entire ~5 s WAV in a single `PlayStream` call.

**Four things the official `AudioClient` documentation adds** (full treatment in
`ROBOT-API.md` §7): **[web]**

- **`stream_id` is the interrupt model** — *"the same ID means continuous playback from cache,
  different IDs mean interrupting the current playback."* Barge-in is free: send the next
  utterance with a new id, no `PlayStop` needed.
- **Volume is 0–100**, and `LedControl` has a hard **200 ms minimum call interval**.
- **TTS is local/offline with exactly two roles**, and **"mixed Chinese and English modes are
  not supported"** — split bilingual strings or use `PlayStream`.
- **`PlayStop` takes `app_name`** — the official example passes `"example"`, the same string it
  gave `PlayStream` as app name. Three of four sources now agree; the on-robot C++ example
  passing `stream_id` is simply wrong.
- **Version floor: `Vui_Service ≥ 2.0.3.8`, `Vui Module ≥ 2.0.0.3`.** We have never obtained
  either version — they live on the control board — so `GET_VOLUME` returning a value versus a
  `3203` is the only probe we have for whether this unit serves the calls at all.

⚠️ **The LED strip is safety-relevant, not decorative.** It has **four uncoordinated writers**:
the motion FSM's own state colours (solid blue = normal, orange = damping, green = seated,
**yellow = debug mode**, purple = zero torque, dark blue = standby, **red = error state**), the
voice assistant (breathes blue on hearing, green on receiving an instruction), `SET_RGB_LED`,
and us. **[web]** Driving it overwrites the operator's only indicator that the robot is in an
error state or debug mode, and nothing documents how to hand the strip back to the FSM. Either
do not expose LED control to the LLM, or expose it only as a short flash that restores
afterwards. **And ask the operator to record the strip colour at the top of every window** — it
is a free, instant, zero-RPC readout of the robot's mode, and it retroactively disambiguates
every session we have logged.

`/api/audiohub` does **not** exist on this robot: a grep across `/home`, `/unitree`, `/opt` and
`/etc` hits exactly two files, both our *own* docs synced onto the robot. **[live]** `/api/vui`
exists only as a **Go2** client (service `vui` v1.0.0.1: 1001 SetSwitch … 1006 GetBrightness) —
note `1001` is `SetSwitch` on `vui` but `TTS` on `voice`, the same per-service api_id collision
`ROBOT-INVENTORY.md` §3 records for sport/arm. There is no `/unitree/module/vui_service` on this
Jetson, and `strings` on `master_service` yields no `audiohub`/`vui`/`voice`/`audio_msg` hits —
so `G1-WEB-RESEARCH.md` §4.4's arXiv-sourced `vui_service` claim does not hold **for this host**.
It says nothing about the control board, which we cannot inspect. **[live]**

**Three different things carry the letters "vui", and conflating them will waste an afternoon:**
**[web]**

| Name | What it is |
| ---- | ---------- |
| `vui` | a **Go2-only RPC service** (`rt/api/vui/*`), no G1 counterpart — the finding above stands |
| `vui_service` | the **`robot_state` process name** for the *"Audio and Lighting Control Service"*, i.e. what actually provides TTS, `PlayStream`, volume and the LED strip |
| *"VuiClient Service Interface"* | the **title of the official page that documents `AudioClient`** — it defines no VuiClient at all |

None of them is the DDS RPC service `voice`. And `/api/audiohub`'s name is no longer a mystery
either: **"Audio Hub" is a real firmware component** (≥ 1.0.1.0) named alongside Vui Service and
Webrtc Bridge in the app-side playback docs — an app/WebRTC-side component, **not** a DDS RPC
service. Keep the "does not exist on this robot" finding; stop calling the name invented.

### 5.4 `rt/audio_msg` — ASR text, never audio

Type is **`std_msgs::msg::dds_::String_`** (shipped by `unitree_sdk2py`). The vendor example
subscribes it and prints `resMsg->data()`; the only consumer on this robot does
`json.loads(msg.data)` then `payload.get("text")`, and **silently drops anything that is not
valid JSON**. So the payload is JSON with a `text` key. `unitree_go::msg::dds_::AudioData_`
exists in our IDL but is a Go2 type and is not on this path. **[src]**

**The full schema is now published, and it carries two things we should be using.** **[web]**
Fields: `index`, `timestamp`, `text`, **`angle` (azimuth of the speaker, 0–180)**,
`speaker_id`, `sense` (emotion), `confidence`, `language`, `is_final`. And a **second payload
shape on the same topic**: `{"play_state": 0|1}` — `0` = playback stopped, `1` = started. That
upgrades the unitree-ui `play_state` claim from **[web]**-third-party to vendor-documented.

- **`angle` is free sound direction-of-arrival**, on a topic we already know how to read.
- **`play_state` is a real playback start/stop event** — the audio equivalent of
  `rt/arm/action/state`, giving `say()` true completion detection instead of a guessed duration.
- ⚠️ **Trap: `speaker_id` means two different things on one service.** Here it is the ASR's
  *speaker recognition (diarization)* result; in `TtsMaker` it is the *voice role* (0 = Chinese,
  1 = English). Same key, unrelated meanings.

⚠️ **ASR output is gated on a mode we cannot set over DDS.** *"When the robot's microphone is
turned on (**switch to the wake-up mode in the APP or remote control**), the built-in microphone
+ ASR module will recognize the human voice."* **[web]** Modes switch with **L1+L2** on the
remote or in the App under 【Device】→【Data】→【Audio】→【Voice assistant】; the wake word is
*"Hello Robot"*; **L2+Select** wakes, **L1+Select** force-interrupts; the dialogue ends after
15 s of silence. So a future `listen()` built on `rt/audio_msg` has a **human prerequisite**.
Whether the raw multicast feed (§5.2) is gated the same way is **unknown and worth testing** —
if it is always live, our own STT is independent of the vendor assistant entirely.

**The embedded ASR is unusable for us.** A colleague code comment dated 2026-08-06 — after this
robot arrived — records it live-verified **transliterating non-Japanese speech into unrelated
kana**: *"Hola Darío"* comes back as `オラオラがるよ…`, so exact phrase matching never fires
regardless of the configured phrase. Near-silence emits lone punctuation. This is second-hand
(a third party's code comment asserting a live observation), but it is dated after hardware
access and it is why `gemm-ai` abandoned `rt/audio_msg` and runs Whisper continuously over the
raw multicast instead. **[src]**

### 5.5 Yes, on-robot TTS exists — and exactly what would drive it

**`say` is a stub** (`apps/bridge/src/bridge/mcp_server.py:547-572` logs and returns
`{"stub": True}`). `SPEC.md` §17.4 assumed a WebRTC/Cartesia path; that is **no longer
necessary** — synthesis can be entirely on-robot for Chinese and English, or Cartesia → PCM16
@16 kHz → `PlayStream` for anything else.

`speaker_id` is `0` = Chinese, `1` = English. **Two voices only, and neither reads Spanish
intelligibly** — verified on-robot by the colleague, which is why they built an
MP3→PCM16 gTTS path that goes out through `PlayStream`. That same file's note about a PyAV
frame-padding bug *"heard on-robot as crackle/noise and stuck playback"* is second-hand evidence
that **`PlayStream` genuinely drives this robot's speaker**. **[src]**

**This used to be blocked on our SDK pin. It is not any more.** The historical problem:
`direct_url.json` pinned commit `a7dff75`, whose `unitree_sdk2py/g1/`, `g1/arm/`, `g1/audio/`
and `g1/loco/` contain **no `__init__.py`**, while `setup.py` uses `find_packages()`, which
only discovers packages that have one — so the venv shipped only `core go2 idl rpc utils` and
`from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient` raised `ImportError`. Same
root cause as the missing `b2` import that `scripts/postsync.sh` patches. **[live]**

⚠️ **Re-checked while folding in the official docs: `apps/bridge/.venv` is now pinned to
`65691c8` and ships `a2 as2 b2 comm core g1 go2 h1 h2 idl rpc utils`**, including
`unitree_sdk2py/g1/{arm,audio,loco}` with `__init__.py` present. **[live]** So `AudioClient`
imports today, and `unitree_sdk2py.b2.robot_state` (api version `1.0.0.2`, with api_ids 1004/
1005/1006 the go2 client lacks) is available too — see `ROBOT-API.md` §8. The paragraph above
is kept as the explanation of *why* `a7dff75` behaved that way, not as a current state.

Two ways to call it, and **the second still needs no `g1` package**:

1. Use the now-present `AudioClient` directly.
2. Do what `g1_rpc.py` already does for `sport` and `arm` — register the voice api_ids on our
   existing `_G1Client`:
   - `_G1Client("voice", (1001, 1003, 1004, 1005, 1006, 1010), timeout_s=…)`
   - `say(text)` → `call_raw(1001, json.dumps({"index": n, "text": text, "speaker_id": 1}))`
   - PCM playback needs the binary variant, which `call_raw` does not cover: add a method calling
     the **already inherited** `self._CallRequestWithParamAndBin(1003, json.dumps({"app_name":
     "c3po", "stream_id": str(ms)}), list(pcm_bytes))`, then `call_raw(1004,
     json.dumps({"app_name": "c3po"}))` to stop.

Nothing in the missing `g1` package is actually required — `rpc/client.py:59` already provides
`_CallRequestWithParamAndBin`. **[src]**

**Two vendor defects to carry over deliberately:**

- The vendored `g1_audio_client.py` does `self.tts_index += self.tts_index`, so the TTS `index`
  is **permanently 0**. The A2 copy of the same file has the correct `+= 1`. If the firmware
  dedupes on index, repeated utterances silently fail to play. Test with two different texts
  back to back. **[src]**
- `PlayStop` takes **`app_name`** — the header, the Python client and the JSON key all agree —
  but the vendor C++ example passes `stream_id`. Follow the header. **[src]**

**Ack semantics are probably immediate, unlike the arm service.** Every vendor example *sleeps*
after TTS (`Sleep(5)`, `Sleep(8)`, `sleep_for(10s)`) and paces `PlayStream` with `Sleep(1)`
between 3-second chunks rather than relying on the call blocking. That strongly suggests `voice`
acks on receipt, not on completion — the opposite of the arm service, which
`ROBOT-INVENTORY.md` §3 records acking after 4.19 s. **Not proven.** Practical consequence:
`ARM_TIMEOUT_S`-style headroom is probably unnecessary here, but a `say()` that must know when
speech *ended* needs its own duration model. **[?]**

### 5.6 Sharing the speaker

`gemm-ai.service` is a **writer** on the `voice` service — `RobotEmbeddedTTS` → `TtsMaker`, plus
`PlayStream`/`PlayStop` keyed on `APP_NAME = "gemm-ai"`. This **corrects `ROBOT-INVENTORY.md`
§5**, which says it *"only subscribes, to `rt/audio_msg`"*: in real mode it does not use
`rt/audio_msg` at all — that is its mock/wake path — it joins the raw mic multicast, runs local
Whisper, and speaks. It is still not a motion risk. **[live]**

Multicast means our mic reads will never conflict with theirs. **The speaker will.** Two stacks
calling `TtsMaker`/`PlayStream` interleave, and `PlayStop` is keyed by `app_name`, so ours must
use its own (`"c3po"`) and **cannot stop theirs** — now confirmed by the vendor, which
documents `PlayStop(app_name)` and scopes stopping per application. **[web]** Whether
`PlayStream` mixes with or preempts a concurrent `TtsMaker` is still unknown; queue behaviour
is defined **only** for `PlayStream` (via `stream_id`) and nowhere for `TtsMaker`, so prefer
`PlayStream` for anything that must be interruptible.

**And there is a third competitor: Unitree's own voice assistant.** It wants the same speaker,
the same mic and the same LED strip, with no arbitration — and **there is no documented way to
disable it in software.** The only documented off-switches are the remote (L1+L2 to change
mode, **L1+Select to force-interrupt**) and the App; the vendor's entire "close the
interaction" section is one sentence with no mechanism. **[web]** It needs the Internet for its
GPT path (firmware ≥ 1.3.0), so on an air-gapped `192.168.123.x` robot it degrades to an
offline *"Hello, I am here"* — which is the quietest practical state and probably the one we
are already in. **Do not plan on disabling it programmatically.**

⚠️ **Never switch `vui_service` off to silence it.** That is the `robot_state` service name for
*"Audio and Lighting Control Service"* — one service providing TTS, `PlayStream`, volume **and**
the light strip. **[web]** Turning it off would silence us as well.

And the privacy fact, stated plainly: while `gemm-ai.service` runs, **the mic is always on and
everything said near this robot goes into a Whisper transcript**. `stop_gemm` does not stop it
(§7).

### 5.7 Wake word, VAD and what our venv lacks

No firmware wake word is exposed through any API; api 1002 (`ASR`) is registered by every vendor
client and called by none. On disk: **Silero VAD v5** (bundled with faster-whisper 1.1.0,
onnxruntime 1.19.2) is the only real VAD model; openWakeWord is **not** installed and
`GEMM_WAKEWORD_MODEL_PATH` is empty; no Porcupine, `.ppn` or `.tflite` wakeword models exist
anywhere. The VAD actually in use is a hand-rolled RMS gate (threshold 500.0, 1.2 s silence
tail, 8 s max utterance). STT cached on-robot is `Systran/faster-whisper-base` (142 MB, dated
2026-08-06 — so the mic→Whisper path was genuinely exercised on hardware). **[live]**

Our bridge venv has **none** of this: no whisper, no onnxruntime, no VAD, no PyAV, no
sounddevice/pyaudio. `ffmpeg` and `sox` are not installed system-wide either (`aplay`, `arecord`
and `amixer` are). **[live]**

The RGB LED (api 1010) has **no documented physical location** in any source. Finding it needs
eyes.

---

## 6. IMU, battery and wireless controller

### 6.1 IMUs — there are three, and they are not interchangeable

The body/pelvis IMU rides **inside** `LowState_` on `rt/lf/lowstate` (~20 Hz measured). Layout of
`unitree_hg::msg::dds_::IMUState_`: **[src]**

```
float32[4] quaternion     # w,x,y,z — terminations.hpp builds Eigen::Quaternionf(q[0],q[1],q[2],q[3])
float32[3] gyroscope      # rad/s, raw
float32[3] accelerometer  # m/s^2, raw
float32[3] rpy            # ZYX Euler, body frame; rpy[2] is yaw
int16      temperature    # NOTE: int16 here; unitree_go's IMUState uses int8 — different wire size
```

The quaternion's **w,x,y,z** order is now vendor-stated rather than inferred — the official IDL
comment reads `// Quaternion QwQxQyQz`. **[web]**

A **second IMU** is published on `rt/secondary_imu`, same `IMUState_` type, subscribed by vendor
G1 low-level examples as the **torso** IMU. **Both spellings are real:** Unitree's own topic
table lists `rt/secondary_imu` **and** `rt/lf/secondary_imu` as a high-rate / low-frequency
pair, the same convention it applies to `lowstate`, `odommodestate` and `dex3/*/state`.
**[web]** So this is not an either/or, and the earlier "only one can be right" framing is
withdrawn. Presence on *this* firmware remains unverified. **[src]**

The three IMUs on this robot:

| IMU              | Where it appears                                        | Catch                                                         |
| ---------------- | -------------------------------------------------------- | ------------------------------------------------------------- |
| G1 body          | `LowState_.imu_state`, `rt/secondary_imu`                | `unitree_hg/IMUState`, *"que ningún SLAM estándar entiende"*   |
| Livox ICM40609   | `/livox/imu` or `rt/utlidar/imu_livox_mid360`, 200 Hz     | inverted mount, gravity sign flip (§1.7); `Imu_` not in our IDL|
| D435i BMI055     | IIO `accel_3d` / `gyro_3d`                                | not factory-calibrated; extrinsic to depth fixed **[web]**     |

Consequence for any LIO we build: **it must use the LiDAR's IMU or the RealSense's, not the
G1's.**

### 6.2 Battery — solved, and never actually read

**The G1's `LowState_` carries no battery field at all.** Nine fields, in wire order: **[src]**

```
uint32[2] version; uint8 mode_pr; uint8 mode_machine; uint32 tick;
IMUState imu_state; MotorState[35] motor_state; uint8[40] wireless_remote;
uint32[4] reserve; uint32 crc
```

No `bms_state`, no `power_v`/`power_a`, no `foot_force` — all of which **do** exist in
`unitree_go/LowState.msg`, the quadruped type. **That is why `battery_pct` reads null: the field
a Go2 code path would use does not exist in the humanoid message.**

State of charge is on its own topic, and it is confirmed observed on **this** robot: two rosbag2
recordings list `/lf/bmsstate`, type `unitree_hg/msg/BmsState`, at 580 msgs / 28.97 s = **20.02
Hz** (2026-08-11) and 302 / 15.07 s = 20.04 Hz (2026-08-13). **[live]** Our
`g1_protocol.REAL_TOPICS.bmsstate` already reads `"rt/lf/bmsstate"` — previously **[web]**-only
from the unitree-ui reverse engineering; the bags upgrade it to observed.

```
uint8 version_high, version_low, fn;  uint16[40] cell_vol;  uint32[3] bmsvoltage;
int32 current;  uint8 soc;  uint8 soh;  int16[12] temperature;
uint16 cycle;  uint16 manufacturer_date;  uint32[5] bmsstate;  uint32[3] reserve
```

**`soc` is 0–100 percent**, not 0–255 — the vendor's own predicate compares it directly:
`low_battery(bms_state, limit_soc = 20.0)` in `g1/common/terminations.hpp`. The type is already
shipped by `unitree_sdk2py`, so wiring this needs **no hand-written IDL**:
`from unitree_sdk2py.idl.unitree_hg.msg.dds_ import BmsState_` and a subscriber on
`rt/lf/bmsstate`. **[src]**

⚠️ **"faults: none, battery: null" in `ROBOT-INVENTORY.md` §6 is not evidence of a healthy
pack.** `state.py` hardcodes `battery_pct: None` in both return paths, and the only value ever
appended to `faults` is `stale_lowstate_<n>s`. We have never looked at the battery, so a low-SOC
or thermal guard has never been excluded as an explanation for anything.

Unconfirmed: whether `current` is mA or 10 mA, whether `bmsvoltage[3]` is mV, and whether all 40
`cell_vol` / 12 `temperature` entries are populated on this pack. One decoded message settles all
three. Do **not** confuse `AgvBmsState_.battery_percentage` (a wheeled-base accessory type, C++
SDK only) with `BmsState_.soc`.

**Both halves of the location finding are vendor-confirmed — and the open half is confirmed
unanswerable by reading.** The `rt/lf/bmsstate` → `unitree_hg BmsState_` pair appears in
Unitree's own G1 topic table, and the absence of battery from `LowState_` is visible in their
own struct listing. **[web]** But **no official page documents the `BmsState_` fields at all**:
a grep across all 45 returns exactly that one topic-table row, and `BmsState_` is missing from
the page that enumerates the `unitree_hg` structures. So **stop hoping a doc will answer the
units question** — it needs one decoded live message, and that should be a first-class goal of
the next window. Note also there is no high-frequency `rt/bmsstate` twin in the vendor table:
`rt/lf/bmsstate` is the only battery topic they list.

One indirect corroboration on units, not authoritative: the vendor SLAM service's `rt/slam_info`
JSON states `batteryAmp` in **mA**, `batteryVol` in **mV**, `batteryPower` in **percent**, and
`batteryTemp`/`motorTemp[]` in **°C**. **[web]** It is that service's own reporting rather than
raw `BmsState_` fields, and it only publishes while `unitree_slam` runs — so it makes those
units plausible, it does not settle the struct's scaling.

### 6.3 Wireless controller — two representations, one of them free

**(a) `rt/wirelesscontroller`**, `unitree_go::msg::dds_::WirelessController_`, five fields:
`float32 lx, ly, rx, ry; uint16 keys`. Whether the G1 publishes this independently is
**unverified** — only a Go2 example uses it. **[src]** ⚠️ **And it appears in ZERO of the 45
official G1 pages**, nor in Unitree's own G1 topic table, while the `LowState_` path is
documented **twice**. **[web]** That is positive evidence of absence: treat it as Go2-only
unless a live `DCPSPublication` scan proves otherwise, which makes **(b) the only
vendor-documented route to remote state on the G1**, not merely the cheaper one.

**(b) `LowState_.wireless_remote`, `uint8[40]`** — the raw controller packet, present in both
`unitree_hg` and `unitree_go` LowState. Every vendor example `memcpy`s it and reinterprets it as: **[src]**

```c
typedef struct { uint8_t head[2]; BtnUnion btn; float lx, rx, ry, L2, ly; uint8_t idle[16]; }
        xRockerBtnDataStruct;
```

⚠️ **The axis order inside the packet is `lx, rx, ry, L2, ly` — not the `lx/ly/rx/ry` order of
the DDS message.** Getting this wrong silently swaps axes. Unitree's `remote_control_data` page
reproduces the identical struct and identical bit order, so this is now **doubly sourced** and
should be stated as fact. **[web]** Two additions from it: joystick range is **[−1.0, 1.0]**,
and the `L2` float is an **analog axis distinct from the L2 button bit**. One flag: that page's
decode snippet types the message as the **Go2** `LowState_`, whose offsets differ — copy the
struct definitions from the page and the accessor from our own `unitree_hg` IDL.

Key bits (bit 0 → bit 15), identical in both representations:

| Bit  | 0    | 1    | 2      | 3      | 4    | 5    | 6    | 7    | 8    | 9    | 10   | 11   | 12   | 13    | 14    | 15   |
| ---- | ---- | ---- | ------ | ------ | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ----- | ----- | ---- |
| Key  | R1   | L1   | start  | select | R2   | L2   | F1   | F2   | A    | B    | X    | Y    | up   | right | down  | left |
| Mask |0x0001|0x0002| 0x0004 | 0x0008 |0x0010|0x0020|0x0040|0x0080|0x0100|0x0200|0x0400|0x0800|0x1000|0x2000 |0x4000 |0x8000|

**On the G1 the remote is free** — it rides in the LowState we already subscribe to, so it needs
no extra subscription and no extra type. Our `state.py::_on_lowstate` currently **discards** the
field (it keeps only tick/mode_machine/motor_count/has_imu). Decoding it would turn "the remote
didn't work" from an anecdote into a measurement: expect `head == {0xFE, 0xEF}` and `btn`
changing as buttons are pressed. The SDK also treats an all-zero 40-byte block for **3000 ms** as
`isJoystickTimeout_` — a ready-made "controller is absent" predicate to copy. **[src]**

**Which combinations the firmware intercepts is still undocumented — but the combinations
themselves now are.** Unitree publishes the full key set and maps it to its own ①…⑧ symbols,
never to FSM ids, so the interception question survives intact. The full table, the LED colour
map, and the canonical bring-up chains are in `ROBOT-API.md` §4.4–4.5; the four operationally
critical items: **[web]**

1. **L2 + B is the emergency stop** — *"G1 goes into damped mode, which will losing balance and
   falling down"* — and it **works even inside debug mode**.
2. **R1 + X** is Main Operation Control for a **1-DoF waist**; **R1 + Y** is the **3-DoF-waist**
   equivalent, *"recommended"* for this chassis. This is central to `ROBOT-API.md` §11.
3. **"When in the standing position, certain button combinations need to be `held for two
   seconds` to take effect."** A tap does nothing. This alone may explain "the remote also
   failed".
4. **L2 + R2 enters debug mode**, only from zero-torque or damping; **L2 + A** confirms it with
   a diagnostic pose; the LED goes **solid yellow**.

What was already established stands: the controller's bytes reach us both decoded and raw, so
any interception happens upstream of us, invisibly. Our `faults.py` carries source **1000 =
"Emergency Stop"** — and Unitree's total-device status word now gives a concrete counterpart,
bit **`0x1000` "soft emergency stop switch is pressed"** (`ROBOT-API.md` §9.6), alongside a
**solid red "Error State"** LED. **Do not guess the reserved combos.** **[src]** + **[web]**

---

## 7. Exclusivity and sharing

### 7.1 Who arbitrates what

| Resource                            | Arbitrated by                | Owners allowed        | What collision looks like                              |
| ----------------------------------- | ---------------------------- | --------------------- | ------------------------------------------------------ |
| `/dev/video0..5`                    | kernel V4L2                  | one **per node**      | `xioctl(VIDIOC_S_FMT) failed … Device or resource busy` |
| `/dev/ttyUSB0..3` (hands)           | kernel tty                   | one per port          | open fails / `Failed to get device info`               |
| D435i IMU (IIO)                     | IIO, separate from V4L2      | does not contend      | —                                                       |
| **Livox Mid-360**                   | **the sensor itself**        | **one host, globally**| the previous owner just goes silent                    |
| Mic multicast `239.168.123.161:5555`| nothing — IP multicast       | unlimited readers     | —                                                       |
| Speaker (`voice` service)           | nothing                      | unlimited writers     | utterances interleave — **three writers**: us, `gemm-ai`, the vendor voice assistant |
| **LED strip**                       | **nothing**                  | unlimited writers     | our colour hides the FSM's own state colour (§5.3)      |
| DDS topics                          | nothing                      | unlimited             | —                                                       |
| **LiDAR over DDS** (`rt/utlidar/*`) | nothing — ordinary pub/sub   | **unlimited**         | — (contrast the raw Livox path above) **[web]**          |
| **Robot control API** (`sport` 7105)| **nothing**                  | must be one, by agreement | two controllers, one set of legs                    |
| **Vendor navigation** (`slam_operate` 1102) | **nothing**          | must be one, by agreement | it runs its own PID emitting vx/vy/vyaw — a second commander **[web]** |

The asymmetry that matters: **the LiDAR's exclusivity is enforced by the device, not by the OS,
and it persists.** Killing the process that grabbed it does not hand it back — the destination
address stays written in the sensor's flash-backed config (§1.3). Every other single-owner
resource here releases on process exit.

`DEPLOYMENT.md` §2's exclusivity table is correct in its conclusions but understates two things:
the LiDAR row's "driver binds UDP 56100–56500" is the *host* side of a device-side reconfiguration,
and the RealSense row's "one owner" is per-endpoint, which may leave depth available to us
while colour is held.

**Nothing in this SDK uses the lease mechanism.** `grep -rn 'Client(.*true)'` across the whole
include tree returns **zero** hits — every client, including `LocoClient`, is constructed with
`enableLease = false`. So no vendor service arbitrates ownership: **whoever writes to the request
topic is obeyed.** If we ever *do* see error 3205/3206/3207 (lease denied / not in cache /
already exists), something outside this SDK has taken a lease, and that would be a genuine
answer to the FSM-authority question. **[src]**

### 7.2 A third stack was running, and our interlock did not see it

`ROBOT-INVENTORY.md` §5 and `DEPLOYMENT.md` §2 describe two stacks sharing this robot: ours and
`gemm` (a container plus `gemm-ai.service`). **There is a third.** Found running from
`/home/unitree/gemm_ai/xr_teleoperate`: **[live]**

| Process                                                   | PID   | Holds                        |
| --------------------------------------------------------- | ----- | ---------------------------- |
| `teleimager.image_server`                                 | 5850  | `/dev/video4`, ~44 % CPU     |
| `_image_service_watchdog.sh` (PPID 1, setsid-detached)     | 5848  | respawns the above, 20×/3 s  |
| `brainco_hand_server --network_interface eth0`             | 5923  | `/dev/ttyUSB1`, ~8 % CPU     |
| `test_vuer_only.py` (started 02:07)                        | 10751 | — someone was actively using it |

**It commands motion.** `repo/teleop/utils/motion_switcher.py` wraps `LocoClient` and calls
`Move()`; `repo/teleop/robot_control/robot_arm.py` publishes to **`rt/arm_sdk`** and
**`rt/lowcmd`**, with `_set_arm_sdk_weight()` and `release_arm_sdk()` (*"smoothly release
arm_sdk control back to ai_sport"*). And `teleop_hand_and_arm.py` without `--motion` calls
`MotionSwitcher().Enter_Debug_Mode()`, which loops `ReleaseMode()` until `CheckMode` returns an
empty name — **deliberately leaving the robot with no motion controller loaded**. **[src]**

That state was observed: `motion_switcher` **1001 `CheckMode`** returned `rpc_code 0`,
`{'form': '0', 'name': ''}` on 2026-08-14. **An empty `name` means no controller is loaded.** In
that state 7001/7002 return nothing at all, so `get_state` reports `fsm_id=None`,
`fsm_mode=None`, `posture=unknown`. **[live]**

> **Run `CheckMode` first whenever the robot ignores commands.** It is the single most useful
> diagnostic found this session, because it distinguishes "wrong FSM id" from "nothing is loaded
> to act on any id" — which look identical from the sport service, since both answer `code 0`.

**`run_c3po` did not notice any of this.** Its `warn_if_other_commander` only greps for
`cmd_vel_to_loco`. A full teleoperation stack holding the camera and the hand bus and driving the
robot through `arm_sdk`/`lowcmd` **passes the check silently**. That is a real gap in the
one-commander invariant (`DEPLOYMENT.md` §2), and it should be widened to cover `xr_teleoperate`,
`teleimager`, `brainco_hand_server`, **`unitree_slam`** (its 1102 pose navigation closes its own
velocity loop — see §7.1) and **any publisher on `rt/arm_sdk` or `rt/lowcmd`**.

**And the arm path has a documented precondition we did not have.** Unitree requires the
built-in Arm Action Service to be switched **off** before anyone drives `rt/arm_sdk` directly —
*"you must first turn off Unitree's built-in Arm Control Service … the service name is
`g1_arm_example`"* via `robot_state` 1001. **[web]** That is also the clean explanation of 7400
contention: two owners of one topic. Note the vendor's own gloss on 7400 is broader than the
robot header's — *"an action is being executed"* — so **7400 means BUSY as well as
CONTENDED**, and our diagnosis text should offer both causes.

Three reasons the existing controls cannot catch it:

1. `stop_gemm` filters `docker ps --filter name=^gemm` — these are not containers.
2. `gemm-ai.service` is systemd, also not a container (already flagged in `DEPLOYMENT.md` §2).
3. `xr_teleoperate` is **neither** — it is a human-launched SSH-session process under a
   setsid-detached watchdog. Killing the server without killing the watchdog just gets it
   respawned.

**The concrete, diagnosable failure this produces:** the arm action service is itself implemented
on top of `rt/arm_sdk` (*"The controller is based on the `rt/arm_sdk` interface"*), so if a teleop
session takes that topic, our gestures start failing with **7400 — "The topic rt/armsdk is
occupied."** Note the vendor's error string spells it **without** the underscore while every
publisher in code uses `rt/arm_sdk`; the code spelling is real and the error text is a typo. **[src]**

A second gesture-path trap in the same family, which we have no handling for: after a *sustained*
gesture the arm latches, and the only accepted next actions are id 99 (release) or a repeat of the
same id — anything else returns **7401 "The arm is holding."** Our bridge has no 7401 handling
anywhere, so it will surface as an unexplained `rpc_error_code_7401`. **[src]** Unitree confirms
the recovery exactly and **names which gestures latch** — *"applicable to sustained actions like
**Arms Horizontal (15)**, **Heart (20/21)**, etc."* **[web]** The **20 s auto-release** is
`[src]` from the robot header only and is **not** corroborated by the docs — **send 99, do not
wait it out.**

**A third trap, and it explains the whole 2026-08-14 session:** *"The Arm Action Service
Interface relies on the built-in motion control. **After entering debug mode, the built-in
motion control exits completely, and the Arm Action Service becomes invalid.**"* **[web]** Since
`xr_teleoperate` started without `--motion` puts the robot in exactly that state, **every arm
call would have failed that day regardless of fsm_id**. Concrete bridge change: run `CheckMode`
before any gesture and, on an empty `name`, fail fast with *"no motion controller loaded — arm
actions are unavailable in debug mode"* rather than surfacing a raw 7404/3104.

### 7.3 Version skew — which vendor tree you are reading matters

Two vendor source trees sit on this machine and they **disagree**. Knowing which is which is the
difference between a real api_id and a hallucinated one: **[live]**

| Tree             | Path                                             | Cloned     | 7110/7111 | arm 7108/7113 |
| ---------------- | ------------------------------------------------ | ---------- | --------- | ------------- |
| `unitree_ros2`   | `gemm/ros2_ws/src/external/unitree_ros2`         | 2026-07-20 | **no**    | **no**        |
| `unitree_sdk2`   | `gemm_ai/xr_teleoperate/vendor/unitree_sdk2`     | 2026-08-13 | yes       | yes           |

**A third source of skew, worth naming: Unitree's published documentation is not a fourth tree
you can trust as a tiebreaker.** It publishes **no api_ids at all** for any service across 45
pages, its struct layouts contradict the IDL our robot actually deserialises with, and several
G1 pages carry verbatim Go2, H1 and B2 text. The support asymmetry is also worth recording:
`get_sdk` (2026-07-20) names and links **only `unitree_sdk2` (C++)**; Python is acknowledged in
a single architecture bullet (*"DDS, supports C++ and Python"*) and never given a repo or a
guide. **[web]** Our bridge's whole dependency, `unitree_sdk2py`, sits **outside Unitree's
documented surface** — which is exactly why the policy of reading the robot's own vendored
headers rather than the docs is right. **Never resolve a Python-side discrepancy by deferring
to a doc page.**

**The `unitree_ros2` tree is the one that matches this firmware.** The 2026-08-13 SDK clone is
newer than the robot and additionally introduces `g1/agv/`, `g1/common/terminations.hpp`,
`rt/user_lowcmd`, `rt/hand_sdk` and `rt/secondary_imu` examples. Treat every SDK-only constant as
**existing in the SDK, unproven on firmware 1.5.3.8**. A `3203 "Api not implement error"` response
is the cheap discriminator.

One thing this settles in our favour: `g1/common/terminations.hpp` — the vendor's own runtime
abort predicates — is **physically on this robot**, so its thresholds can be promoted from
`G1-WEB-RESEARCH.md` §4.1's **[web]** to **[src]** and cited in code: `bad_orientation` > 1.0 rad,
`joint_vel_out_of_limit` > 10.0 rad/s, `ang_vel_out_of_limit` > 6.0 rad/s,
`motor_winding_overheat` > 120 °C (`temperature[1]`), `motor_casing_overheat` > 85 °C
(`temperature[0]`), `low_battery` soc < 20 %, `lost_connection` stale > 1000 ms. **[src]**

### 7.4 Clock sync — we do not need it yet, and here is when we will

Recorded so nobody adds it as cargo cult. Unitree's `time_sync_interface` (2026-04-15, applies
to firmware > 1.5.1 — ours is 1.5.3.8): **PC1 (`192.168.123.161`) is an NTP server**, and the
documented client config is chrony with `server 192.168.123.161 iburst prefer` / `makestep 1.0
3` / `rtcsync`, or `systemd-timesyncd` with `NTP=192.168.123.161` (the two are mutually
exclusive). **[web]**

**We need none of it today.** Our bridge computes `lowstate_age_s` as (local now − local
receipt time) on a single host, so no cross-clock comparison happens, and DDS itself does not
require synchronised clocks. It becomes **required** the moment we do any of: correlate the
colleague's rosbag/foxglove timestamps against our logs; fuse D435i frames (which the vendor
times with `CLOCK_REALTIME`, §2.1) with DDS state; or timestamp DB episodes against on-robot
events.

Two cautions worth having now regardless: **[web]**

- ⚠️ *"When PC1 performs network time synchronization, it may cause short-term time
  fluctuations. Therefore, it is recommended to disable G1's WiFi mode when using programs that
  rely on system time."* **The Jetson is on Wi-Fi for our SSH/mDNS access**, so a mid-experiment
  clock step could make a latency or duration measurement nonsense.
- If we ever do add chrony on the Jetson, expect the documented failure: chronyd 3.5 dies with
  `code=dumped, signal=SYS` under its seccomp filter. Fix is `DAEMON_OPTS="-F 0"` in
  `/etc/default/chrony`.

---

## 8. Open questions

Each with what would settle it. **A few are now answerable without robot access at all** — those
come first.

**Answerable with no robot**

0a. **Fetch the two Dex3 tactile pad-layout images** at
    `doc-cdn.unitree.com/static/2024/12/26/…_5000x2812.png`, dropped by the docs' markdown
    conversion. They are the only pad→finger map that exists (§4.4). Only needed if tactile
    matters.
0b. **Ask the operator to read the Unitree Explore APP's waist motor lock switch**, and whether
    a ≥ 1.3.0-era calibration was ever done. Phone only. It is `ROBOT-API.md` §11.3 Rank 2 and
    it decides which walk FSM id this firmware accepts.
0c. **Transcribe the mode-switch diagram** — see `ROBOT-API.md` §13, Q1. Not a peripherals
    question, but it is the single highest-value doc action outstanding.

**LiDAR**

1. **Where is the Mid-360 unicasting right now?** Not to the Jetson (proved passively). Most
   likely the control board, or `point_send_en` (`0x0003`) is off. Settle with
   `sudo tcpdump -i eth0 -n host 192.168.123.120 -c 20`, or a read-only
   `QueryLivoxLidarInternalInfo` reading registers `0x0006`/`0x0007`. **Lower priority now** —
   the DDS republish (§1.5) means we should not be taking the unicast at all.
2. **Is the vendor `lidar_driver` service on, and do `rt/utlidar/*` exist right now?** Settle with
   `robot_state` api_id **1003 `ServiceList`** — a pure getter, and the cheapest high-yield probe
   on the whole robot. The vendor's expected service-name list is in `ROBOT-API.md` §8.
3. **Is the host IP the SDK writes into the sensor persistent across a LiDAR power cycle?** This
   decides whether "reversible" means automatic or "stays stolen". Settle by power-cycling after
   running our driver and re-testing which host receives point data.
4. **Sign of `/livox/imu` linear_acceleration.z with the robot static** — the inverted-mount
   gravity trap (§1.7). One supervised window with the driver up.
5. **RELIABLE or BEST_EFFORT on the `utlidar` topics?** Bag metadata and gemm's prose contradict
   each other; a default subscriber sees nothing if the prose is right.
6. **The real `base_link → lidar_link` translation.** gemm's default `z = 1.0` is marked
   "APROXIMADA". Unitree now gives `(−0.0, 0.0, −0.47618)` with an "inverted" placement — but
   the sign is wrong for a head-mounted sensor as literally stated, so **validate against a real
   cloud before using it in any transform** (§1.5).
6a. **Is our unit a Mid-360 or a Mid360s?** Unitree changed the part for units produced after
    April 2026; this robot arrived 2026-08-04. It does not affect the DDS republish path, but it
    invalidates any pinned Livox-SDK2 / `livox_ros_driver2` version (§1.5).

**Cameras**

7. **Did `/dev/video10` ever exist on this robot?** The only unanswered part of the chest-camera
   question. Settle with `sudo dmesg | grep -iE 'usb|uvc|xhci'` (blocked: `dmesg_restrict=1`) plus
   a physical look at the chest for an unplugged lead.
8. **Does `videohub_pc4` actually produce frames on `rt/frontvideostream`, or does it also sit in
   its retry loop?** It held `/dev/video4` for ~6 minutes at boot but nothing captured its stdout.
   Settle by stopping teleimager, `sudo systemctl start master_service`, then subscribing with the
   0.10.2 Python bindings on domain 0 / eth0.
9. **Does `unitree_sdk2py`'s Go2 `VideoClient` (service `videohub`, 1001) get a response from the
   G1 head videohub?** Topic names match exactly, so it should. Not exercised.
10. **Can the D435i be shared per endpoint?** Depth (`video0`) and IR (`video2`) were unclaimed
    while teleimager held only colour. Test whether we can open depth without disturbing anyone —
    and whether depth and IR share an endpoint, which Intel's doc does not say.
11. **Is the RTP H.264 multicast at `230.1.1.1:1720` reachable from the Mac?** It is multicast on
    the wired robot LAN, so per SPEC §10.2 a Wi-Fi-only Mac almost certainly cannot receive it.
    Unverified either way.

**Hands**

12. **Does the control board publish `rt/dex3/{left,right}/state` or `rt/inspire/state`?**
    **Now the highest-value probe in this whole document.** Both types are already in our venv,
    the subscribe is passive with zero writes, and a single message settles §4 outright — it
    also decides the newly-raised possibility that a Dex3 resident service is running on PC1
    where we cannot look (§4.2, §4.3a). Do this **before** the next locomotion window, since
    Unitree advises against running/balance tests with hands attached.
13. **Which hands are physically fitted, and how many?** Settle by **looking at the robot** —
    three fingers and 7 DoF is Dex3-1; five fingers is BrainCo Revo2 **or** either Inspire, so a
    glance narrows but does not close it. Do not settle it by probing the serial ports.
14. **Which G1-EDU variant letter is this unit?** The OTA name gives `G1_Edu+`, which maps to
    **none** of Standard / Advanced / Ultimate A–D (§4.0). `mode_machine = 5` tells us only
    29 DoF. Is the variant recorded anywhere — a model plate, an `mscli` field, a config under
    `/unitree`, the serial number?
15. **Is anything attached to the left wrist at all?** The BrainCo probe only speaks Modbus RTU at
    460800, so its silence proves nothing about a non-BrainCo device — and per §4.1 the service
    is launched with an explicit `--serial` per hand, so it only ever probed what it was told to.
16. **If a Dex3 pair is fitted, where does its RS485 land?** No vendor page associates the Dex3
    with a PC2 USB-serial dongle; the pages that describe such dongles are the Inspire ones
    (§4.2). Tracing which physical wrist cable lands on which FTDI channel would be decisive.
17. **For BrainCo, which end of [0,1] is open?** Documented for Inspire DFX (1.0 = open) and
    implied for `hand_sdk` (positive tau closes), but **not** for BrainCo. Do not write an "open
    hand" preset until it is read out of the server source or observed (§4.1).
18. **Dex3 tactile: 6 pads or 9, and what is the pad→finger map?** The hand page says three 3×4
    fingertip arrays (6 locations); `about_G1` says 9 array sensors. The map exists only in the
    two dropped images (Q0a). ~~Right-hand slot 3~~ is settled: **`middle_0` on both hands**
    (§4.4).
19. **Do `rt/dex3/*/cmd` and `rt/brainco/*/cmd` conflict if both are published?** Nothing
    describes arbitration between a resident Dex3 service and a user-run BrainCo one, and per
    `ROBOT-API.md` §1.4 the robot arbitrates nothing. Worth knowing before any hand skill ships,
    given `brainco_hand_server` was found already running.
20. **Provenance of `/api/dex3_msg_controller`** (§4.6). If nobody can point at the observation, it
    should be struck from `ROBOT-INVENTORY.md` §4 and `MENTAL-MODEL.md`.

**Audio**

21. **Is `rt/audio_msg` publishing, and what is the exact payload?** A 10–15 s read-only DDS
    subscribe as `std_msgs::msg::dds_::String_` while someone speaks Chinese, English and Spanish
    nearby. The schema is now documented (§5.4) — this is about whether **this** firmware
    matches it, and specifically whether **`play_state`** appears and whether it fires for *our*
    `PlayStream` or only the assistant's playback. If ours, `say()` gets true completion
    detection.
22. **Is the mic multicast gated on wake-up mode, the way `rt/audio_msg` ASR output explicitly
    is?** Determines whether a future `listen()` needs an operator to set a mode from the APP or
    remote — a human prerequisite we cannot satisfy over DDS (§5.4).
23. **Does the mic multicast actually carry packets today?** The group is joined; joined ≠ flowing.
    Count packets only, for ~5 s — and get the operator's explicit consent first, because it is
    indistinguishable from recording the person standing next to the robot.
24. **What does `voice` api 1002 (`ASR`) do?** Registered by every client, called by none, and it
    has **no documented counterpart at all** — the official AudioClient has six functions and no
    ASR call (§5.3). Candidates remain: enable/disable, language select, pull-last-result.
25. **Does the firmware care that the vendored `TtsMaker` sends `index: 0` forever?** The official
    prototype has **no `index` parameter**, so `index` is client-internal and the bug has no
    documented consequence — and no documented defence either. Send two different texts back to
    back and listen.
26. **Does `TtsMaker` queue or interrupt when speech is already playing?** Defined only for
    `PlayStream` (via `stream_id`) and nowhere for TTS. Until tested, prefer `PlayStream` for
    anything interruptible. Related: does a second `app_name` interrupt the first?
27. **`GET_VOLUME` (1005) is the one genuinely read-only call on the whole voice service** — and
    it now answers three things: whether the service is alive, whether it is ≥ `Vui_Service
    2.0.3.8` (a `3203` says no), and **whether audio is FSM-gated**, by calling it once in each
    reachable state including the empty-name debug state (`ROBOT-API.md` §7.4). If it answers
    everywhere, `say()` becomes the bridge's fallback acknowledgement for refused motion.

**State and input**

28. **What are the real BMS numbers?** `soc`, `soh`, `temperature[12]`, `bmsstate[5]`, and the
    units of `current`/`bmsvoltage`. One decoded message from `rt/lf/bmsstate` — **and confirmed
    unanswerable by reading**, since Unitree documents `BmsState_`'s fields nowhere (§6.2).
29. **Is `rt/secondary_imu` / `rt/lf/secondary_imu` actually published on this firmware?** Both
    spellings are vendor-documented as a high-rate/low-frequency pair, so the "only one can be
    right" framing is withdrawn — what is unconfirmed is whether a torso IMU exists here at all.
30. **Is `rt/wirelesscontroller` independently published on the G1**, or is the remote only
    available as `LowState_.wireless_remote[40]`? Zero hits across 45 official pages makes the
    latter the strong default; a passive `DCPSPublication` scan settles it (Q35).
31. **Which button combinations does the firmware itself intercept?** Still undocumented — the
    official page maps combos to its own ①…⑧ symbols and never to FSM ids. Log `wireless_remote`
    while an operator presses each combo (**held ≥ 2 s**) and watch whether `fsm_id` changes with
    no RPC from us. Only in a supervised window, with a hand on the physical e-stop.
32. **Which DDS field carries the total-device status word** whose `0x1000` is "soft emergency
    stop switch is pressed"? Candidates: `unitree_go SportModeState_.error_code` on
    `rt/odommodestate`, or `MainBoardState_` on `rt/lf/mainboardstate` (`ROBOT-API.md` §9.6). It
    is `ROBOT-API.md` §11.3 Rank 4.

**Sharing**

33. **Who stopped `master_service` at 01:40:34, and should it be restarted?** Nothing in
    `scripts/robot/` touches it. Until it runs, both video-hub nodes stay down and the boot-time
    `amixer set Speaker 75%` has not been applied. (Note the vendor's own advice for synthesised
    audio: *"set the volume to 100% to ensure maximum playback volume"* — the ALSA mixer and the
    `voice` service's 0–100 volume are two independent gains, and a silent robot can be either.)
34. **Does the arm action service still work while `brainco_hand_server` or an `xr_teleoperate`
    session holds `rt/arm_sdk`, and is 7400 "occupied" or "busy"?** The robot header says the
    former, the official remark says the latter (§7.2). Two back-to-back gestures with nothing
    else running discriminates. Test deliberately rather than mid-demo.
35. **What is the full DDS topic census?** gemm's stack reports the robot exposes ~121 topics and
    references a `docs/robot-topics.md` that is **not on the robot** — it lives in their repo
    elsewhere. Getting that file would close most of §6 in one step. Failing that, a passive
    `DCPSPublication` discovery read (topic name → type name) creates no writers and would produce
    the definitive live census in one shot — and would simultaneously answer Q12, Q30 and the
    `rt/lowstate`-vs-`rt/lf/lowstate` question.
36. **Is our onboard CycloneDDS config hiding participants?** We set
    `AllowMulticast=false` plus a single unicast peer at `192.168.123.161`; the vendor's own
    config is plain multicast on `eth0` (`ROBOT-API.md` §10). Anything publishing from another
    `192.168.123.x` address — the NX itself, `unitree_slam`, a colleague's node — would be
    invisible to us with no error. Test by publishing from the Jetson and checking whether our
    own bridge sees it.

**Answered by the official documentation — kept as a record**

- ~~The `lf` Dex3 state topic being a decimated mirror~~ — vendor convention, confirmed (§4.4).
- ~~Right-hand Dex3 slot 3: `middle_0` or `index_0`?~~ — **`middle_0` on both hands**, and the
  left URDF's out-of-order link names explain where the contradiction came from (§4.4).
- ~~Is the head camera a D435i or a UVC binocular?~~ — **D435i**, and it has no DDS path (§2.1).
- ~~Can the LiDAR be shared?~~ — not the raw unicast, but **yes over DDS**, vendor-confirmed at
  10 Hz / 200 Hz (§1.5).
- ~~Is the voice volume range 0–255?~~ — **0–100** (§5.3).
- ~~Does `PlayStop` take `app_name` or `stream_id`?~~ — **`app_name`** (§5.5).
