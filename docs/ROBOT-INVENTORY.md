# G1 Robot Inventory — what's actually on the machine

Companion to `SPEC.md`. SPEC says what we intend to build; this says what the hardware
actually presents, verified against the physical robot on **2026-08-11**, with a second
pass on **2026-08-12** while installing our stack onboard (§1 addressing, §5 the second
`gemm` component, §6 status).

Every claim is tagged:

- **[live]** — observed directly on the robot in this session
- **[src]** — read from source on the robot (vendor headers, third-party code)
- **[web]** — from published documentation
- **[?]** — believed but _not_ verified; do not build safety-critical logic on these

---

## 1. Compute and network

Two computers, plus a LiDAR that is its own network host. **[live]**

| Node                       | Address                                | Role                                                      |
| -------------------------- | -------------------------------------- | --------------------------------------------------------- |
| Jetson Orin NX (`g1-orin`) | `192.168.123.164` (eth0), DHCP (wlan0) | General-purpose host. SSH. Where our bridge runs.         |
| Control board              | `192.168.123.161`                      | Publishes the robot's DDS topics. No SSH (only TCP 9991). |
| Livox Mid-360 LiDAR        | `192.168.123.120`                      | Direct network peer on the internal LAN.                  |
| Mac (when cabled)          | `192.168.123.99`                       | Static, set by hand — the internal LAN has **no DHCP**.   |

The control board pushes ~24 MB/s to multicast `239.255.0.1` and has no wireless interface.
That is the whole reason the bridge runs onboard — see SPEC §10.2.

**Wi-Fi:** `EDU-Special`, WPA2-PSK, MAC-whitelisted. `wlan0` is `14:0a:02:f0:63:f6`. **[live]**

**Address it by name, not by number.** The `wlan0` lease has moved twice (`10.4.64.27` →
`10.10.32.19`), and one of those old addresses later answered as a different device
entirely — so a stale IP does not fail closed, it fails *misleadingly*. `avahi-daemon` runs
onboard and **`g1-orin.local` resolves from the Mac over `EDU-Special`** — verified
2026-08-12, both `dscacheutil` and `ping`. **[live]** Use that in `~/.ssh/config`,
`BRIDGE_URL`, and anywhere else the robot needs naming. A static DHCP reservation for
`14:0a:02:f0:63:f6` would work too, but mDNS needs no cooperation from the school's network
team.

The SSH user is **`unitree`** (home `/home/unitree`). `c3po` and `c3po-wire` are `Host`
aliases in the Mac's `~/.ssh/config`, not accounts on the robot. **[live]**

**Route trap.** The vendor `eth0` profile (`unitree1`) installs `default via 192.168.123.1`
at metric 20100, beating Wi-Fi's default — but that gateway never resolves in ARP, so all
egress black-holes and the robot has _no internet_ even with Wi-Fi up. Fixed with
`nmcli connection modify unitree1 ipv4.never-default yes`, which keeps the on-link
`192.168.123.0/24` route DDS needs. **Re-check after any Unitree OTA.** **[live]**

---

## 2. DDS layer

**Two incompatible CycloneDDS versions coexist.** **[live]**

| Version | Where                                                  | Config schema                                  |
| ------- | ------------------------------------------------------ | ---------------------------------------------- |
| 0.7.0   | ROS 2 Foxy debs (`ros-foxy-cyclonedds`)                | `<NetworkInterfaceAddress>`                    |
| 0.10.2  | `/usr/local/lib`, `~/cyclonedds_ws/install/cyclonedds` | `<Interfaces><NetworkInterface/></Interfaces>` |

Feeding a modern `<Interfaces>` config to the Foxy stack fails outright:
`config: //CycloneDDS/Domain/General: Interfaces: unknown element`.

Consequences for us:

- Our bridge pins `cyclonedds==0.10.2`, which **matches the prebuilt library already on the
  Jetson** — so `CYCLONEDDS_HOME=~/cyclonedds_ws/install/cyclonedds` needs no source build,
  unlike the Mac. That library is a third-party 2023 build in a home directory, so an OTA
  could remove it; that's the main argument for eventually containerizing.
- **Do not depend on the ROS 2 CLI.** `ros2 topic list` segfaults (exit 139) both with and
  without `--daemon` since the last reboot. It worked earlier only because the boot-time
  daemon answered over a local socket without the CLI touching DDS. Use the 0.10.2 Python
  bindings instead.
- CycloneDDS picks arbitrarily among `eth0`, `docker0`, `wlan0` — logged verbatim as
  `selected arbitrarily from: eth0, docker0, wlan0`. Onboard, the interface **must** be
  pinned to `eth0` or the bridge may see none of the robot. This is the `DDS_INTERFACE`
  work item.

---

## 3. The G1 Loco API

Authoritative source: `unitree_ros2/example/src/include/g1/g1_loco_client.hpp`, vendored on
the robot. Requests go to `/api/sport/request`, responses to `/api/sport/response`. **[src]**

| api_id      | Call                                                                               | Implemented in `apps/bridge`?                                      |
| ----------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 7001–7006   | GET fsm*id, fsm_mode, balance_mode, swing/stand height, phase *(7006 deprecated)\_ | **7001/7002 yes** (posture readback); rest no                      |
| 7101        | SET_FSM_ID — postures, `Damp() = SetFsmId(1)`                                      | **yes**                                                            |
| 7102        | SET_BALANCE_MODE                                                                   | no                                                                 |
| 7103 / 7104 | SET_SWING_HEIGHT / SET_STAND_HEIGHT                                                | no                                                                 |
| **7105**    | **SET_VELOCITY** — `{"velocity":[vx,vy,omega],"duration":d}`                       | **yes** — accepted by firmware; non-zero velocity still unexecuted |
| 7106        | SET_ARM_TASK — gestures                                                            | **yes**                                                            |
| 7107        | SET_SPEED_MODE                                                                     | no                                                                 |
| 7110        | SWITCH_TO_USER_CTRL **[web]**                                                      | no                                                                 |

`SetVelocity(vx, vy, omega, duration = 1.0f)` — note the **default duration of 1 second**.

### Verified live 2026-08-11 — first commands ever sent to this robot

| Call                           | Result                                               |
| ------------------------------ | ---------------------------------------------------- |
| 7001 `GET_FSM_ID`              | `code=0`, `{"data":802}`                             |
| 7002 `GET_FSM_MODE`            | `code=0`, `{"data":0}`                               |
| 7003 `GET_BALANCE_MODE`        | `code=7301` — declined in this state                 |
| 7105 `SET_VELOCITY(0,0,0,1.0)` | **`code=0`** — JSON shape confirmed against firmware |
| 7106 `SET_ARM_TASK` (wave)     | **arm moved**, `code=0` after **4.19 s**             |

Two things this settled that no amount of reading would have:

**The services have different ack semantics.** `sport` answers promptly.
`arm` answers **on completion of the motion** — 4.19 s for a wave. With the SDK's
default timeout, every gesture returned `RPC_ERR_CLIENT_API_TIMEOUT` (3104) _while the
robot was visibly performing it_. That's a false failure in the dangerous direction: an
operator or LLM reads "failed" and retries a command the robot already obeyed. Fixed by
sizing timeouts to motion duration (`g1_rpc.ARM_TIMEOUT_S`), **not** by going
fire-and-forget, which would have discarded genuine error reporting.

**`mode_machine` ≠ FSM id.** Read simultaneously: `mode_machine=5` while `fsm_id=802`.
Never label one with the other.

⚠️ **The label for FSM 802 is suspect.** It was read while the robot stood perfectly
still, so `"run"` is very likely wrong — 802 is probably a general "controller
active / main operation" state. Resolve by watching `fsm_id` transition during a
supervised motion window. Don't build preconditions on that label.

### Correction to our own code ✅ fixed

`apps/bridge/src/bridge/sdk/g1_rpc.py`'s docstring used to state _"G1 uses a single api_id
per service (7101 posture, 7106 arm gesture)"_, which was wrong and risked leaving someone
thinking velocity control wasn't available over this path. It now says the service spans
7001..7107 and that callers register each api_id they intend to use.

Note api_ids are scoped **per service** — `7107` means `SET_SPEED_MODE` on the sport service
but something different on the arm service. Don't treat the numbers as globally unique.

---

## 4. Sensors and peripherals

| Peripheral            | How it attaches                                                            | Status                                                                                                                                                                               |
| --------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Livox Mid-360 LiDAR   | Ethernet, `192.168.123.120`                                                | **[live]** responds; ports 56100 cmd / 56200 push / 56300 points / 56400 IMU **[web]**                                                                                               |
| Intel RealSense D435i | USB `8086:0b3a`, `/dev/video0–5`                                           | **[live]** driven by the third-party container, publishes 5 `/camera/*` topics                                                                                                       |
| Dex3-1 hands          | RS485 behind an FTDI **FT4232H** quad UART (`0403:6011`), `/dev/ttyUSB0–3` | **[live]** bus present. **Not IP-addressed.** Software-visible via `/lf/dex3/{left,right}/state` and `/api/dex3_msg_controller`                                                      |
| G1 head/chest cameras | USB, via `/unitree/module/video_hub_pc4`                                   | **[live] degraded** — `videohub_pc4_chest` is running against `/dev/video10`, but that node **no longer exists**; the head `videohub_pc4` isn't running at all. Worth investigating. |
| Audio                 | Jetson Orin NX APE (capture), HDA/HDMI (playback)                          | **[live]** enumerated; the G1's own mic array/speaker path is **[?]** not yet identified                                                                                             |

The LiDAR being a plain network peer closes SPEC §17.2's open question: no WebRTC shim and no
SSH-tunnelled driver needed — anything on the internal LAN can talk to it directly.

---

## 5. Third-party stack sharing this robot

A colleague (`OliverJones08` in git; the `gemm` stack on the robot) runs **two independent
pieces**, not one. Both return on every boot. **[live]**

**1. `gemm-bringup`** — docker container, `--network host`, `restart=unless-stopped`. A full
Nav2 autonomy stack:

```
nav2_controller  nav2_planner  nav2_behaviors  nav2_bt_navigator
nav2_velocity_smoother  nav2_lifecycle_manager
realsense2_camera_node  foxglove_bridge (:8765)  gemm_navigation/odom_tf_bridge
static_transform_publisher (base_link -> lidar_link)
```

**2. `gemm-ai.service`** — a **systemd unit**, enabled and active, running
`~/gemm_ai/.venv/bin/python -m backend.main` under Python 3.8 with its *own* CycloneDDS copy
at `~/gemm_ai/.cyclonedds`. A voice/vision assistant. Discovered 2026-08-12. **[live]**

Two consequences, both of which cost time if you don't know about it:

- **It binds `0.0.0.0:8000`**, which is the port our bridge would otherwise have taken. Ours
  runs on **8001** because of it.
- **`stop_gemm` does not stop it.** That script matches
  `docker ps --filter name=^gemm`, and a systemd unit is not a container. "gemm stopped"
  therefore does not mean "the robot is entirely ours" — check
  `systemctl is-active gemm-ai` as well.

It is **not** a motion risk, which is the question that actually matters here: its own
source contains no `SetVelocity`, no api_id 7105, and no `LocoClient`/`SportClient` use —
the only hits for those were inside its vendored `unitree_sdk2py`. It only *subscribes*, to
`rt/audio_msg` (the embedded ASR topic). So it does not touch the one-commander invariant
and does not need stopping before driving. Re-check if their backend grows. **[live]**

**Collision risk is currently low, by their design.** Nav2 publishes `/cmd_vel`, but the
bridge that would forward it to the robot — `cmd_vel_to_loco` — is **disabled by default** and
was absent from the running process list. Their launch files gate it loudly:

> `cmd_vel_to_loco`, el puente que hace caminar al robot — **APAGADO por default**
> "Reenviar /cmd_vel a la API de locomocion del G1. **EL ROBOT CAMINA.**"

It is one launch argument away from being live. **There is no interlock between their stack
and ours.** Before either party sends motion, coordinate — two independent controllers
commanding the same legs is the obvious way to break this robot.

Their `cmd_vel_to_loco` also carries two design decisions worth adopting rather than
reinventing (their reasoning, verbatim in intent):

- **`duration = 1.0s`, not `Move()`'s 864000s.** If the commanding process dies, the robot
  brakes within a second instead of walking into a wall for ten days. This is an _intrinsic
  deadman at the firmware level_ and is stronger than any watchdog we write in Python.
- **Fire-and-forget.** The vendor C++ client blocks up to 5s per call, which is impossible at
  10 Hz.

Their own caveat, and it applies to us too: _"Nada de esto esta verificado todavia: no hubo
ventana con el robot"_ — none of that path is tested against hardware. **[?]**

---

## 6. What this changes for C3PO

Ordered by what unblocks the most. Status as of **2026-08-12**.

1. ✅ **`DDS_INTERFACE=eth0` override.** Read in `mcp_server.py` and passed to `init_dds`;
   the robot's `.env` sets it. Confirmed in the running bridge's log: `interface=eth0`.
2. ✅ **api_id 7105 for `walk_to`/`turn`.** The real path issues `SET_VELOCITY` RPCs
   (`_locomotion.py`, `g1_protocol.API_ID_LOCO_SET_VELOCITY`). `CMD_TOPIC =
   "rt/run_command/cmd"` still exists but is now the *sim-only* path, as
   `g1_protocol.py` labels it. **Still unexecuted on real hardware** — see below.
3. 🔧 **Link watchdog scope.** Built and **off by default** (`LINK_WATCHDOG` unset →
   `watchdog.disabled` in the log). The firmware's 1 s `SET_VELOCITY` deadman remains the
   primary stop; ours is the second layer for non-velocity cases.
4. ✅ **`g1_rpc.py` docstring fixed** — it now states the service spans 7001..7107.
5. **Don't build on the ROS 2 CLI**, and don't assume the vendor camera path works — it's
   currently degraded.
6. 🔧 **Interlock with the `gemm` stack.** Now enforced mechanically by `scripts/robot/`
   (starting either stack stops the other; `run_c3po` refuses while `cmd_vel_to_loco` is
   alive). That is a backstop, not a substitute for the two teams agreeing who drives —
   and note it does **not** cover `gemm-ai.service` (§5).

### Verified live 2026-08-12 — the read path, end to end

The bridge now runs onboard the Jetson (`SIM_MODE=real`, domain 0, `eth0`) and serves its
MCP tool surface over streamable-http on `127.0.0.1:8001`. `get_state` through that server
returns live control-board data: **[live]**

| Field                       | Reading                                        |
| --------------------------- | ---------------------------------------------- |
| `posture`                   | `zero_torque`                                  |
| `fsm_id` / `fsm_mode`       | `0` / `0`                                      |
| `mode_machine`              | `5`                                            |
| `motor_count`               | `35`                                           |
| `lowstate_age_s`            | ~0.02–0.04 (fresh, `rt/lf/lowstate`)           |
| `pose_source` / `pose_age_s`| `odom` (`rt/odommodestate`) / ~0.01–0.02       |
| `faults`                    | none                                           |

Two things worth recording. **`fsm_id=0` reads alongside `posture=zero_torque`** — the first
confirmed entry in the FSM-id → posture map, and directly relevant to the open question of
which id accepts velocity. It says nothing either way about the suspect `802` label in §3;
that still needs a supervised motion window to resolve.

And `mode_machine` read **5** here while `fsm_id` was 0, having read 5 while `fsm_id` was 802
on 2026-08-11. Same `mode_machine`, different `fsm_id`, on two occasions — which settles that
they are genuinely independent fields rather than two views of one value.

### Still unverified — needs a supervised robot window

Nothing below changed on 2026-08-12: **no motion command was sent.** The install verified
the read path only.

- Whether api_id 7105 and its JSON actually move *this* robot (the call is accepted; a
  non-zero velocity has never been executed)
- Axis directions and sign conventions for `[vx, vy, omega]`
- Which FSM id the robot must be in before velocity commands are accepted
- Real velocity scaling (the sim gains in `_locomotion.py` are fitted to a policy that runs
  at ~10–15% of commanded velocity and will **not** transfer)
