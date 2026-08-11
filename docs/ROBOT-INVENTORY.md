# G1 Robot Inventory — what's actually on the machine

Companion to `SPEC.md`. SPEC says what we intend to build; this says what the hardware
actually presents, verified against the physical robot on **2026-08-11**.

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

| api_id      | Call                                                                              | Implemented in `apps/bridge`?             |
| ----------- | --------------------------------------------------------------------------------- | ----------------------------------------- |
| 7001–7006   | GET fsm_id, fsm_mode, balance_mode, swing/stand height, phase _(7006 deprecated)_ | no                                        |
| 7101        | SET_FSM_ID — postures, `Damp() = SetFsmId(1)`                                     | **yes**                                   |
| 7102        | SET_BALANCE_MODE                                                                  | no                                        |
| 7103 / 7104 | SET_SWING_HEIGHT / SET_STAND_HEIGHT                                               | no                                        |
| **7105**    | **SET_VELOCITY** — `{"velocity":[vx,vy,omega],"duration":d}`                      | **no — this is the `walk_to`/`turn` gap** |
| 7106        | SET_ARM_TASK — gestures                                                           | **yes**                                   |
| 7107        | SET_SPEED_MODE                                                                    | no                                        |
| 7110        | SWITCH_TO_USER_CTRL **[web]**                                                     | no                                        |

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

### Correction to our own code

`apps/bridge/src/bridge/sdk/g1_rpc.py`'s docstring states _"G1 uses a single api_id per
service (7101 posture, 7106 arm gesture)"_. **That is wrong.** The loco service exposes the
full 7001–7107 range; we simply only use two of them. The docstring should be fixed before it
misleads someone into thinking velocity control isn't available over this path.

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

A colleague (`OliverJones08` in git; the `gemm` stack on the robot) runs **`gemm-bringup`** —
a full Nav2 autonomy stack, `--network host`, `restart=unless-stopped`, so it returns on every
boot. **[live]**

```
nav2_controller  nav2_planner  nav2_behaviors  nav2_bt_navigator
nav2_velocity_smoother  nav2_lifecycle_manager
realsense2_camera_node  foxglove_bridge (:8765)  gemm_navigation/odom_tf_bridge
```

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

Ordered by what unblocks the most.

1. **`DDS_INTERFACE=eth0` override** (`sdk/connection.py`). Without it, onboard DDS is a coin
   flip across three interfaces. Cheap, and everything else depends on it.
2. **Implement api_id 7105 for `walk_to`/`turn`.** `_locomotion.py` still hardcodes
   `CMD_TOPIC = "rt/run_command/cmd"`, which real firmware doesn't subscribe to. The real path
   is now fully specified above. Adopt `duration≈1.0s` and re-issue at loop rate.
3. **Reconsider the link watchdog's scope.** SPEC §10.3 assumed we'd build the deadman
   ourselves. With `duration=1.0s`, the firmware already provides one. Our watchdog becomes a
   second layer for the _non-velocity_ cases (a posture command mid-transition), not the
   primary stop mechanism. This likely makes it smaller than specced.
4. **Fix the `g1_rpc.py` docstring** (§3 above).
5. **Don't build on the ROS 2 CLI**, and don't assume the vendor camera path works — it's
   currently degraded.
6. **Agree an interlock with the `gemm` stack** before any motion testing.

### Still unverified — needs a supervised robot window

- Whether api_id 7105 and its JSON match this robot's firmware
- Axis directions and sign conventions for `[vx, vy, omega]`
- Which FSM id the robot must be in before velocity commands are accepted
- Real velocity scaling (the sim gains in `_locomotion.py` are fitted to a policy that runs
  at ~10–15% of commanded velocity and will **not** transfer)
