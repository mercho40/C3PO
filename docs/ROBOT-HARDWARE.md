# G1 Hardware — what the physical machine presents

The ground-truth survey of the real G1: hosts and network, the DDS layer, every peripheral
(LiDAR, cameras, hands, audio, IMUs, battery, remote), and the third-party stacks that
cohabit the robot. This is the reference for anyone integrating a sensor, writing a
peripheral skill, or trying to understand why a device is silent.

Scope boundaries:

- **Control API** (services, api_ids, FSM, SET_VELOCITY, gestures, state topics, error
  codes) → `docs/ROBOT-API.md`. This doc points there rather than restating.
- **Perception pipeline** (containers, FAST-LIO, Nav2, domain-42 design) →
  `apps/perception/README.md`.
- **Operating the stacks** (start/stop scripts, deploy, ports, exclusivity _procedure_) →
  `docs/OPERATIONS.md`. This doc records what the hardware enforces; that one records what
  we do about it.

Observations here are dated snapshots from on-robot sessions (mostly 2026-08-11 through
2026-08-15); each carries its date where it matters. Timestamps quoted _from_ the robot are
in its own clock, `Asia/Shanghai` (CST +0800) — that is timezone, not skew.

## Evidence tags

- **[live]** — observed directly on the robot
- **[src]** — read from source, config or binaries on the robot
- **[web]** — from published documentation. Two bodies, both unverified against this robot:
  third-party/community material, and Unitree's own official G1 developer documentation
  (45 pages, folded in 2026-08-13). Prefer `[src]`/`[live]` over both, always — official is
  not the same as correct, and several G1 pages are demonstrably copy-pasted from other
  robots. Citation rules and the wrong-robot list are in `docs/ROBOT-API.md`'s legend.
- **[?]** — believed but _not_ verified; do not build safety-critical logic on these

## Contents

1. [Compute and network](#1-compute-and-network)
2. [DDS layer](#2-dds-layer)
3. [The devices at a glance](#3-the-devices-at-a-glance)
4. [Livox Mid-360 LiDAR](#4-livox-mid-360-lidar)
5. [Intel RealSense D435i](#5-intel-realsense-d435i)
6. [The G1's own cameras via `video_hub_pc4` — degraded](#6-the-g1s-own-cameras-via-video_hub_pc4--degraded)
7. [Hands — unresolved, balance toward BrainCo](#7-hands--unresolved-balance-toward-brainco)
8. [Audio — mic array, speaker, LED](#8-audio--mic-array-speaker-led)
9. [IMU, battery and wireless controller](#9-imu-battery-and-wireless-controller)
10. [Cohabitants: the gemm stack, teleop, and sharing](#10-cohabitants-the-gemm-stack-teleop-and-sharing)
11. [Clock sync](#11-clock-sync)
12. [Open questions](#12-open-questions)

---

## 1. Compute and network

Two computers, plus a LiDAR that is its own network host. **[live]**

| Node                       | Address                                | Role                                                                               |
| -------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------- |
| Jetson Orin NX (`g1-orin`) | `192.168.123.164` (eth0), DHCP (wlan0) | General-purpose host. SSH. Where our bridge runs.                                  |
| Control board ("PC1")      | `192.168.123.161`                      | Publishes the robot's DDS topics. **No SSH** — the only open TCP port is **9991**. |
| Livox Mid-360 LiDAR        | `192.168.123.120`                      | Direct network peer on the internal LAN.                                           |
| Mac (when cabled)          | `192.168.123.99`                       | Static, set by hand — the internal LAN has **no DHCP server**.                     |

The control board pushes **~24 MB/s to multicast `239.255.0.1`** and has **no wireless
interface**. That is the whole reason the bridge runs onboard for `SIM_MODE=real` — the
consequence chain is in `docs/ARCHITECTURE.md`.

**The Jetson carries almost no vendor payload — by design, and Unitree says so.**
`/unitree/module/` holds exactly two modules, `master_service` and `video_hub_pc4`, and the
vendor's own install bundle at `/home/unitree/g1plus_pc4_unitree_install/` confirms that is
the complete "pc4" payload — its `module/` contains only `master_service`. **[live]**
Unitree states the rule outright: _"**Unitree does not deploy services on the NVIDIA Jetson
Orin module**"_ and _"PC1 is dedicated to the Unitree motion control program and is **not
open to the public**. Developers can only use PC2 for secondary development."_ **[web]**
(`about_G1` 2026-05-06, `architecture_description` 2025-04-30. PC1 = `192.168.123.161`,
PC2/NX = `192.168.123.164`.)

So every motion, audio, hand and state service (`sport`, `arm`, `voice`, `motion_switcher`,
`robot_state`, and a Dex3 resident driver if one exists) runs on the control board, which
has no SSH. **Those we can only reach over DDS, never by reading files** — which means a
filesystem-wide search on the Jetson is evidence about the Jetson and about nothing else.
§7.2 records where that bit us.

Firmware identity and the vendor-source-tree version-skew rules live in
`docs/ROBOT-API.md`'s legend — read that before trusting any api_id or struct from a
vendored tree or a doc page.

**Wi-Fi:** `EDU-Special`, WPA2-PSK, **MAC-whitelisted** — a new device cannot just join.
The robot's `wlan0` MAC `14:0a:02:f0:63:f6` is the whitelisted one. **[live]**

**Address it by name, not by number.** The `wlan0` DHCP lease has moved twice
(`10.4.64.27` → `10.10.32.19`), and one of those old addresses later answered as a
different device entirely — so a stale IP does not fail closed, it fails _misleadingly_.
`avahi-daemon` runs onboard and **`g1-orin.local` resolves from the Mac over
`EDU-Special`** — verified 2026-08-12, both `dscacheutil` and `ping`. **[live]** Use that
name in `~/.ssh/config`, `BRIDGE_URL`, and anywhere else the robot needs naming. A static
DHCP reservation for `14:0a:02:f0:63:f6` would work too, but mDNS needs no cooperation from
the school's network team.

The SSH user is **`unitree`** (home `/home/unitree`). `c3po` (Wi-Fi/mDNS) and `c3po-wire`
(cabled path) are `Host` aliases in the Mac's `~/.ssh/config`, not accounts on the robot.
**[live]**

**Route trap.** The vendor eth0 NetworkManager profile (`unitree1`) installs
`default via 192.168.123.1` at metric 20100, beating Wi-Fi's default route — but that
gateway never resolves in ARP, so all egress black-holes and the robot has _no internet_
even with Wi-Fi up. Fixed with `nmcli connection modify unitree1 ipv4.never-default yes`,
which keeps the on-link `192.168.123.0/24` route DDS needs. **Re-check after any Unitree
OTA.** **[live]**

---

## 2. DDS layer

**Two incompatible CycloneDDS versions coexist on the Jetson.** **[live]**

| Version | Where                                                  | Config schema                                  |
| ------- | ------------------------------------------------------ | ---------------------------------------------- |
| 0.7.0   | ROS 2 Foxy debs (`ros-foxy-cyclonedds`)                | `<NetworkInterfaceAddress>`                    |
| 0.10.2  | `/usr/local/lib`, `~/cyclonedds_ws/install/cyclonedds` | `<Interfaces><NetworkInterface/></Interfaces>` |

Feeding a modern `<Interfaces>` config to the Foxy stack fails outright:
`config: //CycloneDDS/Domain/General: Interfaces: unknown element`.

Consequences:

- Our bridge pins `cyclonedds==0.10.2`, which **matches the prebuilt library already on the
  Jetson** — so `CYCLONEDDS_HOME` points at `~/cyclonedds_ws/install/cyclonedds` onboard and
  needs no source build, unlike the Mac (values and per-host comments live in
  `apps/bridge/.env.example`; build steps in `apps/bridge/README.md`). That library is a
  third-party 2023 build in a home directory, so an OTA could remove it; that is the main
  argument for eventually containerizing. It is also **not only ours**: the vendor's own
  `videohub_pc4` firmware service links against the same tree (§6.5), so deleting or moving
  it breaks a root-owned vendor service, not just C3PO.
- **Do not depend on the ROS 2 CLI onboard.** `ros2 topic list` segfaults (exit 139) both
  with and without `--daemon` since the last reboot. It only ever appeared to work because
  the boot-time daemon answered over a local socket without the CLI touching DDS. Use the
  0.10.2 Python bindings instead.
- **Interface pinning is mandatory onboard, and here is the evidence.** CycloneDDS picks
  arbitrarily among `eth0`, `docker0`, `wlan0` — logged verbatim as
  `selected arbitrarily from: eth0, docker0, wlan0`. Unpinned, the bridge may see none of
  the robot. Hence `DDS_INTERFACE=eth0` onboard (the env var and its comment live in
  `apps/bridge/.env.example`). ⚠️ The pinning **currently does not reach CycloneDDS** —
  the vendor SDK's inline config overrides the bridge's `CYCLONEDDS_URI`; it has worked
  only because autodetermine lands on `eth0` while `docker0` is down. The pending fix is
  tracked in `docs/ROBOT-API.md` (known divergences). The vendor independently
  corroborates the choice: their own `videohub_pc4` CycloneDDS config pins `eth0` with
  the 0.10.2 schema (§6.5).

The `idlc` compiler shipped in `~/cyclonedds_ws/install/cyclonedds/bin/` can generate
Python IDL for types the SDK does not ship — see `docs/DECISIONS.md` D2.2 and
`apps/bridge/src/bridge/sdk/ros_idl.py`.

---

## 3. The devices at a glance

State as of the 2026-08-13/14 survey, with 2026-08-15 recon updates folded in.

| Device                   | Attaches via                                                                                        | Address / node                                                                  | Held by, at snapshot                   | Health                                            |
| ------------------------ | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | -------------------------------------- | ------------------------------------------------- |
| Livox Mid-360 LiDAR      | Ethernet, robot internal LAN — **also republished on DDS**, §4.5                                    | `192.168.123.120`, `0c:9a:e6:87:5c:4a`                                          | **not the Jetson** — some other host   | alive, 2.5 ms RTT; not streaming to us            |
| Intel RealSense D435i    | USB 3.0 hub `2-2`, port 3 → `2-2.3`, `8086:0b3a`                                                    | `/dev/video0–5`, IIO accel+gyro                                                 | `teleimager.image_server` (video4)     | healthy; **depth and IR unclaimed**               |
| G1 "head camera"         | **is the D435i colour node** — see §6                                                               | `/dev/video4`                                                                   | nobody (`master_service` stopped)      | stopped, recoverable                              |
| G1 chest camera          | would be `/dev/video10`                                                                             | —                                                                               | nobody                                 | **absent** — no such device electrically          |
| Hands                    | RS485 behind FTDI FT4232H `0403:6011`; a Dex3 pair would be served from the **control board**, §7.2 | `/dev/ttyUSB0–3`                                                                | `brainco_hand_server` (ttyUSB1)        | one right hand answering; identity **[?]** — §7.3 |
| Mic array (4 mics)       | control board, **not** the Jetson                                                                   | UDP mcast `239.168.123.161:5555`                                                | `gemm-ai.service` joined the group     | joined but **silent at rest** — §8.2              |
| Speaker + RGB LED        | control board                                                                                       | `voice` RPC service (`vui_service`)                                             | shared, **no arbitration, 3+ writers** | live — answered at **FSM 0**, 2026-08-21          |
| Body IMU                 | inside `LowState_`                                                                                  | `rt/lf/lowstate`                                                                | shared (DDS)                           | live, ~20 Hz                                      |
| Battery / BMS            | own DDS topic                                                                                       | `rt/lf/bmsstate`                                                                | shared (DDS)                           | live, ~20 Hz — bridge reads `soc` (§9.2)          |
| Wireless controller (R3) | control board radio                                                                                 | **`LowState_.wireless_remote[40]`** — `rt/wirelesscontroller` is Go2-only, §9.3 | shared (DDS)                           | see `docs/ROBOT-API.md` for the decode            |

---

## 4. Livox Mid-360 LiDAR

### 4.1 Addressing and ports

A plain network peer on the robot's internal wired LAN. From the Jetson's `eth0`
(`192.168.123.164/24`): `ping` 3/3, RTT 1.902/2.470/3.600 ms, `ttl=255` — a `ttl` of 255 is
characteristic of an embedded stack, not Linux. ARP: `192.168.123.120 lladdr
0c:9a:e6:87:5c:4a`. **[live]**

Ports, read from the deployed driver config rather than from memory — **five**, not four:
**[src]**

| Purpose      | LiDAR-side | Host-side |
| ------------ | ---------- | --------- |
| Command      | 56100      | 56101     |
| Push message | 56200      | 56201     |
| Point data   | 56300      | 56301     |
| IMU data     | 56400      | 56401     |
| Log data     | 56500      | 56501     |

**Trap: the host side is `+1`.** A `tcpdump` filter on `udp port 56300` will see nothing
while `56301` sees everything. The same numbers appear in the upstream vendor default, so
this is the stock Mid-360 scheme, not a Unitree customisation. (One community report,
livox*ros_driver2 issue #176, claims having to set host ports \_equal* to the LiDAR ports to
get data — contrary to both the shipped config and the upstream default; treat as noise
unless reproduced. **[web]**)

Default Livox addressing is `192.168.1.1XX` where `XX` is the serial's last two digits
**[web]**; ours is `192.168.123.120`, so Unitree changed both subnet and host. **The
address is almost certainly unit-specific** — any hardcoded `.120` found in third-party
material is a per-unit assumption, not a G1 constant.

### 4.2 The driver config actually on the robot

Deployed: `/home/unitree/gemm/ros2_ws/src/gemm/gemm_bringup/config/mid360_config.json`
(md5 `c55200ef4aafec7af163a70c9edc699e`), verbatim: **[src]**

```json
{
  "lidar_summary_info": { "lidar_type": 8 },
  "MID360": {
    "lidar_net_info": {
      "cmd_data_port": 56100,
      "push_msg_port": 56200,
      "point_data_port": 56300,
      "imu_data_port": 56400,
      "log_data_port": 56500
    },
    "host_net_info": {
      "cmd_data_ip": "192.168.123.164",
      "cmd_data_port": 56101,
      "push_msg_ip": "192.168.123.164",
      "push_msg_port": 56201,
      "point_data_ip": "192.168.123.164",
      "point_data_port": 56301,
      "imu_data_ip": "192.168.123.164",
      "imu_data_port": 56401,
      "log_data_ip": "",
      "log_data_port": 56501
    }
  },
  "lidar_configs": [
    {
      "ip": "192.168.123.120",
      "pcl_data_type": 1,
      "pattern_mode": 0,
      "extrinsic_parameter": {
        "roll": 180.0,
        "pitch": -2.3,
        "yaw": 0.0,
        "x": 0,
        "y": 0,
        "z": 0
      }
    }
  ]
}
```

`lidar_type: 8` = Mid-360. `pcl_data_type: 1` = `kLivoxLidarCartesianCoordinateHighData`
(32-bit mm cartesian, the normal SLAM choice). `pattern_mode: 0` =
`kLivoxLidarScanPatternNoneRepetive` — the non-repetitive rosette that grows coverage with
integration time.

**Three config paths look plausible and are wrong.** Getting this wrong costs an afternoon:

- `…/build/gemm_bringup/config/mid360_config.json` **exists as a directory entry** but is a
  dangling symlink to `/ws/ros2_ws/src/…` — `/ws` is the gemm container's mount point and
  does not exist on the host, so any `cat`/`stat` from the Jetson returns ENOENT and the
  file reads as "not there". **[live]**
- `…/install/gemm_bringup/share/…` genuinely does not exist, even though `livox.launch.py`
  resolves its `user_config_path` through `FindPackageShare('gemm_bringup')`. The launch
  only works from inside the container's install space. **[live]**
- `src/external/g1pilot/config/livox_mid.json` has the right LiDAR IP but a `host_net_info`
  pointing at `192.168.123.123`, an address on **no** interface of this robot. Its symptom,
  in the gemm authors' own words: _"cero paquetes, en silencio."_ **[src]**

(Our own perception stack carries a fourth config, with `host_net_info` correctly at
`192.168.123.164` — `apps/perception/nav/ws/src/c3po_perception/config/MID360_config.json`.
See §4.5 for what running it implies.)

### 4.3 The single-host unicast constraint — and why the raw stream cannot be shared

This is the most consequential fact about the sensor, and it is not an OS-level lock.

`host_net_info` is **not** read by `livox_ros_driver2`. Its
`src/parse_cfg_file/parse_livox_lidar_cfg.cpp` parses only the `lidar_configs` array; grep
for `host_net_info` there returns nothing. The same JSON path is handed to the SDK at
`src/lds_lidar.cpp:142` (`LivoxLidarSdkInit(path_.c_str())`), and
`/usr/local/lib/liblivox_lidar_sdk_shared.so` contains the `host_net_info` literals and the
error _"Parse host net info failed, has not host_ip or cmd_data_ip."_ The SDK pushes those
addresses to the sensor via `SetLivoxLidarPointDataHostIPCfg` / `…ImuDataHostIPCfg` /
`…StateInfoHostIPCfg`, and `/usr/local/include/livox_lidar_def.h` shows where they land:
**[src]**

| Register | Field                                   |
| -------- | --------------------------------------- |
| `0x0006` | `HostPointIPInfo pointcloud_host_ipcfg` |
| `0x0007` | `HostImuDataIPInfo imu_host_ipcfg`      |
| `0x0008` | `LivoxIpCfg ctl_host_ipcfg`             |

**The destination lives in the sensor's own flash-backed config, not in the client.** There
is no multicast and no second stream. The sensor cannot be shared — only handed over.
Whoever starts a driver **last** re-points it for everyone, and killing that process does
not hand it back, because the address stays written.

The gemm authors state the same constraint in `livox.launch.py`'s docstring: _"OJO: el
Mid-360 unicastea el point data al host configurado. Al levantar este driver, el LiDAR pasa
a mandarnos el stream a nosotros; mientras corre, el SLAM del vendor puede quedar sin
datos. Es esperado y reversible (el vendor lo reconfigura la próxima vez que arranca)."_
**[src]** Their "reversible" claim is explicitly untested and should not be relied on.

Handing it back is worse than an IP change, because the driver also **writes sensor
settings** on every discovery: `livox_lidar_callback.cpp`'s `LidarInfoChangeCallback` calls
`SetLivoxLidarPclDataType`, `SetLivoxLidarScanPattern`, `SetLivoxLidarBlindSpot`,
`SetLivoxLidarDualEmit`, `SetLivoxLidarInstallAttitude`,
`SetLivoxLidarWorkMode(kLivoxLidarNormal)` and `EnableLivoxLidarImuData`. The next owner
inherits all of that unless it sets its own. **[src]**

Two escape hatches exist in Livox-SDK2's README but not in the ROS driver's:
`"master_sdk": false` makes the SDK listen-only, and `"multicast_ip"` fans the stream to a
group — a two-line JSON edit, since the driver passes the config straight through to SDK2.
**[web]** Untested here, and a slave only receives if someone already configured the group
as master.

### 4.4 Nothing was publishing it, and not to us

Four independent checks at the 2026-08-13/14 snapshot, all negative: **[live]**

- No driver process (`ps aux | grep -iE 'livox|fast_lio|point_lio|lio'` → empty).
- No containers at all — `gemm-bringup`, where the driver would run, was `Exited (137)`.
- No socket bound to any `56[1-5]0[01]` port.
- Passive `/proc/net/snmp`: UDP `NoPorts` flat at 2378 across 9 s while `InDatagrams` rose
  ~15k (~1.6k pkt/s, the control board's DDS multicast). If the sensor were unicasting to
  `192.168.123.164` with nothing bound, `NoPorts` would climb fast. It did not.

So the Mid-360's unicast target was **some host other than the Jetson** — most likely the
control board with the vendor `lidar_driver` owning it, or `point_send_en` (register
`0x0003`) disabled outright. Unresolved (§12 Q1). No FAST-LIO2, Point-LIO or FAST-LIVO2
exists anywhere on this robot — an exhaustive search over `/home/unitree`, `/unitree` and
`/opt` returned zero — so LiDAR integration is greenfield. **[live]** (Our bridge does not
consume the LiDAR at all today; `mcp_server.py` passes `lidar_online=False` unconditionally
and `world_model.py` refuses honestly.)

### 4.5 Two integration routes: the DDS republish, and our own driver

**Route 1 — the vendor `lidar_driver` DDS republish (sharing-friendly).** The control board
keeps the sensor as its peer and republishes over DDS. Measured off two rosbags recorded
from this very robot, and confirmed by Unitree's own most recent LiDAR page
(`lidar_services_interface`, 2026-06-04): **[live]** + **[web]**

| Topic                           | Type                                   | Rate (measured / documented)  | Frame         |
| ------------------------------- | -------------------------------------- | ----------------------------- | ------------- |
| `rt/utlidar/cloud_livox_mid360` | `sensor_msgs::msg::dds_::PointCloud2_` | 9.94 / 9.82 Hz — **10 Hz**    | `livox_frame` |
| `rt/utlidar/imu_livox_mid360`   | `sensor_msgs::msg::dds_::Imu_`         | 199.6 / 198.2 Hz — **200 Hz** | `livox_frame` |

An ordinary pub/sub topic any number of subscribers can join — the only multi-consumer
route to this sensor. Two blockers, both ours: `unitree_sdk2py` ships `PointCloud2_` (and
`PointField_`) but **not** `sensor_msgs::msg::dds_::Imu_`, so the 200 Hz IMU topic needs
hand-written IDL; and the topics only exist while `lidar_driver` is switched on — a
documented dependency (Lidar Driver ≥ 1.0.0.5), toggled with no vendor SDK via the
`robot_state` service (`ServiceSwitch` `{"name":"lidar_driver","switch":0|1}`; the api_ids
and the `ServiceList` probe live in `docs/ROBOT-API.md` §8). The gemm client for it is
`gemm_bringup/tools/g1_service.py`. **[src]**

**MEASURED 2026-08-21, and it changes the recommendation.** A subscriber in our own
`humble` container, on domain 0 with CycloneDDS pinned to `eth0`: **[live]**

```
/utlidar/cloud_livox_mid360   sensor_msgs/msg/PointCloud2   9.71 Hz   20064 pts   point_step 22
    x y z intensity (float32) | ring (uint16) | time (float32, offset 18)
/utlidar/imu_livox_mid360     sensor_msgs/msg/Imu
publisher QoS (both):  RELIABLE, KEEP_LAST(1), VOLATILE
```

Three things follow, and together they make Route 1 far more attractive than it looked:

1. **The cloud carries PER-POINT `time`, plus `ring`.** That is the velodyne layout, which
   FAST-LIO reads natively as `lid_type: 2` — so the CustomMsg requirement that appeared to
   rule this route out does not apply. No custom preprocessor. (`time_unit` still has to be
   matched to the field's units; check before trusting the odometry.)
2. **The IMU is a standard `sensor_msgs/msg/Imu`.** The "unitree*sdk2py ships no `Imu*`"
   blocker is real but irrelevant here: it binds the _bridge_, which has no ROS. A ROS 2
   node gets the type for free.
3. **The publishers are RELIABLE.** The contradiction below is settled in favour of the bag
   metadata; the prose was wrong. A BEST_EFFORT subscriber still matches (requested weaker
   than offered), which is why a default-QoS probe sees data when the service is on.

Two caveats that stay: `KEEP_LAST(1)` means a slow subscriber silently DROPS rather than
queues, and the topics are on **domain 0** — reaching them from our domain-42 containers is
the real cost of this route, not the message format.

**QoS trap, and it already burned the gemm team once:** their 2026-08-07 conclusion that
"these topics do not exist in any DDS domain" was wrong — it came from probing with a
default-QoS subscriber while the service was switched **off**. Their note claims the
publishers are BEST*EFFORT (*"`ros2 topic hz`/`echo` con el default no ve nada aunque estén
fluyendo"_) **[src]** — except the bag metadata \_they_ produced records those publishers as
**RELIABLE**, KEEP_LAST depth 1, VOLATILE. **[live]** The prose and the metadata contradict
each other; trust the metadata, but verify before relying on either. Depth-1 KEEP_LAST also
means a slow subscriber silently drops rather than queues.

**Route 2 — run our own `livox_ros_driver2` (what the perception stack does).** The
operative perception design (`apps/perception/README.md`) runs the driver in the nav
container with `host_net_info` pointed at the Jetson — i.e. it **takes the unicast**, with
everything §4.3 implies: it steals the stream from whoever holds it, and it mutates the
sensor's _persistent_ flash-backed state, which on a shared robot is a conversation, not
just a command. An earlier position in this doc's ancestor ("MUST NOT touch the Livox
config; always use the DDS republish") is superseded by that design; the constraint
survives as the caveat above, and the DDS republish remains the right source for anything
that must coexist with a vendor or colleague consumer.

### 4.6 Extrinsics, and the Mid360s hardware change

**Vendor-documented extrinsics** (the only measured values anywhere): **[web]**

- Mid-360 mounted **in the middle of the head**. IMU-to-lidar offset `(0.011, 0.02329,
−0.04412)` m with **no rotation**.
- Lidar relative to the robot frame: `(−0.0, 0.0, −0.47618)` m, _"placement method is
  inverted"_, pitch-axis inclination **−2.3°** — which matches the deployed
  `roll 180.0, pitch −2.3` extrinsic in §4.2 exactly. ⚠️ The z sign is wrong for a
  head-mounted sensor as literally stated, so **treat the direction of that transform as
  ambiguous and validate against a real cloud** before using it. It is still the only
  measured alternative to gemm's `z = 1.0` marked in-file _"APROXIMADA: medir en el
  robot"_ (§4.8).
- SLAM output frame: **X forward, Z up** (hence Y left if right-handed), origin at the
  Mid360-IMU.

⚠️ **Hardware change we sit on the wrong side of:** _"The lidar of G1 produced after
**April 2026** has been changed to '**Mid360s**'. For self-developed users, LIVOX official
Livox-SDK2 and livox_ros_driver2 need to be updated."_ **[web]** This robot arrived
**2026-08-04**, i.e. after the cutover, so it may carry a Mid360s and **any pinned
Livox-SDK2 / livox_ros_driver2 version may be too old.** It does not affect the DDS
republish path — but check the sensor model before pinning driver versions (§12 Q7).

Also worth recording, Unitree's own pre-use check: _"confirm there are no abnormalities in
the raw point clouds and IMU data, and the head is fixed vertically upward without
looseness. If the front and rear point cloud frames are obviously stratified when the lidar
illuminates the same static vertical plane in a static state, or the IMU values are
significantly abnormal, contact sales for lidar repair."_ **[web]**

### 4.7 Message layouts if we run our own driver

Selected by the ROS param `xfer_format` (`lddc.h:41-47`): `0` = `sensor_msgs/PointCloud2`,
`1` = `livox_ros_driver2/CustomMsg`, `2` = PCL XYZI; `3` is internal. Driver default in
code is `0`, but gemm's launch **overrides it to 1**. Topic is `/livox/lidar` either way
(`multi_topic` pinned to 0). IMU on `/livox/imu`, `sensor_msgs/Imu`, with **no rate control
at all** — one message per received packet, so the ROS rate equals the sensor's 200 Hz
push. **[src]**

`CustomMsg`: `header`, `uint64 timebase`, `uint32 point_num`, `uint8 lidar_id`,
`uint8[3] rsvd`, `CustomPoint[] points`, where `CustomPoint` = `uint32 offset_time` (ns
from timebase), `float32 x,y,z`, `uint8 reflectivity`, `uint8 tag`, `uint8 line`.

`PointCloud2` path: 7 fields, `point_step = sizeof(LivoxPointXyzrtlt)` — x/y/z FLOAT32 at
0/4/8, intensity FLOAT32 at 12, tag UINT8 at 16, line UINT8 at 17, timestamp FLOAT64 at
18; `height=1`, unorganised. `line` ranges 0..3 (`comm.h:82 kLineNumberMid360 = 4`) — that
4 is the value FAST-LIO-family configs want for `scan_line`/`N_SCANS`. Note the
`xfer_format=0` per-point `timestamp` is an **offset**, not absolute time, and FAST-LIO
wants CustomMsg while Nav2/RViz want PointCloud2 — one node instance cannot serve both.
**[web]**

Two ARM-specific community traps: **[web]**

- On ARM + ROS 2 + CycloneDDS, CustomMsg reportedly sticks at **~5 Hz instead of 10 Hz**
  (reproduced on Jetson Orin NX and RK3588); the reporter's fix was switching RMW to
  Zenoh — unavailable on our Foxy Jetson. Unconfirmed here; measure before trusting the
  10 Hz.
- The recurring `bind failed` / `Failed to init livox lidar sdk` folklore on Jetson ARM64
  converges on one cause: the `host_ip` in the JSON is not an address that exists on that
  machine (exactly the g1pilot failure in §4.2).

### 4.8 The inverted-mount IMU trap

The G1 mounts the Mid-360 **upside down**. The deployed extrinsic is `roll 180.0,
pitch -2.3, yaw 0.0`, and it is pushed **into the sensor** (`SetLivoxLidarInstallAttitude`,
register `0x0012`). Host-side compensation is off — `pub_handler.cpp:132` sets
`packet.extrinsic_enable = false` and the per-point extrinsic branches are gated on that
flag. **[src]**

**It is not applied to the IMU on either side.** With the sensor inverted, gravity reads
negative, `gravity_align_en` aligns against a flipped vector, and LIO diverges **without
throwing any error**. deepglint had to patch the driver for exactly this. The check gemm
prescribe, and it is the right one: with the robot standing still, look at the **sign of
`/livox/imu` linear_acceleration.z**. Related **[web]** claim worth testing at the same
time: the driver publishes IMU acceleration in **g, not m/s²** (no 9.8 factor anywhere;
upstream issue #157 open since 2024-12, a commenter confirms a level Mid-360 reads ≈1 on
Z).

Frame naming also splits: the driver publishes `livox_frame` while gemm's Nav2 expects
`lidar_link`, bridged by a separate `static_transform_publisher base_link → lidar_link`
with `z = 1.0` marked in-file as _"APROXIMADA: medir en el robot."_ No measured value
exists anywhere on the box. **[src]**

### 4.9 Sensor specs that shape any SLAM design

Published specs **[web]**: 905 nm, 40 m @10 % / 70 m @80 % reflectivity, 0.1 m blind zone,
360° × −7°…+52° FOV (asymmetric, biased **downward** once mounted inverted),
200 000 pts/s, 10 Hz, ICM40609 IMU, 6.5 W avg / 14 W peak, IP67. Two consequences: a
single 10 Hz frame is **sparse** — do not evaluate it as a 32-beam spinner, and prefer LIO
that integrates per-point timestamps over frame-wise ICP; and the 0.1 m blind zone plus
that FOV means the floor and the robot's own arms will appear, so self-filtering is
mandatory. Also **[web]**: the unit stops operating automatically above **~80 °C shell
temperature**, which would look exactly like a network fault.

---

## 5. Intel RealSense D435i

### 5.1 Attachment and node map

`8086:0b3a`, serial `255323064200`, USB path `2-2.3` (hub `2-2` = `0bda:0411` 4-port
USB 3.0), driver `uvcvideo`, kernel 5.10.104. It is the **only** camera physically attached
to this Jetson: `v4l2-ctl --list-devices` returns exactly two entries, the RealSense and
the Tegra CSI capture path (`/dev/media0`, **zero** `/dev/video` nodes bound — no MIPI/CSI
camera). **[live]**

**Officially confirmed as the G1's head sensor, and officially confirmed to have no DDS
path.** _"The G1 robot depth camera is located overhead and is a **realsense D435i** …
binocular infrared camera (global shutter), laser transmitter, RGB camera (rolling
shutter), and 6-axis IMU."_ **[web]** (`depth_camera_instruction`, 2025-11-17.) The entire
page covers only `realsense-viewer`, `realsense-ros` and librealsense — **it names no DDS
topic and no Unitree service.** That settles the asymmetry between the two head sensors:
**the LiDAR is republished on DDS and is multi-consumer (§4.5); the D435i is not, and
stays a single-owner V4L2/USB device.** Any C3PO vision source must negotiate with whoever
holds the node, not expect a topic.

Vendor reference configuration worth matching (**[web]**): depth `640×480 Z16 @60`, both
IR `640×480 Y8 @60`, **colour `848×480 BGR8 @60`** (note 848, not 640), gyro 400 Hz, accel
200 Hz; stereo `VISUAL_PRESET = HIGH_ACCURACY`, auto-exposure on, emitter on, laser power
150; temporal filter with `HOLES_FILL 6`, `SMOOTH_ALPHA 0.4`, `SMOOTH_DELTA 20`. And a
timestamping rule stated as a `// do not use` comment against `frame.get_timestamp()`:
**the vendor uses `clock_gettime(CLOCK_REALTIME)` instead** — which is precisely why §11's
time-sync question matters if we ever fuse frames with DDS state. USB 2.0 needs a hub
adapter and degrades resolution/frame rate. (Intel's own D400 datasheet is 403-blocked from
intel.com and Mouser, so quoted depth specs online are unconfirmed reseller repetition —
`rs-enumerate-devices` against the actual camera is the better source.)

The six nodes resolve unambiguously by USB interface: **[live]**

| Node          | USB iface   | Role                      | Formats                       |
| ------------- | ----------- | ------------------------- | ----------------------------- |
| `/dev/video0` | `2-2.3:1.0` | Depth capture             | `Z16`                         |
| `/dev/video1` | `2-2.3:1.0` | Depth metadata            | —                             |
| `/dev/video2` | `2-2.3:1.0` | Infrared / stereo capture | `GREY`, `UYVY`, `Y8I`, `Y12I` |
| `/dev/video3` | `2-2.3:1.0` | IR metadata               | —                             |
| `/dev/video4` | `2-2.3:1.3` | **Colour capture**        | `YUYV`, `BYR2`                |
| `/dev/video5` | `2-2.3:1.3` | Colour metadata           | —                             |

Resolutions, from `--list-formats-ext`: **[live]**

- **Depth `Z16`** — 256×144 @300/90; 424×240, 480×270, 640×360, 640×480, 848×480
  @90/60/30/15/6; 848×100 @300/100; 1280×720 @30/15/6. Depth at 720p tops out at 30 fps.
- **IR** — same ladder for `GREY`/`UYVY`/`Y8I`; `Y8I` adds 1280×800 @30/15 (native stereo
  resolution); `Y12I` only 640×400 and 1280×800 @25/15. `Y8I`/`Y12I` are **interleaved
  left+right** — librealsense splits them; there is no second node for the right imager.
- **Colour `YUYV`** — 320×180 up to **1920×1080 @30/15/6**. `BYR2` (Bayer, experimental)
  at 1920×1080 @30 only. This is the node every consumer on this robot fights over.

### 5.2 The IMU is not a V4L2 device

The `i` variant is confirmed: two IIO devices hang off the RealSense HID interface
`2-2.3:1.5` / `0003:8086:0B3A.0001` — `iio:device0` = `accel_3d`, `iio:device1` =
`gyro_3d`, each with its own trigger. `in_accel_scale = 0.009806650` (m/s² per LSB, i.e.
g/1000); `in_anglvel_scale = 0.001745329` (rad/s per LSB = exactly 0.1 °/s). Idle sampling
frequencies read accel 10 Hz, gyro 0 Hz, and neither device exposes
`sampling_frequency_available`, so the selectable rate list cannot be read from sysfs.
**Those are idle HID defaults, not what librealsense would configure** — treat them as
**[?]**. **[live]**

The useful consequence: the IMU surfaces through **HID/IIO, not a `/dev/video` node**, so
an IMU-only consumer does not contend for V4L2 at all. **[live]** + **[web]** The sensor
itself is a Bosch BMI055, **not factory-calibrated** (non-zero angular velocity at idle,
gravity ≠ 9.80665), with a depth-to-IMU extrinsic that is precalculated and cannot be
modified. **[web]**

### 5.3 Exclusivity, and who held it

`/dev/videoN` is a single-owner kernel resource — but **per node, not per camera**. At the
snapshot, `lsof /dev/video*` returned exactly one holder, and only on `video4`: **[live]**

```
python  5850  unitree  15u  CHR  81,4  /dev/video4
```

PID 5850 = `…/xr_teleoperate/envs/tv/bin/python -u -m teleimager.image_server`, PPID
5848 = `…/xr_teleoperate/scripts/_image_service_watchdog.sh` (itself PPID 1,
setsid-detached), cgroup `user.slice/user-1000.slice/session-27.scope` — **a
human-launched SSH-session process, not a service**. See §10.3.

**`/dev/video0` (depth) and `/dev/video2` (IR) had no holder at all.** That is our
opening: Intel's own doc says multiple librealsense clients can coexist _"as long as no two
users try to stream from the same camera endpoint"_, with Depth, Colour and Motion as
independent endpoints **[web]** (against widespread forum wisdom that "only one process can
use a RealSense at a time"). So C3PO may be able to take depth without disturbing anyone —
but the same doc is RS400-era and does not say whether D435i depth and IR share an
endpoint. **Untested.** A collision looks like
`xioctl(VIDIOC_S_FMT) failed … Device or resource busy`.

### 5.4 What is actually streaming today: teleimager

`teleimager.image_server` is the **only live camera feed on the robot**: JPEG frames over
a ZeroMQ `PUB` socket, plus a config REQ/REP socket. **[live]**

| Property    | Value                                                       |
| ----------- | ----------------------------------------------------------- |
| Image PUB   | `tcp://0.0.0.0:55555`                                       |
| Config REP  | `tcp://0.0.0.0:60000`                                       |
| Wire format | JPEG bytes — `cv2.imencode(".jpg", bgr_numpy)` then publish |
| Geometry    | 540×960 @ 30 fps, monocular, `type: opencv`, `video_id: 4`  |

**It binds `0.0.0.0`, not `127.0.0.1` as its own config comment claims.** That makes it a
plain TCP feed reachable from the Mac over Wi-Fi — unlike every DDS multicast path, which
needs the wired robot LAN (§1). If we ever need a camera on the Mac in a hurry, this is
the one transport that already crosses that boundary. **[live]**

Two teleimager details worth knowing before reusing it: its `type: uvc` branch **silently
ignores `video_id`** (it resolves only via `physical_path` or `serial_number`, with no
fallback), so a uvc-typed camera configured with only `video_id` is never constructed and
the server runs publishing nothing, with no fatal error — hence the working config uses
`type: opencv`. It also ships a native `type: realsense` driver (`--rs` flag + serial
number) that can emit **depth** over ZMQ, which is the shortest path to depth-over-TCP if
we want it. **[src]** (Its shipped default YAML describes a `type: uvc` binocular
`[480,1280]` camera; maintainer replies confirm the head camera is in fact the D435i —
consistent with §6.1. **[web]**)

The colleague's ROS node is a different consumer of the same node: `gemm-bringup`'s
`realsense2_camera_node`. The container is `--network host`, `Privileged=true`,
bind-mounts all of `/dev:/dev`, and has `restart=unless-stopped` — so it **will** reclaim
`/dev/video4` on the next docker daemon restart or reboot and fight teleimager's watchdog.
Its live profile is deliberately colour-only (`enable_depth=false`,
`align_depth.enable=false`, `enable_gyro/accel=false`), contract topic
`/camera/camera/color/image_raw/compressed`; depth, aligned depth and a fused IMU
(`unite_imu_method=2`) appear only in `record.launch.py`. **[live]** + **[src]**

### 5.5 Jetson-specific pitfalls

- **`pyrealsense2` is a per-interpreter problem, not an impossibility.** The on-robot
  comment claims the aarch64 PyPI wheel needs GLIBC ≥ 2.32 and this Jetson has 2.31. The
  GLIBC part is true (`ldd 2.31-0ubuntu9.16`), the conclusion is too broad:
  `pyrealsense2 2.55.1.6486` **is** installed under `gemm_ai`'s Python 3.8 venv and
  imports successfully, and native `librealsense 2.50.0` is present from ROS Noetic at
  `/opt/ros/noetic/lib/aarch64-linux-gnu/librealsense2.so.2.50.0`. What is missing is a
  wheel for the teleop py3.10 venv and for **our bridge's py3.12** venv. A working aarch64
  binding exists on the box to copy the approach from. **[live]** (The deeper wheel/backend
  analysis for the perception containers lives with the perception stack —
  `apps/perception/README.md`.)
- **Node numbers are enumeration-order dependent — use the stable symlinks.**
  `/dev/v4l/by-path/` gives `platform-3610000.xhci-usb-0:2.3:1.0-video-index0` → depth,
  `…:1.0-video-index2` → IR, `…:1.3-video-index0` → colour; `/dev/v4l/by-id/` keys on the
  serial. This solves the "video_id may shift after reboot" problem the colleague
  documents. **[live]**
- **Permissions are already fine.** `crw-rw-rw-+ root:plugdev` on all six nodes, from
  `/lib/udev/rules.d/60-ros-noetic-realsense2-camera.rules` (`idProduct=="0b3a"`,
  `MODE:="0666"`, `GROUP:="plugdev"`); `unitree` is in `video` and `plugdev`. teleimager's
  `setup_uvc.sh` was **never run** here (`/etc/udev/rules.d/10-libuvc.rules` absent,
  `/etc/sudoers.d/` holds only `README`), which is why its log shows
  `sudo modprobe -r uvcvideo` failing with _"a terminal is required to read the
  password"_. That failure is benign. **[live]**
- **No calibration files exist on disk, and that is correct.** No `camera_info` YAML, no
  intrinsics or extrinsics, no depth-to-colour alignment table. The D435i stores
  intrinsics, stereo extrinsics, depth scale and IMU calibration in on-camera flash;
  librealsense reads them at runtime. Consequence: **any consumer wanting aligned depth
  must go through librealsense/realsense2_camera or read intrinsics off the device — they
  cannot be picked up from a file.** The only calibration JSON on the box is a D500-series
  _example_, not applicable. **[live]**
- **[web] risks not yet tested here:** RSUSB vs V4L-native backend on Jetson (verified
  boards are AGX-class; Orin **NX** is not on the list, and an OTA that bumps the L4T
  kernel silently unloads a hand-patched module); and `librealsense` moved org to
  `realsenseai/`, dropping the "Intel" prefix from device names, so code matching
  `"Intel RealSense D435I"` may break across an upgrade.

---

## 6. The G1's own cameras via `video_hub_pc4` — degraded

### 6.1 The correction that matters most: the head camera is the D435i

Earlier inventories listed "Intel RealSense D435i" and "G1 head/chest cameras" as separate
peripherals. **For the head, on this unit, that is wrong.**
`/unitree/etc/master_service/service/video_hub_pc4` `Start.Cmd`, verbatim: **[src]**

```
export CYCLONEDDS_URI=/unitree/module/video_hub_pc4/cyclonedds.xml;
/unitree/sbin/start-stop-daemon --start --background --make-pidfile \
  --pidfile=/unitree/var/run/videohub_pc4.pid \
  --exec /unitree/module/video_hub_pc4/videohub_pc4 /dev/video4
```

The camera argument is literally `/dev/video4` — the D435i colour node — and the binary
embeds the same string as its default. This matches the Weston Robot note that later G1
batches connect the D435i to the development computer.

**Consequence: three independent consumers target one V4L2 node** — vendor `videohub_pc4`,
the colleague's `realsense2_camera_node`, and `teleimager`. Only one can win, and there is
no arbitration beyond `open()` returning EBUSY.

### 6.2 What is broken, and how far the root cause was established

**Head node — cause fully established. [live]** `videohub_pc4` is not running because
`master_service.service` was `inactive (dead) since Fri 2026-08-14 01:40:34 CST`, with
`Process: 4600 ExecStop=/etc/init.d/master_service stop (code=exited, status=0/SUCCESS)`
and journal lines `Stopping LSB: master service init script…` / `master_service.service:
Succeeded.` That is a clean, deliberate `systemctl stop`, **not a crash and not an OOM**.
It is an LSB init script wrapped by `systemd-sysv-generator` with `Restart=no` and
`RemainAfterExit=yes`, so it will **not** self-recover — but it does start at boot, and
did (01:34:49). Timeline: 01:34 boot → both videohubs start → heartbeats every 5 s →
01:40:34 stopped → 01:42/01:43 teleimager takes `/dev/video4`.

Who stopped it and why is documented, and it is the sanctioned workaround: the colleague's
`xr_teleoperate/scripts/start_image_service.sh` prints, verbatim, _"Si es 'videohub_pc4'
(servicio propio de Unitree), pararlo con: sudo systemctl stop master_service"_ — the same
thing a Unitree maintainer sanctions **[web]**. So a human ran exactly that to free the
camera. **[src]**

**Chest node — root cause NOT established.** `video_hub_pc4_chest` is configured for
`/dev/video10`, which does not exist. What we established: **[live]**

- The config is **stock and unmodified** — `module.json` version 1.0.2.3, commit
  `1899ba6f9237dd2c323d5feb9877bb540e57ca61`, all files dated 2025-04-30, installed
  2025-05-19. No recent OTA touched it. The chest node is doing exactly what Unitree ships
  it to do.
- The highest `/dev/video` node on this box is `video5`; all six belong to the one
  RealSense; `/dev/v4l/by-id` and `by-path` list only RealSense entries; both USB hubs
  enumerate healthily **with free ports**. **There is simply no second UVC camera
  presenting to this Jetson.**
- This **refutes** the earlier web-sourced hub-event hypothesis (_"all G1 cameras hang off
  one USB-C hub in the neck, so a hub-level event takes out several `/dev/video` nodes at
  once"_): if a shared neck hub had dropped, the RealSense on `2-2.3` would have gone with
  it, and it did not.

What we could **not** establish is whether a chest camera was ever fitted, or is unplugged,
or is dead. That needs the kernel's enumeration history, and `dmesg` is root-restricted
here (`/proc/sys/kernel/dmesg_restrict=1`, `sudo` needs a password). **Do not write "the
chest camera failed" anywhere — the honest statement is "no second camera is electrically
present, cause unknown."**

### 6.3 The trap: "alive" never meant "producing frames"

`master_service` reported the chest service **alive** for its entire 6-minute life —
heartbeats `child service is alive, name:video_hub_pc4_chest` every 5 s — while
`/dev/video10` did not exist. Two reasons: `start-stop-daemon --status` only checks
pidfile + exec path, and the binary contains a retry loop with the strings
`check video device loop` / `video device not work, wait 30 seconds, and then try again` /
`video device works normal`. **[live]**

You will see a healthy service in the supervisor log and conclude the chest camera works.
It does not, and it never did on this boot.

### 6.4 What the vendor path would give us, if restarted

`strings` on `/unitree/module/video_hub_pc4/videohub_pc4` (leaked source path
`/home/unitree/sjy/g1_videohub_nx/videohub_pc4.c`): **[src]**

| Mechanism         | Detail                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------- |
| DDS stream topic  | `rt/frontvideostream`, type `unitree_go::msg::dds_::Go2FrontVideoData_`                           |
| DDS request/reply | `rt/api/videohub/request` / `…/response` (+ internal `rt/videohub/inner`), `Request_`/`Response_` |
| RTP multicast     | `…rtph264pay ! udpsink host=230.1.1.1 port=1720 multicast-iface=eth0 sync=false`                  |

(This corrects the earlier web-research claim that the G1 has "no published DDS image
topic"; it is right only about RTSP.) The GStreamer pipeline takes 1920×1080 YUY2 @15 fps
and produces three outputs, all NVENC-accelerated: **720p H.264 @8 Mbps**, **1080p JPEG**,
**360p H.264 @800 kbps**.

The chest binary is a reduced variant: its API topics are
`rt/api/videohub_chest/{request,response}`, its pipeline is JPEG-only, and it has **no
`rt/frontvideostream` writer** — so the chest camera would only ever have been reachable as
a request/response snapshot, never as a continuous stream. **[src]**

**The client already exists in our bridge venv.** `unitree_sdk2py/go2/video/video_api.py`
defines `VIDEO_SERVICE_NAME = "videohub"`, `VIDEO_API_VERSION = "1.0.0.1"`,
`VIDEO_API_ID_GETIMAGESAMPLE = 1001`, and `VideoClient.GetImageSample()` calls
`_CallBinary(1001, [])`. The service name maps exactly onto the binary's
`rt/api/videohub/request|response`, so this Go2-labelled client **should** address the G1
head videohub unchanged; a chest client would need service name `videohub_chest`. Untested
— the service is stopped and an RPC is a write. **[src]**

One mismatch to expect: the shipped `Go2FrontVideoData_` IDL is
`{time_frame: uint64, video720p, video360p, video180p}`, but the G1 pipeline has **no 180p
appsink** — so `video180p` is presumably always empty. **[?]**

### 6.5 Two operational hazards from this module

**An unowned shared dependency.** `videohub_pc4` embeds the rpath
`/home/unitree/cyclonedds_ws/install/cyclonedds/lib` and links `libddsc.so.0` — the _same_
third-party CycloneDDS 0.10.2 tree in a user home directory that our `CYCLONEDDS_HOME`
points at (§2). Deleting or moving that directory breaks a **root-owned vendor firmware
service**, not just C3PO. Its config `/unitree/module/video_hub_pc4/cyclonedds.xml` is, in
full: `<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eth0"
priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>` —
which independently corroborates our `DDS_INTERFACE=eth0` + 0.10.2-schema decision: the
vendor pins `eth0` too. **[src]**

**It comes back on reboot and will fight.** `master_service` starts at boot and re-grabs
`/dev/video4`; teleimager's watchdog respawns up to 20 times, 3 s apart. Whoever wins is a
race. Service control is `/unitree/sbin/mscli` (`startservice`, `stopservice`,
`restartservice`, `listservice`, `getservice`, `reloadservice`, `removeservice`), with
definitions in `/unitree/etc/master_service/service/` — only three exist: `ota_pipe`,
`video_hub_pc4`, `video_hub_pc4_chest`. `mscli` needs root. **[src]**

Side effect worth knowing: `/unitree/etc/master_service/cmd/am-init` is
`/usr/bin/amixer set Speaker 75%`, so the Jetson's boot-time speaker volume is set by
`master_service` — while it is dead, that has not been applied. **[src]**

---

## 7. Hands — **two BrainCo**, settled by inspection

**RESOLVED 2026-08-20, by the operator looking at the robot** — the observation §7.3 named
as the only one that could settle it. **Two BrainCo hands are fitted.** One was
**physically disconnected** at the time of the 2026-08-15 probe, which is what produced
the single right-hand answer and the idle `/dev/ttyUSB0/2/3` that the Dex3-vs-BrainCo
argument below was built on. **[live]**

Not Dex3-1. Not Inspire. The sections that follow are kept because the reasoning in them
is still worth reading — §7.4 in particular is a Dex3 reference that now applies to no
hand on this machine, and is retained only so nobody re-derives it if the hands are ever
swapped. **Where a section says "if that is what is fitted", it is not.**

What this changes immediately: hand units are BrainCo's **[0,1]** normalised range, not
Dex3 radians, and there is no Dex3 `timeout` deadman bit to set (§7.6). Any hand skill
written against §7.4 would have been wrong in units and in protocol.

**2026-08-15 probe, now explained:** the zero-write probe (`docs/ROBOT-API.md` §12) ran a
6 s passive subscribe on `rt/dex3/{left,right}/state` **and**
`rt/lf/dex3/{left,right}/state` and got **nothing**, with the FT4232H present and
`/dev/ttyUSB0–3` idle. **[live]** The Dex3 silence was correct — there are no Dex3 hands.
The idle ports and the missing left hand were **one hand being unplugged**, not evidence
about which hand family is fitted. Worth keeping as a lesson: the probe's negative result
was read as weak evidence for BrainCo when it was actually strong evidence of a
disconnected cable, and no amount of further DDS probing would have distinguished those. One correction to that session's record: it concluded the BrainCo state topic
could not be checked because `MotorStates_` "is a type this SDK does not ship" — **wrong**:
`unitree_go::msg::dds_::MotorStates_` _is_ shipped in our bridge venv, so subscribing
`rt/brainco/{left,right}/state` is possible and remains the missing half of the probe
(§7.3).

### 7.0 The vendor's G1-EDU variant → hand table

From `get_sdk` (vendor-updated 2026-07-20), verbatim apart from the vendor's own typos
("Andvanced", "DFQ" for the RH56DFX): **[web]**

| Variant               | Configuration                                               |
| --------------------- | ----------------------------------------------------------- |
| G1-EDU **Standard**   | 23 DoF, **no hands**                                        |
| G1-EDU **Advanced**   | 29 DoF, **no hands**                                        |
| G1-EDU **Ultimate A** | 29 DoF + 2 × **Dex3-1** three-finger hands (**no** tactile) |
| G1-EDU **Ultimate B** | 29 DoF + 2 × **Dex3-1** (**tactile included**)              |
| G1-EDU **Ultimate C** | 29 DoF + 2 × **Inspire DFX** (RH56DFX)                      |
| G1-EDU **Ultimate D** | 29 DoF + 2 × **Inspire FTP**                                |

**BrainCo appears in no factory variant at all.** That reframes the whole argument: a
running `brainco_hand_server` is evidence of a **retrofit**, not of the factory build —
which fits with it living under `xr_teleoperate/vendor/`, a third-party stack a colleague
installed. The `brainco_hand` page (2026-03-25) reinforces the asymmetry: BrainCo is a
build-it-yourself GitHub service you `cmake` and launch with `sudo` and an explicit
`--serial`, whereas _"G1 internally provides a **resident service program** that
communicates with Dex3-1 and converts to DDS messages."_

Two more constraints narrow it further:

- **`mode_machine = 5` = 29 DoF** (`docs/ROBOT-API.md` §4.2). **[live]** + **[web]** So
  this unit is **not** G1-EDU Standard — it is Advanced or one of the four Ultimates.
- `about_G1`'s maximum-configuration arithmetic is built on a **Dex3-1 pair**: 23 base + 2
  waist + 2×2 wrist = 29, then 29 + 2×7 = **43 DoF**. No 6-DoF hand (BrainCo Revo2 or
  either Inspire) reaches 43 — those give 41. **[web]** So if this unit's paperwork ever
  states 43 DoF, that is a Dex3 pair. (Our OTA package name gives the product string
  `G1_Edu+` with no DoF count — see `docs/ROBOT-API.md`'s legend — and that string maps to
  **none** of Standard / Advanced / Ultimate A–D.)

### 7.1 The case for BrainCo — live process evidence

`brainco_hand_server` (pid 5923, started 01:43, ~8 % CPU sustained,
`--network_interface eth0`), from `…/xr_teleoperate/vendor/brainco_hand_service/bin/`,
holding `/dev/ttyUSB1` (fd 12 in `/proc/5923/fd`). Its log records the full probe and
bind: **[live]**

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
answered this probe** — right, medium, Modbus RTU slave `0x7f` at **460800 baud**, polled
at 100 Hz. **No left hand answered on any port** — which, per §7.3, is not the same as
"nothing is attached there".

**Two vendor confirmations, and one thing they change.** `brainco_hand` documents the
launch as `sudo ./brainco_hand --id 126 --serial /dev/ttyUSB0 # 126: left hand, 127: right
hand` **[web]** — so our live log's "Starting worker for right (slave 127)" / slave `0x7f`
reading is exactly right. But note the second half: **the service is started by hand with
an explicit `--serial` per hand.** "No left hand answered" therefore reflects _how it was
launched_ as much as what is plugged in — whoever started it chose one port.

Its interface is nothing like Dex3's: **[src]**

| Property    | Value                                                                              |
| ----------- | ---------------------------------------------------------------------------------- |
| Topics      | `rt/brainco/{left,right}/cmd`, `rt/brainco/{left,right}/state`                     |
| Types       | `unitree_go::msg::dds_::MotorCmds_` / `MotorStates_` (bare sequences)              |
| Entries     | **6**, order `[Thumb, Thumb_aux, Index, Middle, Ring, Pinky]`                      |
| Cmd scale   | `positions[i] = clamp(q, 0, 1) × 1000`, `speeds[i] = clamp(dq, 0, 1) × 1000`       |
| State scale | `q = positions/1000`, `dq = speeds/1000`, `tau_est = currents/1000` (amps)         |
| Ignored     | `kp`, `kd`, `tau` — the fields exist in the message, the server does not read them |

Positions and speeds are **normalised to [0,1]**, not radians. Their README recommends
setting all finger speeds to 1.0.

**Unitree's own page confirms this line for line** — topics, the `[0,1]` normalisation,
the speed-1.0 recommendation, the 6-DoF count and the exact finger order. **[web]** What
it never states: the **DDS message type**, the baud rate, or anything about force or
tactile sensing. So the `MotorCmds_`/`MotorStates_` typing and the ×1000 wire scaling stay
**[src]**-only from the server binary's source, and our 460800 baud / slave `0x7f` stay
live-only. ⚠️ **And it never says which end of [0,1] is _open_** — Inspire DFX explicitly
maps 1.0 = open, `hand_sdk` says positive tau closes, and BrainCo says nothing. **Do not
write a BrainCo "open hand" preset until that is read out of the server source or
observed.**

### 7.2 The case for Dex3-1 — configuration and bus evidence

- The FT4232H is present and healthy: `0403:6011`, `bNumInterfaces = 4`, device serial
  `FTA9IWAI`, `ftdi_sio 1-2.2:1.0..1.3` → `/dev/ttyUSB0..3`, all four present as
  `crw-rw---- root:dialout 188,0..3`. That is exactly the bus a Dex3 **pair** would sit
  on. **[live]**
- `g1pilot` ships a URDF for _this_ robot named `g1_29dof_dx3.urdf` — "dx3" = Dex3. **[src]**
- `xr_teleoperate`'s assets include `g1_body29_hand14.urdf` — 29 body DoF + 2 × 7 hand
  DoF, i.e. a **two-Dex3** configuration. **[src]**
- `xr_teleoperate` ships a working `Dex3_1_Controller` (100 Hz, `kp=1.5 kd=0.2`, XR
  retargeting through dex-retargeting DexPilot), and both SDKs ship `g1_dex3_example`
  binaries. **[src]**

But: a filesystem-wide search for any `*dex3*` artifact outside vendored SDK source trees
returns **nothing** — no Dex3 service binary, no systemd unit, no `/unitree/module` entry.
**[live]**

⚠️ **The conclusion once drawn from that had to be withdrawn.** An earlier version of this
survey read _"even if a Dex3 were plugged in, nothing on this Jetson would publish
`rt/dex3/_/state`"* and used it as evidence against Dex3. Unitree states plainly:
*"**Unitree does not deploy services on the NVIDIA Jetson Orin module.**"* **[web]** The
Dex3 driver is *"a resident service program"* the robot provides itself — i.e. it runs on
the control board at `192.168.123.161`, which has no SSH. **The search was aimed at the
wrong host.** The observation stands; the inference does not. (The 2026-08-15 silent-topic
probe above is the _right_-host version of the same test, and it too came back empty.)

The same page also weakens the bus argument in the other direction: **no vendor page
associates the Dex3-1 with a PC2 USB-serial dongle.** The two pages that _do_ describe
USB-serial dongles on PC2 are the **Inspire** ones, and BrainCo documents one USB-serial
device per hand. So the FT4232H's four ports fit "two retrofit third-party hands" at least
as well as "a Dex3 pair". **[web]**

### 7.3 Why the evidence cannot settle it, and the probes that can

The BrainCo probe speaks **only** Modbus RTU at 460800 baud. A Dex3 or Inspire hand would
not answer that probe. So _"no left hand answered on any port"_ is **not** evidence that
nothing is attached to the left wrist, and ttyUSB0/2/3 sitting idle is equally consistent
with "nothing there" and "something there that does not speak BrainCo".

**The single observation that settles it: look at the robot.** ✅ **Done 2026-08-20 — two
BrainCo (§7).** Retained because the discriminator is worth keeping: a Dex3-1 is a
**7-DoF, three-finger** hand (thumb ×3, index ×2, middle ×2); a BrainCo Revo2 is **6-DoF,
five-finger**; an Inspire DFX or FTP is **6 DoF, five-finger** too — so five fingers
narrows to {BrainCo, Inspire} and does not settle it alone. Second best, and still the
right move after any re-cabling: trace which FT4232H channel each physical wrist lands on.

**Do not settle it by opening the serial ports.** That means driving an RS485 bus attached
to an unknown device on a powered, standing robot.

The zero-write DDS probe table (passively subscribe for a few seconds):

| Topic                                       | Type                                  | In our venv?                    | Proves                                                                                           |
| ------------------------------------------- | ------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------ |
| `rt/dex3/left/state`, `rt/dex3/right/state` | `unitree_hg::msg::dds_::HandState_`   | **yes**                         | Ultimate A/B — a Dex3 pair, resident service on the control board. **Probed 2026-08-15: silent** |
| `rt/inspire/state`                          | `unitree_go::msg::dds_::MotorStates_` | **yes**                         | Ultimate C — Inspire DFX (12 entries, both hands)                                                |
| `rt/inspire_hand/state/r`                   | `inspire::inspire_hand_state`         | **no** — needs hand-written IDL | Ultimate D — Inspire FTP                                                                         |
| `rt/brainco/{left,right}/state`             | `unitree_go::msg::dds_::MotorStates_` | **yes**                         | The retrofit BrainCo, if its server is running                                                   |

⚠️ **Settle this _before_ the next locomotion window, not after.** Unitree's own warning:
_"During development, it is not recommended to perform overly intense actions, such as
**running or balance tests**, while the dexterous hand is attached"_, plus _"when using the
dexterous hand, always ensure that the robot's movements do not cause interference between
the hand and the main body"_ and a separate warning against booting in the lying or
squatting positions with hands fitted. **[web]**

### 7.4 Dex3-1 reference, if that is what is fitted

Topics, the `HandCmd_`/`HandState_` layouts and the tactile struct are catalogued in
`docs/ROBOT-API.md` (hands section and topic census). All **[src]**, correct for the
product Unitree ships:

| Direction  | Topic                            | Type                                                  |
| ---------- | -------------------------------- | ----------------------------------------------------- |
| Command    | `rt/dex3/{left,right}/cmd`       | `unitree_hg::msg::dds_::HandCmd_`                     |
| State      | **`rt/dex3/{left,right}/state`** | `unitree_hg::msg::dds_::HandState_`                   |
| State (lf) | `rt/lf/dex3/{left,right}/state`  | same — decimated mirror (vendor-confirmed convention) |

`rt/dex3/*/cmd` is unanimous across all three vendor clients and both official prose
pages. **Subscribe the bare state name** — it is what both official prose pages use
exclusively. (Unitree's topic table scrambles the Info column for the two _left_-hand rows
— lf/non-lf labels swapped, `rt/dex3/right/state` described as "left" — read the
convention, not the individual cells. **[web]**)

Facts recorded here because they exist nowhere in the API doc:

- **Struct-source trap.** Our venv's IDL and the older `dexterous_hand` page (2025-02-10)
  match field for field; the newer `basic_services_interface` (2025-10-21) publishes a
  _different, shorter_ `HandState_` with `press_sensor_state` and `imu_state` **swapped**
  and `system_v`, `device_v` and `error[2]` missing, and a `HandCmd_` without the
  `reserve[4]`. **Ignore it** — a swapped struct in DDS does not error, it silently
  mis-decodes. General lesson for this whole doc set: **recency is not a proxy for
  correctness on struct layouts.** **[web]** Field meanings worth having: `power_v`/
  `power_a` are the hand's total input supply voltage/current, `system_v` its internal
  supply, `device_v` its step-down output, `error` its error code — a fitted Dex3 gives
  per-hand health telemetry we have no equivalent of anywhere else.
- **Joint order, and what caused the historical contradiction.** Two official pages give
  the identical IDL order for **both** hands: slots 0–6 = `thumb_0, thumb_1, thumb_2,
middle_0, middle_1, index_0, index_1` — **slot 3 is `middle_0` on both hands**. The trap
  that produced the disagreement (xr_teleoperate said Index0/Index1 on the right): **the
  left hand's URDF link names run out of numeric order** — `left_hand_five` → IDL 3,
  `left_hand_six` → IDL 4, `left_hand_three` → IDL 5, `left_hand_four` → IDL 6 — so anyone
  converting URDF indices to IDL indices by sorting names swaps index and middle. Adopt
  the IDL order; verify one slot at a time on real hardware first. **[web]**
- **Units: radians — the number-one mixing hazard across the four hand types.**
  `MotorState_.q` is documented as joint position in **rad**; the vendor's own Dex3
  example normalises to [0,1] **only for printing**. **[web]**

  > **Dex3 = radians. BrainCo = [0,1]. Inspire DFX = [0,1] with 1.0 = open.** A
  > `set_hand_pose(side, q[7])` written for Dex3 and pointed at a BrainCo topic sends 1.7
  > where the driver expects ≤ 1.0; the reverse sends 0.5 rad where 90° was meant.

  (The vendor example's `maxTorqueLimits`/`minTorqueLimits` variables are misleadingly
  named — they hold joint **position** limits, per side.)

- **Per-motor `mode` byte is bit-packed:** `RIS_Mode_t { id:4, status:3, timeout:1 }`,
  i.e. `mode = (id & 0x0F) | ((status & 0x07) << 4) | ((timeout & 0x01) << 7)` —
  confirmed verbatim by Unitree, `status` 0 = Lock, 1 = FOC, and _"timeout: … 1 = enable
  (**default 1 s timeout**)"_. So `timeout = 1` is a **firmware-side 1 s deadman on the
  hand motors** — the same free-safety pattern as `SetVelocity`'s `duration`
  (`docs/ROBOT-API.md` §5) and `rt/hand_sdk`'s auto-fallback. **Any hand skill should set
  `timeout = 1` and re-publish, never `timeout = 0`.** Note the same
  `unitree_hg::msg::dds_::MotorCmd_` struct carries a completely different `mode` encoding
  in a `LowCmd_` (0 = Disable, 1 = Enable) than in a `HandCmd_`. **[web]**
- ⚠️ **TRAP: the `dexterous_hand` page splices the RS485 wire protocol into a DDS
  document.** Under a heading reading "IDL Data Format" it shows a 20-byte packed struct
  with `head[2]`, `CRC32`, and integer scale factors — _"`tor_des`: 256 represents 1 mNm;
  `pos_des`: 32768/2π represents 1 rad; `k_pos`: 1280 represents 1 mN·m/rad"_ — and a
  `uint16`-based tactile struct. **None of that is the DDS type.** The real `MotorCmd_` is
  `{uint8 mode; float32 q, dq, tau, kp, kd; uint32 reserve}`, all plain floats in SI, and
  the real `PressSensorState_` is `{float32 pressure[12]; float32 temperature[12];
uint32 lost; uint32 reserve}` — as shipped in our venv. Those scale factors describe
  what the **resident service** speaks to the hand over RS485, downstream of DDS. Anyone
  implementing from that page will scale `q` by 32768/2π into an integer field that does
  not exist and drive the hand to a wildly wrong position. (Internal proof it is the wrong
  struct: the page's own stated sensor values, "valid when data ≥ 100000", do not fit a
  `uint16`.)
- **Use the URDF joint limits, not the examples' hard-coded clamps** — the examples exceed
  the URDF on `thumb_1` for both hands (left max 1.05 vs URDF 0.920; right min −1.05 vs
  −0.920). Left joints 3–6 are negative-only and right joints 3–6 positive-only, so a
  shared "close the hand" pose must be sign-flipped per side. The spec sheet explains the
  discrepancy: `about_G1` gives the thumb as `0°…+100°, −35°…+60°, −60°…+60°` and
  index/middle as `0°…+90°` and `0°…+100°` — the examples' symmetric ±1.05 is exactly the
  spec's +60° = 1.0472 rad, but the spec range is **asymmetric** (−35°/+60°), so the
  examples are wrong on the negative side regardless. **The URDF limits are the
  conservative ones; use them.** The page never says which index/middle range is the
  proximal `_0` and which the distal `_1`, so half that table is still ambiguous. **[web]**
- **Tactile semantics:** treat `30000` as no-reading, `≥ 100000` as valid, divide by
  10000 for display (_"recommended to scale down 100000 to 10.0000"_). Geometry is
  contested: the hand page says _"a 3×4 array sensor at each fingertip position (6
  locations in total)"_ vs `about_G1`'s "9 array sensors" (six fingertips plus three palm
  pads would reconcile them, but no source says so). The pad→finger map exists **only** in
  two images the vendor docs' markdown conversion dropped
  (`doc-cdn.unitree.com/static/2024/12/26/…_5000x2812.png`) — fetch those if tactile ever
  matters. **[web]** Other spec-sheet figures: RS485 control interface, 7 DoF, 24 V rated
  ("operating voltage 12–58 V" in the electrical table), tactile perception range
  10 g – 2500 g.

### 7.5 The other two hand families, and `rt/hand_sdk`

Relevant mainly as **discriminators** for §7.3, and as a warning about how differently
these four products behave.

**"Inspire hand" is two incompatible products in Unitree's own docs.** **[web]**

|             | **Inspire DFX** (RH56DFX, Ultimate C)                                | **Inspire FTP** (Ultimate D)                                                                                                                                                                   |
| ----------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Topics      | `rt/inspire/{cmd,state}`                                             | `rt/inspire_hand/{ctrl,state,touch}/{r,l}` (`*` defaults to `r`)                                                                                                                               |
| Types       | `unitree_go::msg::dds_::MotorCmds_` / `MotorStates_`                 | custom `inspire::inspire_hand_{ctrl,state,touch}` — **not in `unitree_sdk2py`**, would need hand-written IdlStructs                                                                            |
| Entries     | **12 — BOTH hands in one message, right occupies 0–5**, left 6–11    | **6 — one hand per topic suffix**                                                                                                                                                              |
| Joint order | `[pinky, ring, middle, index, thumb_bend, thumb_rotation]`           | same                                                                                                                                                                                           |
| Control     | **only `q` is meaningful**, everything else reserved                 | a 4-bit `mode` bitmask: 1 angle, 2 position, 4 force, 8 speed, combinable 0–15                                                                                                                 |
| Polarity    | **1.0 = open, 0.0 = closed** (`{"open", Ones()}, {"close", Zero()}`) | —                                                                                                                                                                                              |
| Transport   | serial                                                               | Modbus RTU over RS-485 **and** Modbus TCP (left `192.168.123.210`, right `.211`, port 6000); _"only one device per bus at about 20 Hz"_ on RS-485                                              |
| Tactile     | —                                                                    | **17 sensors**, `fingerone` = **pinky** … `fingerfive` = **thumb**, plus `palm_touch`. On RS-485 the touch topic **publishes nothing**, so silence there is not evidence of a non-tactile hand |

⚠️ **The DFX polarity is the opposite of the intuitive reading** and opposite in spirit to
`hand_sdk`'s "positive tau closes". If Inspire hands turn out to be what is fitted, a
wrong-polarity default would **slam the hand shut on power-up**.

⚠️ **The Inspire DFX page is H1-contaminated**: on a page filed under `G1_developer` it
says _"**H1** provides a USB to serial module … usually named `/dev/ttyUSB1` (left hand),
`/dev/ttyUSB2` (right hand)"_, links to `H1_developer` pages, and its changelog reads
_"Initial Release (**Reuse the H1 routine**)"_. The DDS half is consistent with the
`rt/inspire/*` naming used elsewhere and is probably fine; **the ttyUSB1/ttyUSB2
assignment is an H1 claim and must not be used to reason about our FT4232H port layout.**
It also conflicts with the FTP page's own `/dev/ttyUSB0` default on the G1. **[web]**

**`rt/hand_sdk` is not a generic hand interface.** _"The Hand SDK is an external control
interface provided by the `ai_sport` module … By publishing to `rt/hand_sdk`, a user
process can inject commands for the 4 hand motors into `ai_sport`"_ — scoped explicitly to
the **Dex2-5 five-finger 2-DoF hand and the Dex1-1 parallel gripper**, _neither of which
appears in the G1-EDU variant table_ (§7.0). **[web]** It is an `ai_sport` blending
injection, categorically different from the per-hand driver topics above.
`unitree_go::msg::dds_::MotorCmds_`, 4 motors,
`Motor_real = weight × Hand_SDK + (1 − weight) × G1_Cmd` with all five of `kp/kd/q/dq/tau`
participating. Three details worth carrying:

- ⚠️ **Weight encoding trap:** `weight` is an **integer 0–100 stored in `cmds[0].mode`**
  (`weight = cmds[0].mode / 100.0`); the other three motors' `mode` is unused. That is
  structurally the same idea as `rt/arm_sdk`'s `motor_cmd[29].q` blend weight but a
  **different encoding in a different place** — the two are not interchangeable.
- **Preconditions:** `ai_sport` running, robot **not** in damping, a compatible hand
  installed. _"While the robot is in the damping state, user commands are not forwarded …
  after leaving damping the user must re-set `weight` explicitly."_ In the
  no-controller-loaded debug state this path is **inert** — a poor diagnostic and a bad
  first hand experiment.
- **Auto-fallback on timeout**: stop publishing and `ai_sport` resumes its default
  behaviour, no cancel call needed — a third instance of the firmware-deadman pattern.
  Also: _"ramp `weight` smoothly"_ — stepping 0 → 1 can cause abrupt motion.

Our Isaac sim profile's `rt/dex1/{left,right}/{cmd,state}` with `MotorCmds_` is the
**Dex1-1 gripper**, i.e. the same family `hand_sdk` names.

### 7.6 Corrections our repo still needs, whichever hand is fitted

- ✅ **Applied 2026-08-20.** `apps/bridge/src/bridge/sdk/g1_protocol.py` (`REAL_TOPICS`)
  carried `dex_left_cmd="rt/api/dex3/left/request"` / `dex_right_cmd=…`. **The hands are
  not an RPC service** — no api*id, no JSON envelope, and no `rt/api/dex3/*`topic in any
vendor source on this robot nor in any of the six official hand-related pages. Now`rt/brainco/{left,right}/{cmd,state}`, carrying bare
`unitree_go::msg::dds*::MotorCmds*`/`MotorStates\*` sequences. **[src]**
- **`/api/dex3_msg_controller`**, which earlier inventories cited, appears in **no**
  vendor source, binary or config anywhere on this robot. Its only occurrences were our
  own doc files. Unsourced; struck. **[live]**
- **Hand units on THIS robot are BrainCo's [0,1] normalised range.** The per-family table
  (Dex3 radians, BrainCo [0,1], Inspire DFX [0,1] with 1.0 = open) is kept for reference,
  but only the BrainCo row applies here. There is no Dex3 `timeout` deadman bit to set —
  that firmware deadman does not exist on a BrainCo, so **a hand skill gets no free
  timeout** and any hold must be bounded by the bridge, like every other actuation path.
- **No hand command has ever been sent.** State has been observed; `MotorCmds_` has never
  been published, and `brainco_hand_server` has to be running for anything to receive it.

---

## 8. Audio — mic array, speaker, LED

The `voice` RPC service itself — service name, api*id table (TTS 1001 … SET_RGB_LED 1010),
payload shapes, the `TtsMaker` index bug, `PlayStream`/`PlayStop` semantics and the
vendored-client defects — is fully treated in `docs/ROBOT-API.md` §7. This section covers
the \_hardware*: where the devices live, how the raw paths work, and who shares them.

### 8.1 None of it is on the Jetson

Confirmed four independent ways: **[live]**

- `/proc/asound/cards` → exactly two: `0 [HDA]` (NVIDIA Orin NX HDA) and `1 [APE]`
  (Tegra APE).
- `aplay -l` → card 0 devices 3/7/8/9 = **HDMI only**, no analog out. `arecord -l` → only
  the APE `tegra-dlink-N XBAR-ADMAIFn` endpoints, which are the AHUB's internal DMA
  endpoints, not a physical capture path.
- `amixer -c 1 controls` → 1535 controls, all generic SoC blocks (DSPK, ADX, AMX, SFC,
  MVC, DMIC, I2S), with **no external codec name anywhere**.
- `lsusb` → no USB-audio-class device. PulseAudio → one sink, **zero real sources**.

The Jetson's APE/HDA are SoC plumbing **with nothing wired to it**. The G1's 4-mic array
and speaker belong to the control board at `192.168.123.161` and are reached two ways.

### 8.2 Path 1 — raw mic over UDP multicast (not DDS)

Unitree's own C++ SDK example (`unitree_sdk2/example/g1/audio/g1_audio_client_example.cpp`)
hardcodes it, which upgrades this from community lore to **[src]**:

```c
#define GROUP_IP  "239.168.123.161"
#define PORT      5555
#define WAV_LEN_ONCE (16000 * 2 * 160 / 1000)   // 5120 B = 160 ms
```

Format is **16 kHz, mono, signed 16-bit LE PCM, one pre-mixed channel**. Four mics
physically, one channel on the wire — beamforming and AEC happen on the control board and
we get no per-element or DOA access **[web]** (though `rt/audio_msg`'s `angle` field gives
DOA for free — `docs/ROBOT-API.md` §7).

**The interface pin is load-bearing.** The vendor example walks `getifaddrs` for a
`192.168.123.*` address and sets `mreq.imr_interface` to it. Joining on `INADDR_ANY` lets
the kernel's default route pick `wlan0` or `docker0`, and you get **zero packets,
silently.**

**This is invisible to our DDS config, by design.** The bridge's CycloneDDS setup
(`apps/bridge/src/bridge/sdk/connection.py`) is irrelevant to the mic, which is a plain
UDP socket. A future `listen()` skill must open its own socket and `IP_ADD_MEMBERSHIP`
with `imr_interface` = eth0's address (`192.168.123.164`), independently of CycloneDDS.
**[src]**

At the snapshot the group **was** joined on eth0: `/proc/net/igmp` shows `A17BA8EF`
(= 239.168.123.161) with 1 user, `/proc/net/dev_mcast` shows the derived MAC
`01005e287ba1`, and `ss -ulnp` shows `0.0.0.0:5555` held by pid 2239 = `gemm-ai.service`.
The DDS group `239.255.0.1` was present at the same time with 6 users — two different
groups, both live. **[live]**

**Joined ≠ flowing, and now measured: at rest it does NOT flow.** 2026-08-20, with
`gemm-ai.service` active and holding `0.0.0.0:5555`, a second subscriber joined the group
on eth0 and read **0 packets in 12 s** while the robot sat idle. The join was verified
rather than assumed — `/proc/net/igmp` showed `A17BA8EF` against eth0 — because on this
feed a silent socket is the expected result of binding the wrong interface, so an
unverified zero is worth nothing. **[live]**

That kills "continuously streaming" as a working assumption, and with it the idea that a
`listen()` skill can simply open a socket whenever it likes. Two readings remain open and
they have very different consequences:

- the feed is **gated on the remote's wake-up mode**, exactly as ASR output is — a future
  `listen()` then has a human prerequisite and cannot be part of an unattended loop;
- or it streams **only on demand**, and something must ask first. No RPC to ask with has
  been found: `vui_service` exposes `START_PLAY`/`STOP_PLAY`/`GET_VOLUME`/`SET_VOLUME` and
  **no ASR or capture function at all** (§8.3), so if a trigger exists it is not there.

**⚠️ THE ANSWER BELOW IS INCOMPLETE — the feed has since been observed
free-running with no remote at all.** Later the same day, after several reboots:
**157 packets / 25.1 s of audio over a 25 s window (1.00x realtime), with
`btn=0x0000` and `wireless_remote` reporting the R3 not transmitting.** Nobody
was holding anything. **[live]**

So "gated on wake-up mode" is not the whole rule. Three candidate explanations,
none confirmed: L1+L2 **latches** rather than being held (this was flagged as
untested and is the simplest fit); something in the co-tenant's stack — whose
container roster changed that day — holds the mode open; or the vendor assistant
opens it for its own reasons.

**What to rely on: measure, do not assume.** `listen` reports `audio_flowing`
from observed packets rather than inferring from which source is configured,
precisely because the same feed has now been seen both gated and free-running
within a day. Treat the button as a way to OPEN the feed, not as proof it is
shut.

The original finding, which stands as far as it goes:

**GATED ON THE REMOTE'S WAKE-UP MODE.** Holding
**L1+L2** opens it; releasing closes it. Two runs, both with the join verified: 212 packets
/ 33.9 s of audio in a 45 s window, then 262 packets / 41.9 s while a person spoke Spanish
into it, transcribed live. At rest, across every earlier probe, exactly zero. **[live]**

So `listen()` has a **human prerequisite** and cannot run unattended: something has to hold
a button on the remote for the robot to hear anything. That is a hard constraint on every
voice design downstream — an always-listening assistant is not available on this hardware
without the App-side equivalent, which is untested.

Everything below was the investigation that got here, and is kept because each step
eliminated a cheaper explanation.

**A stopped service is NOT the explanation** — the obvious third reading, checked and
killed. `robot_state` 1003 `ServiceList` reports `vui_service` _and_
`audio_player_service` both running on 2026-08-21 (`status: 0`; polarity confirmed against
`robot_state` reporting itself running while answering — `ROBOT-API.md` §8). So the mic is
silent with its owning services up.

Distinguishing the two remaining readings needs a person: hold **L1+L2** on the remote and
re-run the count. `apps/bridge/scripts/mic_wakeup_probe.py` does both halves at once — it
counts multicast bytes _and_ decodes `LowState_.wireless_remote`, because a run where
nobody pressed anything and a run where the press changed nothing are the same column of
zeros, and only the button field tells them apart. It prints a verdict rather than raw
counts.

First run, 2026-08-21: 929 lowstate frames, **0 remote-shaped** — an all-zero
`wireless_remote` blob is the vendor's own `isJoystickTimeout_` predicate (§9.5), so the
R3 was not transmitting at all and the run is correctly reported INCONCLUSIVE rather than
as a confident zero. That is the script working, not failing. Recording a room still needs
consent — this counts bytes and decodes nothing.

**And the privacy fact, stated plainly:** while `gemm-ai.service` runs, it continuously
Whisper-transcribes this feed — **the mic is always on and everything said near this robot
goes into a transcript**, and `stop_gemm` does not stop that service (§10.1). A published
arXiv G1 teardown additionally documents persistent telemetry sending audio to external
servers without explicit consent (the control-board `vui_service`/`chat_go` stack, port
6080, continuous mic capture) **[web]** — its claim of a `vui_service` process _on the
Jetson_ is refuted for this host (no `/unitree/module/vui_service` here **[live]**), but
the control board cannot be inspected, so the warning stands before any always-on-mic
experiment.

### 8.3 Path 2 — the `voice` DDS RPC service: sharing and the LED

Full API treatment: `docs/ROBOT-API.md` §7. Hardware-relevant facts:

**The speaker has at least three uncoordinated writers** — us, `gemm-ai` (a confirmed
writer: `RobotEmbeddedTTS` → `TtsMaker`, plus `PlayStream`/`PlayStop` keyed on
`APP_NAME = "gemm-ai"` **[live]**), and Unitree's own voice assistant. `PlayStop` is
scoped per `app_name`, so ours must use its own (`"c3po"`) and **cannot stop theirs**.
Whether `PlayStream` mixes with or preempts a concurrent `TtsMaker` is unknown; queue
behaviour is defined only for `PlayStream` (via `stream_id`), so prefer `PlayStream` for
anything that must be interruptible.

**The vendor voice assistant cannot be disabled programmatically.** It wants the same
speaker, mic and LED strip, with no arbitration; the only documented off-switches are the
remote (L1+L2 to change mode, L1+Select to force-interrupt) and the App. It needs the
Internet for its GPT path (firmware ≥ 1.3.0), so on an air-gapped `192.168.123.x` robot it
degrades to an offline _"Hello, I am here"_ — the quietest practical state and probably
the one we are already in. **[web]**

⚠️ **Never switch `vui_service` off to silence it.** That is the `robot_state` service
name for _"Audio and Lighting Control Service"_ — one service providing TTS, `PlayStream`,
volume **and** the light strip. Turning it off would silence us as well. **[web]** (Three
different things carry the letters "vui" — the Go2-only `vui` RPC service, the
`vui_service` process name, and the "VuiClient Service Interface" doc page title; the
disambiguation table is in `docs/ROBOT-API.md` §7. `/api/audiohub` does not exist on this
robot — "Audio Hub" is a real firmware component but an app/WebRTC-side one, not a DDS
RPC service. **[live]** + **[web]**)

⚠️ **The LED strip is safety-relevant, not decorative.** It has **four uncoordinated
writers**: the motion FSM's own state colours (the colour → FSM-state map is in
`docs/ROBOT-API.md` §4.5 — it is the operator's only mode indicator, including debug
mode and error state), the voice assistant (breathes blue on hearing, green on receiving
an instruction), `SET_RGB_LED` callers, and us. **[web]** Driving it overwrites the
operator's only indicator that the robot is in an error or debug state, and nothing
documents how to hand the strip back to the FSM. Either do not expose LED control to the
LLM, or expose it only as a short flash that restores afterwards. **And ask the operator
to record the strip colour at the top of every window** — a free, instant, zero-RPC
readout of the robot's mode that retroactively disambiguates every session log. The LED's
**physical location is documented nowhere**; finding it needs eyes.

### 8.4 `rt/audio_msg` — ASR text, never audio

Type is `std_msgs::msg::dds_::String_` carrying JSON; the full schema (including the
`angle` DOA field, the `{"play_state": 0|1}` second payload, and the `speaker_id`
diarization-vs-voice-role trap) is in `docs/ROBOT-API.md` §7. Do not size buffers or QoS
as if this topic carried audio — raw PCM goes over the UDP multicast (§8.2).

Two hardware-level facts about the embedded ASR:

- **It is unusable for us.** A colleague code comment dated 2026-08-06 — after this robot
  arrived — records it live-verified **transliterating non-Japanese speech into unrelated
  kana** (_"Hola Darío"_ → `オラオラがるよ…`), so exact phrase matching never fires;
  near-silence emits lone punctuation. This is second-hand but dated after hardware
  access, and it is why `gemm-ai` abandoned `rt/audio_msg` and runs Whisper continuously
  over the raw multicast instead. **[src]**
- ⚠️ **ASR output is gated on a mode we cannot set over DDS.** _"When the robot's
  microphone is turned on (**switch to the wake-up mode in the APP or remote control**),
  the built-in microphone + ASR module will recognize the human voice."_ **[web]** Modes
  switch with **L1+L2** on the remote or in the App under
  【Device】→【Data】→【Audio】→【Voice assistant】; the wake word is _"Hello Robot"_;
  **L2+Select** wakes, **L1+Select** force-interrupts; the dialogue ends after 15 s of
  silence. So a `listen()` built on `rt/audio_msg` has a **human prerequisite**. Whether
  the raw multicast feed (§8.2) is gated the same way is unknown and worth testing — if it
  is always live, our own STT is independent of the vendor assistant entirely.

### 8.5 On-robot TTS works, and the service is alive

Synthesis is entirely on-robot for Chinese and English: `speaker_id` `0` = Chinese, `1` =
English, **two voices only, and neither reads Spanish intelligibly** — verified on-robot
by the colleague, which is why they built an MP3→PCM16 gTTS path through `PlayStream`.
That same file's note about a PyAV frame-padding bug _"heard on-robot as crackle/noise and
stuck playback"_ is second-hand evidence that `PlayStream` genuinely drives this robot's
speaker. **[src]** For anything non-Chinese/English: external synthesis → PCM16 @16 kHz →
`PlayStream`.

The bridge's `say` tool implements this path (see `apps/bridge/src/bridge/mcp_server.py`
and the voice client in `apps/bridge/src/bridge/sdk/g1_rpc.py`); the api-level details and
vendor defects to code around are in `docs/ROBOT-API.md` §7. Verified 2026-08-15:
`GET_VOLUME` answered `{"volume":100}` — the service is alive on this unit and the volume
was at maximum. **[live]** Note the ALSA mixer (§6.5's boot-time
`amixer set Speaker 75%`) and the `voice` service's 0–100 volume are **two independent
gains** — a silent robot can be either.

Ack semantics are probably immediate, unlike the arm service: every vendor example
_sleeps_ after TTS rather than relying on the call blocking. **Not proven** — a `say()`
that must know when speech _ended_ needs `play_state` (§8.4) or its own duration model.
**[?]**

### 8.6 Wake word, VAD and what our venv lacks

No firmware wake word is exposed through any API; `voice` api 1002 (`ASR`) is registered
by every vendor client and called by none. On disk: **Silero VAD v5** (bundled with
faster-whisper 1.1.0, onnxruntime 1.19.2) is the only real VAD model; openWakeWord is
**not** installed and `GEMM_WAKEWORD_MODEL_PATH` is empty; no Porcupine, `.ppn` or
`.tflite` wakeword models exist anywhere. The VAD actually in use by gemm-ai is a
hand-rolled RMS gate (threshold 500.0, 1.2 s silence tail, 8 s max utterance). STT cached
on-robot is `Systran/faster-whisper-base` (142 MB, dated 2026-08-06 — so the mic→Whisper
path was genuinely exercised on hardware). **[live]**

Our bridge venv has **none** of this: no whisper, no onnxruntime, no VAD, no PyAV, no
sounddevice/pyaudio. `ffmpeg` and `sox` are not installed system-wide either (`aplay`,
`arecord` and `amixer` are). **[live]**

---

## 9. IMU, battery and wireless controller

### 9.1 IMUs — there are three, and they are not interchangeable

The body/pelvis IMU rides **inside** `LowState_` on `rt/lf/lowstate` (~20 Hz measured).
Layout of `unitree_hg::msg::dds_::IMUState_`: **[src]**

```
float32[4] quaternion     # w,x,y,z — vendor-stated: "// Quaternion QwQxQyQz"
float32[3] gyroscope      # rad/s, raw
float32[3] accelerometer  # m/s^2, raw
float32[3] rpy            # ZYX Euler, body frame; rpy[2] is yaw
int16      temperature    # NOTE: int16 here; unitree_go's IMUState uses int8 — different wire size
```

A **second IMU** is published on `rt/secondary_imu`, same `IMUState_` type, subscribed by
vendor G1 low-level examples as the **torso** IMU. Both spellings are real: Unitree's own
topic table lists `rt/secondary_imu` **and** `rt/lf/secondary_imu` as a high-rate /
low-frequency pair, the same convention it applies to `lowstate`, `odommodestate` and
`dex3/*/state`. **[web]** Presence on _this_ firmware remains unverified. **[src]**

The three IMUs on this robot:

| IMU            | Where it appears                                      | Catch                                                                       |
| -------------- | ----------------------------------------------------- | --------------------------------------------------------------------------- |
| G1 body        | `LowState_.imu_state`, `rt/secondary_imu`             | `unitree_hg/IMUState`, _"que ningún SLAM estándar entiende"_                |
| Livox ICM40609 | `/livox/imu` or `rt/utlidar/imu_livox_mid360`, 200 Hz | inverted mount, gravity sign flip (§4.8); `sensor_msgs Imu_` not in our IDL |
| D435i BMI055   | IIO `accel_3d` / `gyro_3d`                            | not factory-calibrated; extrinsic to depth fixed **[web]**                  |

Consequence for any LIO we build: **it must use the LiDAR's IMU or the RealSense's, not
the G1's.**

### 9.2 Battery — its own topic, now actually read

**The G1's `LowState_` carries no battery field at all.** Nine fields, in wire order:
**[src]**

```
uint32[2] version; uint8 mode_pr; uint8 mode_machine; uint32 tick;
IMUState imu_state; MotorState[35] motor_state; uint8[40] wireless_remote;
uint32[4] reserve; uint32 crc
```

No `bms_state`, no `power_v`/`power_a`, no `foot_force` — all of which **do** exist in
`unitree_go/LowState.msg`, the quadruped type. A Go2-shaped code path reading battery off
LowState gets null on the humanoid.

State of charge is on **`rt/lf/bmsstate`** (`unitree_hg::msg::dds_::BmsState_`), observed
on _this_ robot at ~20 Hz in two rosbags (2026-08-11 and 2026-08-13). **[live]** The type
ships in `unitree_sdk2py`, and the bridge now subscribes it: `state.py` reports `soc` as
`battery_pct` and emits a `low_battery_<n>pct` fault below the vendor's own threshold
(`low_battery(bms_state, limit_soc = 20.0)` in `g1/common/terminations.hpp` — **`soc` is
0–100 percent**, not 0–255). **[src]** A null `battery_pct` from the bridge means _no BMS
publisher seen_, not a healthy pack — don't read null as green.

The full `BmsState_` field listing, the units question (`current` mA vs 10 mA,
`bmsvoltage` mV), the `rt/slam_info` corroboration, and the `AgvBmsState_` false-friend
warning are in `docs/ROBOT-API.md` §9. No official page documents the `BmsState_` fields
at all — one decoded live message settles the units, and reading docs never will.

### 9.3 Wireless controller — rides in LowState, free

Two representations exist; **only one is real on the G1**:

- **`rt/wirelesscontroller`** (`unitree_go::msg::dds_::WirelessController_`) appears in
  **zero** of the 45 official G1 pages and only a Go2 example uses it — positive evidence
  of absence; treat it as Go2-only unless a live discovery scan proves otherwise. **[src]**
  - **[web]**
- **`LowState_.wireless_remote`, `uint8[40]`** — the raw controller packet, present in the
  LowState we already subscribe, so remote state needs **no extra subscription and no
  extra type**. Every vendor example reinterprets it as `xRockerBtnDataStruct`; the struct
  layout, the ⚠️ `lx, rx, ry, L2, ly` axis-order trap, the 16-key bit table, the
  `head == {0xFE, 0xEF}` check and the SDK's 3000 ms all-zero `isJoystickTimeout_`
  predicate are catalogued in `docs/ROBOT-API.md` §9.5. Our `state.py::_on_lowstate`
  currently **discards** the field — decoding it would turn "the remote didn't work" from
  an anecdote into a measurement.

Which button combinations the firmware itself intercepts is still undocumented; the
combinations themselves (L2+B e-stop, R1+X vs R1+Y waist variants, the held-2-seconds
rule, L2+R2 debug entry) and the LED colour map are in `docs/ROBOT-API.md` §4.4–4.5. The
controller's bytes reach us both decoded and raw, so any interception happens upstream of
us, invisibly. **Do not guess the reserved combos.** **[src]** + **[web]**

---

## 10. Cohabitants: the gemm stack, teleop, and sharing

How to _operate_ around these facts (stack-control scripts, takeover procedure) is
`docs/OPERATIONS.md` + the `scripts/robot/` headers. This section records what is on the
machine.

### 10.1 The gemm stack is two independent pieces

A colleague (`OliverJones08` in git; the `gemm` stack on the robot) runs **two
independent pieces**, not one. Both return on every boot. **[live]**

**1. `gemm-bringup`** — docker container, `--network host`, `Privileged=true`,
`restart=unless-stopped`. A full Nav2 autonomy stack:

```
nav2_controller  nav2_planner  nav2_behaviors  nav2_bt_navigator
nav2_velocity_smoother  nav2_lifecycle_manager
realsense2_camera_node  foxglove_bridge (:8765)  gemm_navigation/odom_tf_bridge
static_transform_publisher (base_link -> lidar_link)
```

(By 2026-08-15 the running roster had grown — `gemm_robot_server`,
`gemm_lidar_live_relay` were observed alongside; audited as still issuing no motion
commands. See `docs/ROBOT-API.md` §12.)

**2. `gemm-ai.service`** — a **systemd unit**, enabled and active, running
`~/gemm_ai/.venv/bin/python -m backend.main` under Python 3.8 with its _own_ vendored
CycloneDDS copy at `~/gemm_ai/.cyclonedds`. A voice/vision assistant. And it is more than
a port-holder: it is a **live domain-0 DDS participant pinned to eth0** — the 2026-08-18
recon found `/tmp/cdds.LOG` held open by `backend.main` (pid 2862). **[live]**

Two consequences, both of which cost time if you don't know about them:

- **It binds `0.0.0.0:8000`** — the port our bridge daemon would otherwise have taken,
  which is why the bridge runs on 8001 (see `apps/bridge/.env.example`).
- **`stop_gemm` does not stop it.** That script matches
  `docker ps --filter name=^gemm`, and a systemd unit is not a container. "gemm stopped"
  therefore does not mean "the robot is entirely ours" — check
  `systemctl is-active gemm-ai` as well. (The script header carries the same warning.)

**It is not a motion risk, and here is the audit basis:** its own source contains no
`SetVelocity`, no api_id 7105, and no `LocoClient`/`SportClient` use — the only hits for
those were inside its vendored `unitree_sdk2py`. **[live]** (An earlier record claimed it
"only subscribes, to `rt/audio_msg`" — corrected: in real mode it does not use
`rt/audio_msg` at all; it joins the raw mic multicast, runs local Whisper, and **writes**
to the `voice` service, §8.3. The no-motion-writer finding stands.) Re-check if their
backend grows.

### 10.2 `cmd_vel_to_loco` — off by default, one argument from live

Nav2 publishes `/cmd_vel`, but the piece that would forward it to the robot's legs —
`cmd_vel_to_loco` — is **disabled by default** and absent from the running process list.
Their launch files gate it loudly:

> `cmd_vel_to_loco`, el puente que hace caminar al robot — **APAGADO por default**
> "Reenviar /cmd_vel a la API de locomocion del G1. **EL ROBOT CAMINA.**"

It is one launch argument away from being live, **there is no technical interlock between
their stack and ours** beyond what `scripts/robot/` enforces, and their own caveat applies:
_"Nada de esto esta verificado todavia: no hubo ventana con el robot"_ — their motion path
is untested against hardware. **[?]** Two independent controllers commanding the same legs
is the obvious way to break this robot; before either party sends motion, coordinate.

Their `cmd_vel_to_loco` carries two design decisions we adopted rather than reinvented —
the `duration = 1.0 s` firmware-level deadman (never `Move()`'s 864000 s) and
fire-and-forget (the vendor client blocks up to 5 s per call, impossible at loop rate;
their loop runs 10 Hz, ours re-issues at 50 Hz). The full SET_VELOCITY doctrine lives in
`docs/ROBOT-API.md` §5.

### 10.3 The third stack: `xr_teleoperate`

Found running from `/home/unitree/gemm_ai/xr_teleoperate` on 2026-08-14 — neither a gemm
container nor a systemd unit, but human-launched SSH-session processes under a
setsid-detached watchdog: **[live]**

| Process                                                | PID   | Holds                           |
| ------------------------------------------------------ | ----- | ------------------------------- |
| `teleimager.image_server`                              | 5850  | `/dev/video4`, ~44 % CPU        |
| `_image_service_watchdog.sh` (PPID 1, setsid-detached) | 5848  | respawns the above, 20×/3 s     |
| `brainco_hand_server --network_interface eth0`         | 5923  | `/dev/ttyUSB1`, ~8 % CPU        |
| `test_vuer_only.py` (started 02:07)                    | 10751 | — someone was actively using it |

**It commands motion.** `repo/teleop/utils/motion_switcher.py` wraps `LocoClient` and
calls `Move()`; `repo/teleop/robot_control/robot_arm.py` publishes to **`rt/arm_sdk`** and
**`rt/lowcmd`**, with `_set_arm_sdk_weight()` and `release_arm_sdk()`. And
`teleop_hand_and_arm.py` without `--motion` calls `MotionSwitcher().Enter_Debug_Mode()`,
which loops `ReleaseMode()` until `CheckMode` returns an empty name — **deliberately
leaving the robot with no motion controller loaded**. **[src]** That state, its
`CheckMode`-first diagnostic, and the 7400/7401/debug-mode consequences for arm gestures
are treated in `docs/ROBOT-API.md` §4.6/§6.

At the time, `run_c3po`'s other-commander check only grepped for `cmd_vel_to_loco`, so a
full teleoperation stack holding the camera and the hand bus passed it silently. The check
now covers `cmd_vel_to_loco|xr_teleoperate|brainco_hand_server` and names the matched
process (`scripts/robot/_common.sh`, with the story in its comments). **Still uncovered:**
`unitree_slam` (its 1102 pose navigation closes its own velocity loop — a second
commander), and any generic publisher on `rt/arm_sdk` or `rt/lowcmd`. Killing the
teleimager server without killing its watchdog just gets it respawned.

### 10.4 Who arbitrates what

| Resource                                    | Arbitrated by              | Owners allowed            | What collision looks like                                                            |
| ------------------------------------------- | -------------------------- | ------------------------- | ------------------------------------------------------------------------------------ |
| `/dev/video0..5`                            | kernel V4L2                | one **per node**          | `xioctl(VIDIOC_S_FMT) failed … Device or resource busy`                              |
| `/dev/ttyUSB0..3` (hands)                   | kernel tty                 | one per port              | open fails / `Failed to get device info`                                             |
| D435i IMU (IIO)                             | IIO, separate from V4L2    | does not contend          | —                                                                                    |
| **Livox Mid-360 (raw)**                     | **the sensor itself**      | **one host, globally**    | the previous owner just goes silent                                                  |
| **LiDAR over DDS** (`rt/utlidar/*`)         | nothing — ordinary pub/sub | unlimited                 | —                                                                                    |
| Mic multicast `239.168.123.161:5555`        | nothing — IP multicast     | unlimited readers         | —                                                                                    |
| Speaker (`voice` service)                   | nothing                    | unlimited writers         | utterances interleave — **three writers**: us, `gemm-ai`, the vendor voice assistant |
| **LED strip**                               | **nothing**                | unlimited writers         | our colour hides the FSM's own state colour (§8.3)                                   |
| DDS topics                                  | nothing                    | unlimited                 | —                                                                                    |
| **Robot control API** (`sport` 7105)        | **nothing**                | must be one, by agreement | two controllers, one set of legs                                                     |
| **Vendor navigation** (`slam_operate` 1102) | **nothing**                | must be one, by agreement | it runs its own PID emitting vx/vy/vyaw — a second commander **[web]**               |

The asymmetry that matters: **the LiDAR's exclusivity is enforced by the device, not by
the OS, and it persists** — killing the process that grabbed it does not hand it back
(§4.3). Every other single-owner resource here releases on process exit. Two refinements
over the coarse "one owner" rule: the LiDAR "port bind" is the _host_ side of a
device-side reconfiguration, and the RealSense's "one owner" is per V4L2 node (possibly
per endpoint), which may leave depth available while colour is held (§5.3).

**Nothing in the vendor SDK uses the lease mechanism.** `grep -rn 'Client(.*true)'` across
the whole include tree returns **zero** hits — every client, including `LocoClient`, is
constructed with `enableLease = false`. So no vendor service arbitrates ownership:
**whoever writes to a request topic is obeyed.** If we ever _do_ see error 3205/3206/3207
(lease denied / not in cache / already exists), something outside this SDK has taken a
lease — which would be a genuine answer to the FSM-authority question. **[src]**

---

## 11. Clock sync

Recorded so nobody adds it as cargo cult. Unitree's `time_sync_interface` (2026-04-15,
applies to firmware > 1.5.1): **PC1 (`192.168.123.161`) is an NTP server**, and the
documented client config is chrony with `server 192.168.123.161 iburst prefer` /
`makestep 1.0 3` / `rtcsync`, or `systemd-timesyncd` with `NTP=192.168.123.161` (the two
are mutually exclusive). **[web]**

**We need none of it today.** The bridge computes `lowstate_age_s` as (local now − local
receipt time) on a single host, so no cross-clock comparison happens, and DDS itself does
not require synchronised clocks. It becomes **required** the moment we do any of:
correlate the colleague's rosbag/foxglove timestamps against our logs; fuse D435i frames
(which the vendor times with `CLOCK_REALTIME`, §5.1) with DDS state; or timestamp DB
episodes against on-robot events.

Two cautions worth having now regardless: **[web]**

- ⚠️ _"When PC1 performs network time synchronization, it may cause short-term time
  fluctuations. Therefore, it is recommended to disable G1's WiFi mode when using programs
  that rely on system time."_ **The Jetson is on Wi-Fi for our SSH/mDNS access**, so a
  mid-experiment clock step could make a latency or duration measurement nonsense.
- If we ever add chrony on the Jetson, expect the documented failure: chronyd 3.5 dies
  with `code=dumped, signal=SYS` under its seccomp filter. Fix is `DAEMON_OPTS="-F 0"` in
  `/etc/default/chrony`.

---

## 12. Open questions

Each with what would settle it. Control-API questions (FSM tables, 7404 polarity, gesture
indices, `fsm_id` 550, sign conventions) live in `docs/ROBOT-API.md`'s open questions;
these are the hardware ones.

**Answerable with no robot**

- **Q0a. Fetch the two Dex3 tactile pad-layout images** at
  `doc-cdn.unitree.com/static/2024/12/26/…_5000x2812.png`, dropped by the docs' markdown
  conversion. They are the only pad→finger map that exists (§7.4). Only needed if tactile
  matters.
- **Q0b. Ask the operator to read the Unitree Explore APP's waist motor lock switch**, and
  whether a ≥ 1.3.0-era calibration was ever done. Phone only.

**LiDAR**

1. **Where is the Mid-360 unicasting right now?** Not to the Jetson (proved passively,
   §4.4). Most likely the control board, or `point_send_en` (`0x0003`) is off. Settle with
   `sudo tcpdump -i eth0 -n host 192.168.123.120 -c 20`, or a read-only
   `QueryLivoxLidarInternalInfo` reading registers `0x0006`/`0x0007`. Also decides whether
   perception's Stage-5 driver launch steals a stream someone is using (§4.5).
2. **Is the vendor `lidar_driver` service on, and do `rt/utlidar/*` exist right now?**
   Settle with `robot_state` `ServiceList` — a pure getter, the cheapest high-yield probe
   on the whole robot (`docs/ROBOT-API.md` §8).
3. **Is the host IP the SDK writes into the sensor persistent across a LiDAR power
   cycle?** This decides whether "reversible" means automatic or "stays stolen". Settle by
   power-cycling after running our driver and re-testing which host receives point data.
4. **Sign of `/livox/imu` linear_acceleration.z with the robot static** — the
   inverted-mount gravity trap (§4.8) — and whether acceleration is in g or m/s².
5. ~~**RELIABLE or BEST_EFFORT on the `utlidar` topics?**~~ **ANSWERED 2026-08-21: RELIABLE**, KEEP_LAST(1), VOLATILE — read straight off the publisher with `ros2 topic info --verbose` from our own humble container (§4.5). The bag metadata was right; the prose was wrong.
6. **The real `base_link → lidar_link` translation.** gemm's default `z = 1.0` is marked
   "APROXIMADA". Unitree gives `(−0.0, 0.0, −0.47618)` with an "inverted" placement — but
   the z sign is wrong for a head mount as literally stated, so validate against a real
   cloud before using it in any transform (§4.6).
7. **Is our unit a Mid-360 or a Mid360s?** Unitree changed the part for units
   produced after April 2026; this robot arrived 2026-08-04. It does not affect the DDS
   republish path, but it invalidates any pinned Livox-SDK2 / `livox_ros_driver2` version
   (§4.6).
8. **Does the ARM CustomMsg ~5 Hz throttling (§4.7) reproduce on our Foxy Jetson?**
   Measure `/livox/lidar` rate before trusting 10 Hz in any LIO budget.

**Cameras**

9. **Did `/dev/video10` ever exist on this robot?** The only unanswered part of the
   chest-camera question. Settle with `sudo dmesg | grep -iE 'usb|uvc|xhci'` (blocked:
   `dmesg_restrict=1`) plus a physical look at the chest for an unplugged lead.
10. **Does `videohub_pc4` actually produce frames on `rt/frontvideostream`, or does it
    also sit in its retry loop?** It held `/dev/video4` for ~6 minutes at boot but nothing
    captured its stdout. Settle by stopping teleimager,
    `sudo systemctl start master_service`, then subscribing with the 0.10.2 Python
    bindings on domain 0 / eth0.
11. **Does `unitree_sdk2py`'s Go2 `VideoClient` (service `videohub`, api 1001) get a
    response from the G1 head videohub?** Topic names match exactly, so it should. Not
    exercised.
12. **Can the D435i be shared per endpoint?** Depth (`video0`) and IR (`video2`) were
    unclaimed while teleimager held only colour. Test whether we can open depth without
    disturbing anyone — and whether depth and IR share an endpoint, which Intel's doc does
    not say (§5.3).
13. **Is the RTP H.264 multicast at `230.1.1.1:1720` reachable from the Mac?** It is
    multicast on the wired robot LAN, so a Wi-Fi-only Mac almost certainly cannot receive
    it (§1). Unverified either way.

**Hands**

14. **Which hands are physically fitted, and how many?** Settle by **looking at the
    robot** — three fingers and 7 DoF is Dex3-1; five fingers narrows to
    {BrainCo, Inspire}. Do not settle it by probing the serial ports. The Dex3 DDS topics
    were silent on 2026-08-15 (§7.3); the remaining passive probes are `rt/inspire/state`
    and `rt/brainco/{left,right}/state` — **both types are in our venv**.
15. **Which G1-EDU variant letter is this unit?** The OTA product string maps to none of
    Standard / Advanced / Ultimate A–D (§7.0); `mode_machine = 5` tells us only 29 DoF. Is
    the variant recorded anywhere — a model plate, an `mscli` field, a config under
    `/unitree`, the serial number?
16. **Is anything attached to the left wrist at all?** The BrainCo probe only speaks
    Modbus RTU at 460800, and the service was launched with an explicit `--serial` per
    hand — it only ever probed what it was told to (§7.1, §7.3).
17. **If a Dex3 pair is fitted, where does its RS485 land?** No vendor page associates the
    Dex3 with a PC2 USB-serial dongle; tracing which physical wrist cable lands on which
    FTDI channel would be decisive (§7.2).
18. **For BrainCo, which end of [0,1] is open?** Documented for Inspire DFX (1.0 = open),
    implied for `hand_sdk` (positive tau closes), stated nowhere for BrainCo. Do not write
    an "open hand" preset until read out of the server source or observed (§7.1).
19. **Dex3 tactile: 6 pads or 9, and what is the pad→finger map?** The hand page says
    three 3×4 fingertip arrays (6 locations); `about_G1` says 9 array sensors (and "33
    sensors" appears on the product page). The map exists only in the two dropped images
    (Q0a). Also still ambiguous: which index/middle spec range is proximal vs distal, and
    the thumb_1 limit (SDK arrays 0.724/0.742 rad vs spec −35°/+60°) — the URDF stays the
    conservative rule (§7.4).
20. **Do `rt/dex3/*/cmd` and `rt/brainco/*/cmd` conflict if both are published?** Nothing
    describes arbitration between a resident Dex3 service and a user-run BrainCo one, and
    the robot arbitrates nothing (§10.4). Worth knowing before any hand skill ships.

**Audio**

21. **Is `rt/audio_msg` publishing, and does this firmware match the documented schema?**
    A 10–15 s read-only subscribe while someone speaks nearby — specifically whether
    `play_state` appears and whether it fires for _our_ `PlayStream` or only the
    assistant's playback. If ours, `say()` gets true completion detection.
22. **Is the mic multicast gated on wake-up mode, the way `rt/audio_msg` ASR output
    explicitly is (§8.4)?** Determines whether a future `listen()` needs an operator to
    set a mode from the APP or remote — a human prerequisite we cannot satisfy over DDS.
23. **Does the mic multicast actually carry packets today?** The group is joined; joined ≠
    flowing. Count packets only, for ~5 s — and get the operator's explicit consent first,
    because it is indistinguishable from recording the person standing next to the robot.
24. **What does `voice` api 1002 (`ASR`) do?** Registered by every client, called by none,
    no documented counterpart. Candidates: enable/disable, language select,
    pull-last-result.
25. **Does `TtsMaker` queue or interrupt when speech is already playing?** Defined only
    for `PlayStream` (via `stream_id`). Until tested, prefer `PlayStream` for anything
    interruptible. Related: does a second `app_name` interrupt the first?

**State and input**

26. **What are the real BMS numbers?** `soc`, `soh`, `temperature[12]`, `bmsstate[5]`, and
    the units of `current`/`bmsvoltage`. One decoded message from `rt/lf/bmsstate` — and
    confirmed unanswerable by reading, since Unitree documents `BmsState_`'s fields
    nowhere (§9.2).
27. **Is `rt/secondary_imu` / `rt/lf/secondary_imu` actually published on this firmware?**
    Both spellings are vendor-documented as a pair; what is unconfirmed is whether a torso
    IMU exists here at all (§9.1).
28. **Is `rt/wirelesscontroller` independently published on the G1**, or is the remote
    only available as `LowState_.wireless_remote[40]`? Zero hits across 45 official pages
    makes the latter the strong default; a passive `DCPSPublication` scan settles it
    (Q31).
29. **Which button combinations does the firmware itself intercept?** Log
    `wireless_remote` while an operator presses each combo (**held ≥ 2 s**) and watch
    whether `fsm_id` changes with no RPC from us. Only in a supervised window, with a hand
    on the physical e-stop.

**Sharing**

30. **Should `master_service` be restarted?** Nothing in `scripts/robot/` touches it.
    Until it runs, both video-hub nodes stay down and the boot-time
    `amixer set Speaker 75%` has not been applied (§6.5).
31. **What is the full DDS topic census?** gemm's stack reports the robot exposes ~121
    topics and references a `docs/robot-topics.md` that is **not on the robot** — it lives
    in their repo elsewhere. Getting that file would close most of this section in one
    step. Failing that, a passive `DCPSPublication` discovery read (topic name → type
    name) creates no writers and would produce the definitive live census in one shot —
    simultaneously answering Q14, Q27, Q28 and the `rt/lowstate`-vs-`rt/lf/lowstate`
    question.
32. **Is our onboard CycloneDDS config hiding participants?** The bridge sets
    `AllowMulticast=false` plus a single unicast peer at `192.168.123.161`
    (`apps/bridge/src/bridge/sdk/connection.py`); the vendor's own config is plain
    multicast on `eth0` (§6.5). Anything publishing from another `192.168.123.x` address —
    the NX itself, `unitree_slam`, a colleague's node — would be invisible to us with no
    error. Test by publishing from the Jetson and checking whether our own bridge sees it.
33. **Does the arm action service still work while `brainco_hand_server` or an
    `xr_teleoperate` session holds `rt/arm_sdk`, and is 7400 "occupied" or "busy"?** Two
    back-to-back gestures with nothing else running discriminates
    (`docs/ROBOT-API.md` §6). Test deliberately rather than mid-demo.
