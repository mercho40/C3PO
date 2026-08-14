# G1 Robot API — what we send and what comes back

Companion to `ROBOT-INVENTORY.md` (what the hardware presents) and `DEPLOYMENT.md` (where
each piece runs). This file is the **software interface**: services, api_ids, parameter
shapes, error codes, DDS topics and message layouts.

Assembled **2026-08-13/14** from a full read of the vendor source trees on the robot plus
the live observations of that session, on top of the earlier live work recorded in
`ROBOT-INVENTORY.md` §3 and §6. The robot became unreachable at the end of that session;
**nothing here has been re-verified since**, and every experiment listed is blocked on the
next window.

Every claim is tagged:

- **[live]** — observed on the robot
- **[src]** — read from source on the robot (vendor headers, examples, IDL, recordings)
- **[web]** — published documentation. **Two distinct bodies, both unverified against this
  robot**: `G1-WEB-RESEARCH.md` (third-party/community) and, folded in **2026-08-13**,
  **Unitree's own official G1 developer documentation** (45 pages). A `[web]` claim here is a
  hypothesis, never a fact — official or not.
- **[?]** — believed, not verified. Do not build safety-critical logic on these.

### Citing the official Unitree documentation

Official is not the same as correct, and on this doc set it is not even the same as
G1-specific. Rules that came straight out of reading it: **[web]**

1. **Always carry the page slug and the vendor's update date.** They range from 2024-09 to
   2026-07 and they contradict each other. Where two pages disagree, prefer the newer —
   with one exception below.
2. **Several G1 pages are demonstrably copy-pasted from other robots.**
   `motion_witcher_service_interface` documents "the current **Go2** form … Wheel-Foot
   Form"; `sport_services_interface` calls the G1 a "**robot dog**" in one remark;
   `inspire_dfx_dexterous_hand` describes the **H1**'s USB layout and links H1 pages;
   `about_G1`'s "development computing unit" table is an **Intel** spec sheet pasted onto an
   Arm SoC; `remote_control_data`'s decode snippet types the message as the **Go2**
   `LowState_`. Treat wrong-robot residue as the default hypothesis, not the exception.
3. **The exception to "newer wins": struct layouts.** `basic_services_interface`
   (2025-10-21) publishes a `LowState_` missing the leading `version` field and a
   `MotorState_` carrying Go2-only `q_raw/dq_raw/ddq_raw` with `vol`/`sensor` swapped —
   while the older `dexterous_hand` (2025-02-10) matches our shipped IDL exactly. **Never
   hand-write IDL from a doc page.** Our venv's generated IDL is the arbiter; see §9.3.
4. **The docs publish no api_ids at all.** Not one, for any service, across 45 pages — only
   error codes. Every api_id in this file remains `[src]` from the robot's own headers, and
   the documentation cannot corroborate or refute a single one.
5. **The robot wins.** Where an official page contradicts a `[live]` or `[src]` finding of
   ours, ours stands and the conflict is recorded. Those usually mark a firmware or variant
   difference, not our error.

### The two vendor source trees, and which one counts

Both are on the robot and **they disagree**. Knowing which is which is the difference
between a real api_id and one that does not exist on our firmware. **[live]**

| Tree            | Path                                                     | Cloned     | Commit     | Has 7110/7111? | Has arm 7108/7113? |
| --------------- | -------------------------------------------------------- | ---------- | ---------- | -------------- | ------------------ |
| `unitree_ros2`  | `~/gemm/ros2_ws/src/external/unitree_ros2`               | 2026-07-20 | `668d1ec5` | **no** (stops at 7107) | **no**     |
| `unitree_sdk2`  | `~/gemm_ai/xr_teleoperate/vendor/unitree_sdk2`           | 2026-08-13 | `21d0a3b2` | yes            | yes                |

**`unitree_ros2` is the tree that matches this firmware.** The C++ SDK clone is a week
newer than the robot's own OTA and carries constants the control board may not serve. Treat
anything that exists only in the newer clone — 7110, 7111, arm 7108/7113, the whole `agv`
service, `terminations.hpp` — as *declared in an SDK*, **not** as *implemented by firmware
1.5.3.8*. The cheap discriminator is a `3203 Api not implement error` response.

**Firmware identity, first hard reading: package `1.5.3.8`, product `G1_Edu+`.** From
`/unitree/ota/update/1.5.3/package_1.5.3.8_G1_Edu+_upk`; `version.json` carries per-module
versions (`master_service_pc4` 1.0.0.2, `unitree_patch_pc4` 1.0.0.6, `video_hub_pc4`
1.0.2.3) and an **empty** `"Package"` field, so `version.json` alone does not stamp the
firmware. **[live]** This does **not** give the `ai_sport` or `vui_service` versions — those
live on the control board, which has no SSH.

**A note on dates.** The robot's clock is `Asia/Shanghai` (CST, +0800) and the UTC time is
correct, so on-robot timestamps read roughly a day ahead of local expectation. **[live]**
"2026-08-14 01:40 CST" and "the evening of 2026-08-13 locally" are the same moment. Both
dates appear below because both appear in the logs.

---

## 1. The service model — and the trap that has bitten us twice

### 1.1 Name → topic

Every RPC service is a *name*. The SDK mechanically derives the topic pair: **[src]**

```cpp
// include/unitree/robot/channel/channel_namer.hpp
ROBOT_SDK_CHANNEL_PREFIX        = "rt/api/";
ROBOT_SDK_CHANNEL_SUFFIX_CLIENT = "/request";
ROBOT_SDK_CHANNEL_SUFFIX_SERVER = "/response";
```

So service `S` is always `rt/api/S/request` (we publish) and `rt/api/S/response` (we
subscribe). The Python SDK builds the same names in `core/channel_name.py::
GetClientChannelName`. ROS 2 clients write `/api/S/request` — the `rt/` prefix is what ROS 2
adds on the DDS side, so those are the *same topic*, not two topics. **[src]**

Both directions carry the generic envelope, types shipped by `unitree_sdk2py`: **[src]**

```
unitree_api::msg::dds_::Request_
  header.identity.id       int64   # correlation id
  header.identity.api_id   int64   # <-- the only thing that selects the call
  header.lease.id          int64
  header.policy.priority   int32
  header.policy.noreply    bool
  parameter                string  # JSON, or empty
  binary                   seq<uint8>

unitree_api::msg::dds_::Response_
  header.identity          (echoes id/api_id)
  header.status.code       int32   # 0 = OK; see §1.5
  data                     string  # JSON, or empty
  binary                   seq<uint8>
```

The client-side API-version string (`"1.0.0.0"`, `"1.0.0.14"`, …) is **never put on the
wire** — `Client._SetApiVerson` only stores it. Our bridge omitting it is harmless. **[src]**

### 1.2 The trap: api_ids are scoped per service, not globally

**This has already cost us twice, and it is the single most important thing in this
document.** The same integer means different things on different services, and the service
name is chosen by which `Client(...)` you instantiate — nothing on the wire warns you.

| api_id | On `sport`                        | On `arm`                    | On `motion_switcher` | On `robot_state`               | On `voice` |
| ------ | --------------------------------- | --------------------------- | -------------------- | ------------------------------ | ---------- |
| 1001   | —                                 | —                           | `CHECK_MODE` (read)  | `SERVICE_SWITCH` (**write**)   | `TTS`      |
| 1003   | —                                 | —                           | `RELEASE_MODE` (**write**) | `SERVICE_LIST` (read)    | `START_PLAY` |
| 7106   | `SET_ARM_TASK` (task ids 0–3)     | `EXECUTE_ACTION` (catalogue 11–99) | —             | —                              | —          |
| 7107   | `SET_SPEED_MODE` (**a motion command**) | `GET_ACTION_LIST` (**a pure read**) | —          | —                              | —          |

**[src]**

Two concrete failure shapes:

- **7106.** The vendor's `WaveHand()` is `sport`/7106 with `{"data":0}`. Our bridge's `wave`
  is `arm`/7106 with `{"data":26}`. Both are real and both work; they are not two spellings
  of one call. Send the arm catalogue's `26` to the **sport** service and it is not a wave —
  it is an out-of-range task id, answered `7303 Invalid task id`. **[src]**
- **7107.** Reading the gesture catalogue is `arm`/7107, a pure query. The same number on
  `sport` is `SET_SPEED_MODE`, which changes how fast the robot walks. A copy-paste of the
  service name turns a read into a motion command with no error. **[src]**

And a number collision that is *not* an api_id at all: `motion_switcher`'s **error** codes
are 7001–7009 (§3), while `sport`'s **api_ids** are 7001–7006. Same integers, unrelated
meanings. **[src]**

`ROBOT-INVENTORY.md` §3 already carries the short form of this warning; treat this table as
its expansion. `apps/bridge/src/bridge/sdk/g1_rpc.py` gets the structure right — it builds
one `_G1Client` per service name and `Client.__CheckApi` refuses any api_id not registered
on that client — so the routing is safe as long as nobody registers an id on the wrong
client. **[src]**

### 1.3 Services found on this robot

| Service          | API version | Topics                          | Where declared                                   | Evidence |
| ---------------- | ----------- | ------------------------------- | ------------------------------------------------ | -------- |
| `sport`          | `1.0.0.0`   | `rt/api/sport/{request,response}`   | firmware-matched ROS 2 tree + newer SDK      | **[src]**, exercised **[live]** |
| `arm`            | `1.0.0.14`  | `rt/api/arm/{request,response}`      | both trees                                  | **[src]**, exercised **[live]** |
| `motion_switcher`| `1.0.0.1`   | `rt/api/motion_switcher/{request,response}` | G1 header in the ROS 2 tree          | **[src]**, answered **[live]** |
| `robot_state`    | `1.0.0.1`   | `rt/api/robot_state/{request,response}` | Go2 header; colleague verified QoS on this unit | **[src]** |
| `voice`          | `1.0.0.0`   | `rt/api/voice/{request,response}`    | both trees                                  | **[src]**, never called by us |
| `agv`            | `1.0.0.1`   | `rt/api/agv/{request,response}`      | newer SDK only — wheeled G1-D               | **[src]**, almost certainly absent here |

Declared in the SDK but **Go2-scoped**, with no G1 counterpart anywhere on this machine:
`vui`, `obstacles_avoid`, `config`, `videohub` / `front_videohub` / `back_videohub`,
`uwbswitch`. **[src]** In particular the G1's vendor obstacle avoidance, if it exists at
all, is **not** the `obstacles_avoid` API.

Names that appear in **no** vendor source, binary or config on this robot — a filesystem-wide
grep over `/unitree`, both vendor trees and both SDKs returns zero hits:
`action_store`, `/api/gesture`, `/api/gpt`, `/api/vla`, `/api/audiohub`,
`/api/dex3_msg_controller`. **[live]** The last one is cited in `ROBOT-INVENTORY.md` §4 and
`MENTAL-MODEL.md`; its only occurrences anywhere are our own docs. **Treat it as unsourced
and strike it.** The one survivor of that word list is `slam_nav`, and only as a key in
`/unitree/etc/master_service/protect` (`{"slam_nav": 0}`) — a service name in a supervisor
config with no code behind it on this host. **[live]**

**`slam_operate` has been removed from that list.** Unitree documents it as a real service —
`SERVICE_NAME = "slam_operate"`, `VERSION = "1.0.0.1"`, api_ids 1801 start mapping / 1802 end
mapping / 1804 initialize pose / 1102 pose navigation / 1201 pause / 1202 resume / 1901 close
slam, all JSON — gated behind the `unitree_slam` **and** `lidar_driver` services being
switched on. **[web]** (`slam_navigation_services_interface`, 2026-07-20.) Our zero-hit grep
was run on the Jetson, and §8 already establishes that topics and services can be absent
until switched on. So the correct statement is **"documented, not enabled or not installed on
this unit"**, and `robot_state` 1003 `ServiceList` settles it. Note its response envelope is
unlike every other service: `{"succeed":bool,"errorCode":int,"info":str,"data":{}}` **inside**
`data`, so `rpc_code 0` does not imply success there. **[web]** Two operational warnings come
with it, in §10 and §13.

**Two namespaces, one word.** `robot_state`'s `ServiceSwitch` takes *process/service* names —
Unitree publishes the list as `ai_sport` (Main Motion Control Service), `basic_service`,
`g1_arm_example` (Upper Limb Motion Service), `vui_service` (Audio and Lighting Control
Service), `unitree_slam` (Navigation Service), plus `lidar_driver` named elsewhere. **[web]**
Those are **not** the RPC service names of §1.1. `vui_service` in that list does not
contradict the finding above that the RPC service `vui` is Go2-scoped with no G1 counterpart:
`rt/api/vui/*` and the switchable process `vui_service` are different things. Keep the lists
apart or someone will "correct" one with the other.

**PC1 being closed is vendor policy, not a missing key.** `architecture_description`
(2025-04-30): *"PC1 is dedicated to the Unitree motion control program and is **not open to
the public**. Developers can only use PC2 for secondary development."* PC1 = `192.168.123.161`,
PC2/NX = `192.168.123.164`. `quick_development` adds *"Development on Mac and Windows systems
is currently not supported."* **[web]** So §11 cannot be settled by reading, ever, and the
`SIM_MODE=real` decision to relocate the bridge onto the Jetson (CLAUDE.md, SPEC §10) is what
the vendor prescribes rather than a workaround we invented.

**The FSM does not live on the Jetson.** `/unitree/module/` holds exactly two modules
(`master_service`, `video_hub_pc4`), the vendor's own install bundle confirms that is the
complete "pc4" payload by design, and `strings` on the `master_service` binary yields no
`fsm` / `ai_sport` / `motion_switch` / `loco` hits at all. **[live]** Every motion service
runs on the control board at `192.168.123.161`, which has no SSH. **No further source
reading of the FSM owner is possible from any host we control** — this is why §11 is
unresolved by reading and needs experiments.

### 1.4 There is no ownership arbitration

`grep -rn 'Client(.*true)'` across the whole SDK include tree returns **zero** hits: every
vendor client is constructed with `enableLease = false`, including
`LocoClient() : Client(LOCO_SERVICE_NAME, false)`. **[src]**

The lease mechanism exists — api_id **101 LEASE_APPLY** (`{"name": str}` →
`{"id": int64, "term": int64}`), **102 LEASE_RENEWAL**, default term 1 000 000 µs — but
nothing uses it. **[src]** So the robot does not arbitrate: *whoever publishes to the request
topic is obeyed*. Our one-commander invariant (`DEPLOYMENT.md` §2) is enforced entirely by
our own scripts and by the two teams agreeing, never by the firmware.

The corollary is a useful tell: codes **3205 / 3206 / 3207** (lease denied / not in cache /
already in cache) should be impossible. If one ever appears, something outside this SDK has
taken a lease, and that would be a direct answer to §11.

### 1.5 Generic RPC codes (every service)

| Code | Meaning                    | Side       | Notes |
| ---- | -------------------------- | ---------- | ----- |
| 0    | OK                         | —          | See the trap in §3: `0` does **not** mean the request had an effect |
| 3001 | Unknown                    | server     | |
| 3102 | Send request error         | client     | |
| 3103 | Api is not registed        | **client** | You called an api_id you never registered on that client |
| 3104 | Call api timeout           | **client** | Says nothing about robot state. This is what produced our false gesture failures — see §6.5 |
| 3105 | Response api not match     | client     | |
| 3106 | Response data error        | client     | |
| 3107 | Lease is invalid           | client     | |
| 3201 | Send response error        | server     | **Never reaches the client** — the docs state it "occurred on the server and will not be returned to the client". Seeing one client-side should be treated as impossible, not rare **[web]** |
| 3202 | Server internal error      | server     | |
| 3203 | **Api not implement**      | **server** | The firmware does not serve that api_id. The discriminator for every "does this firmware have X?" question |
| 3204 | Api parameter error        | server     | |
| 3205 | Lease denied *(header)* / **"Request rejected"** *(docs)* | server | Not necessarily proof someone took a lease — the official gloss is a plain refusal **[web]** |
| 3206–3207 | Lease errors          | server     | Should be unreachable — see §1.4 |

**[src]**, with the 3201 and 3205 remarks **[web]** from `dds_services_interface`.

**The default RPC timeout is 1 second.** `SetTimeout(float seconds)` — *"If no timeout is
set, the default timeout time is 1 second."* **[web]** That is the documented number behind
§6.6: the arm service acks on motion completion (4.19 s for a wave), so every gesture returned
`3104` against a 1 s default while the robot was visibly obeying.

Two reads on the generic client we do not use and should: **[web]**

- **`GetServerApiVersion()`** — returns the *server's* API version for a service. Zero risk,
  and the `arm_action_interface` example compares client against server before proceeding.
  Between this and `robot_state` 1006 (§8) we could finally version the control board.
- **`ChannelSubscriber::GetLastDataAvailableTime()`** — monotonic microseconds since boot,
  `-1` if the channel was never initialised. A cleaner source for `lowstate_age_s` and the
  `stale_lowstate_*` fault than our own receipt-time bookkeeping, because it distinguishes
  "never started" from "started but silent".

One thing **not** to copy: `ChannelFactory::Init`'s third argument, `enableSharedMemory`. The
docs say to leave it false "when developing applications outside of G1", which tempts a
reader into enabling it now that our bridge runs onboard. Don't — the publishers we care
about live on the control board, a **different host**, so shared memory cannot help and can
only add failure modes. **[web]**

---

## 2. The `sport` service (loco)

Authoritative source: `~/gemm/ros2_ws/src/external/unitree_ros2/example/src/include/g1/
g1_loco_client.hpp`, the firmware-matched tree. **[src]** Note the G1's locomotion service
is literally named **`sport`** — it was renamed from `loco` and H1 still uses `loco`.
Material calling the G1 service `loco` is stale. The rename now has a precise threshold:
*"ai_sport >= 8.2.0.0 version is `LOCO_SERVICE_NAME = "sport"`; lower than this version
`LOCO_SERVICE_NAME = "loco"`."* **[web]** (`rpc_routine`, 2025-09-15.) This unit's `ai_sport`
is past 8.6.x — see the 801→802 renumber in §4.1 — so `sport` is right for us.

### 2.1 api_id table

| api_id | Call                   | Request parameter                               | Response `data`      |
| ------ | ---------------------- | ----------------------------------------------- | -------------------- |
| 7001   | `GET_FSM_ID`           | **empty** parameter string — *not* `"{}"`       | `{"data": <int>}`    |
| 7002   | `GET_FSM_MODE`         | as above                                        | `{"data": <int>}`    |
| 7003   | `GET_BALANCE_MODE`     | as above                                        | `{"data": <int>}`    |
| 7004   | `GET_SWING_HEIGHT`     | as above                                        | float **[web]**      |
| 7005   | `GET_STAND_HEIGHT`     | as above                                        | float **[web]**      |
| 7006   | `GET_PHASE` *(deprecated)* | as above                                    | float list **[web]** |
| 7101   | `SET_FSM_ID`           | `{"data": <int>}`                               | not parsed by any client |
| 7102   | `SET_BALANCE_MODE`     | `{"data": <int>}`                               | " |
| 7103   | `SET_SWING_HEIGHT`     | `{"data": <float>}`                             | " |
| 7104   | `SET_STAND_HEIGHT`     | `{"data": <float>}`                             | " |
| 7105   | `SET_VELOCITY`         | `{"velocity":[vx,vy,omega],"duration":<float>}` | " |
| 7106   | `SET_ARM_TASK`         | `{"data": <int>}` — **task ids 0–3 only** (§6.2) | " |
| 7107   | `SET_SPEED_MODE`       | `{"data": <int>}` — **0/1/2/3 only** (below)     | " |

**[src]** for the api_ids and for every `SET_*` parameter shape — those are read verbatim from
`g1_loco_client.hpp`. **The getter rows carry two caveats.** The header passes an **empty**
parameter string, so the widely-repeated *"empty string (C++) / `{}` (Python) — both
accepted"* line comes from `G1-WEB-RESEARCH.md` §4.1 and has never been tested against this
firmware **[web]**; send the empty string, which is what the vendor and our own `g1_rpc`
getter path do. And the getter *response* types beyond `{"data": <int>}` are **[web]** too:
no client on this robot parses them, so nothing on the machine confirms that 7004/7005 answer
a float or that 7006 answers a list.

**The range stops at 7107.** `7110 SWITCH_TO_USER_CTRL` — listed as `[web]` in
`ROBOT-INVENTORY.md` §3 — is **not in this client**, and neither is 7111. Do not assume they
exist on this firmware; see §2.4.

**No vendor client parses a response body for any setter** — only the int32 status code is
read. **[src]** So the setter response payload is undocumented, and "did it work?" cannot be
answered from the response. That is exactly the hole §11 falls into.

**`SET_SPEED_MODE` has a documented ladder, and it is scoped to running.** `speed_mode`
takes **0 : 1.0 m/s, 1 : 2.0 m/s, 2 : 2.7 m/s, 3 : 3.0 m/s**, and the function is described
as *"Adjust the maximum speed **in running mode**"*. **[web]** Two caveats before anyone uses
it: whether it affects 500/501 walk at all, or only 801/802 run, is unstated; and 3.0 m/s on
an LLM-drivable path is not a number we should ever be able to reach. Our table previously
carried `{"data": <int>}` with no range at all — clamp it to 0..3 and default it to 0.

**Also new: `SET_VELOCITY` is documented as firmware-clamped** — *"The program will
automatically set the cropping to the allowed range."* **[web]** That corrects §5.3's "no
velocity limit exists in any vendor source": a clamp exists, its bound is still not published.
The sign and axis convention remains undocumented after all 45 pages (§5.3).

### 2.2 Error codes

| Code | Symbol                                      | Message / meaning              |
| ---- | ------------------------------------------- | ------------------------------ |
| 7301 | `UT_ROBOT_LOCO_ERR_LOCOSTATE_NOT_AVAILABLE` | "LocoState not available."     |
| 7302 | `UT_ROBOT_LOCO_ERR_INVALID_FSM_ID`          | "Invalid fsm id."              |
| 7303 | `UT_ROBOT_LOCO_ERR_INVALID_TASK_ID`         | "Invalid task id."             |

**[src]** These three are declared with identical numbers *and identical strings* for G1, H2
and R1 — the error code alone never tells you which robot family you are talking to. **[web]**
There is no 7403. `7304 FSM ID return denied` is declared for R1 only, so a 7304 here would
be undocumented rather than impossible. **[web]**

Observed live: **7301** from `GET_BALANCE_MODE` at `fsm_id=802` on 2026-08-11 **[live]**.
**7302 has never been observed** — we have never deliberately sent an invalid id. That
matters more than it looks; see §11.4.

### 2.3 High-level method → wire mapping

Read verbatim from `g1_loco_client.hpp`. **[src]**

| Vendor method                  | Sends                                                    |
| ------------------------------ | -------------------------------------------------------- |
| `ZeroTorque()`                 | `SetFsmId(0)`                                            |
| `Damp()`                       | `SetFsmId(1)`                                            |
| `Squat()`                      | `SetFsmId(2)`                                            |
| `Sit()`                        | `SetFsmId(3)`                                            |
| `StandUp()`                    | `SetFsmId(4)`                                            |
| `Start()`                      | `SetFsmId(500)`                                          |
| `StopMove()`                   | `SetVelocity(0,0,0)` — **not** a special stop opcode      |
| `Move(vx,vy,vyaw,continuous)`  | `SetVelocity(vx,vy,vyaw, continuous ? 864000 : 1.0)`     |
| `HighStand()` / `LowStand()`   | `SetStandHeight(UINT32_MAX)` / `SetStandHeight(0)` — saturating sentinels |
| `BalanceStand()`               | `SetBalanceMode(0)`                                      |
| `ContinuousGait(flag)`         | `SetBalanceMode(flag ? 1 : 0)`                           |
| `SwitchMoveMode(flag)`         | **sends nothing** — a client-side latch only             |
| `WaveHand(turn_flag)`          | `SetTaskId(turn_flag ? 1 : 0)` → sport/7106              |
| `ShakeHand(stage)`             | `SetTaskId(2)` or `SetTaskId(3)` → sport/7106 (§6.2)     |

The **newer** SDK clone drops `StandUp()` entirely and replaces it with
`Squat2StandUp() = SetFsmId(706)` and `Lie2StandUp() = SetFsmId(702)`. **[src]** Both SDK
generations on this machine are post-2025-06, i.e. both are "500-era" — so *"our firmware is
from the era when `Start()` was 200"* is **not** a live hypothesis. **[src]**

### 2.4 7110 / 7111 — declared, unproven, and not a route to walking

From the newer SDK clone only: **[src]**

```cpp
ROBOT_API_ID_LOCO_SWITCH_TO_USER_CTRL     = 7110;   // sends {"data": false}
ROBOT_API_ID_LOCO_SWITCH_TO_INTERNAL_CTRL = 7111;   // {"data": 0|1|2}
enum class InternalFsmMode { LAST = 0, PASSIVE = 1, WALKRUN = 2 };
```

This closes a documented gap (`G1-WEB-RESEARCH.md` says "no numeric values published" for
`InternalFsmMode`). Unitree now documents the feature under the name **User Development
Mode**: *"an interface that temporarily switches the robot into debug mode, allowing a custom
controller to take over the robot and then exit flexibly … users can send low-level commands
to the motors using the topic `rt/user_lowcmd`."* **[web]**

**The conclusion stands; the reasoning in it was wrong.** Our previous justification was that
"the vendor example exits unless `fsm_id == 1` (PASSIVE)", generalised into "not a route to
walking". The docs explicitly support entering User Development Mode **from walking/running**
and returning via `InternalFsmMode::WALKRUN`, so that guard is an example-level choice, not a
firmware rule. 7110 is still not our route to walking — it hands the robot to *our* low-level
control, which is the opposite of loading a built-in walk policy — but say so for the right
reason. The vendor's own safety rule is worth carrying if we ever do use it: *"make sure that
both the first and last actions of your motion control are in a standard standing posture!
Otherwise, the robot may lose control."* **[web]**

**A wire-shape conflict to flag:** the documented prototype is `SwitchToUserCtrl()` with
**no parameters**, while the newer on-robot SDK clone sends `{"data": false}`. **[src]** vs
**[web]**. The robot's header wins for the wire shape; the doc suggests the bool may be
optional or ignored. Both ids remain absent from the firmware-matched tree — `3203` vs `3204`
vs `0` discriminates, but each is a write, so this stays low priority.

---

## 3. `motion_switcher` — the ownership service

**Run `CheckMode` first, before anything else, whenever the robot ignores a command.** This
is the highest-value diagnostic we have and it costs one read-only RPC.

### 3.1 Why it matters more than its size suggests

The `sport` service answers **`code 0`** in two completely different situations:

1. the FSM id you asked for is not enterable from where you are, and
2. **no motion controller is loaded at all**, so there is nothing to execute any FSM
   transition.

From the `sport` service those are **indistinguishable** — same code, same silence, same
`fsm_id` afterwards. `motion_switcher` 1001 separates them in one call. On 2026-08-14 it
returned an empty `name`, which is what "nothing is loaded" looks like, and in that state
7001/7002 return nothing at all, so `get_state` reports `fsm_id=None`, `fsm_mode=None`,
`posture=unknown`. **[live]** Anyone who sees those nulls and concludes "the robot is off"
or "DDS is broken" will be wrong: the DDS link is fine, the robot simply has no controller.

### 3.2 API

Service `motion_switcher`, api version `"1.0.0.1"`, topics
`rt/api/motion_switcher/{request,response}`. **[src]**

| api_id | Call           | Parameter                       | Response          | Effect |
| ------ | -------------- | ------------------------------- | ----------------- | ------ |
| 1001   | `CHECK_MODE`   | `"{}"` — an empty string also works per the C++ header | `{"name": "<mode>", "form": "<form>"}` | **getter, safe** |
| 1002   | `SELECT_MODE`  | `{"name": "<name_or_alias>"}`   | none              | **loads a controller** |
| 1003   | `RELEASE_MODE` | none / `"{}"`                   | none              | **unloads the controller** |
| 1004   | `SET_SILENT`   | `{"silent": true\|false}`       | none              | write |
| 1005   | `GET_SILENT`   | empty                           | `{"silent": bool}` | getter |

`form` is optional in the vendor's `fromJson` — it is only parsed
`if (json.find("form") != json.end())`. **[src]**

Error codes, all scoped to this service: 7001 parameter invalid, 7002 **switcher is busy**,
7003 event invalid, 7004/7005 name or alias invalid, 7006 check cmd execute error, 7007
select cmd execute error, 7008 release cmd execute error, 7009 save customize data error.
**[src]** Do not confuse these with `sport`'s api_ids 7001–7006 (§1.2).

**Conflict with the official table, recorded and not resolved.** Unitree's own list omits
**7003 entirely** and glosses **7005 as "Internal command execute error"**, not as a second
name/alias failure. **[web]** (`motion_witcher_service_interface`, 2025-11-12 — vendor's
typo, not ours.) The robot's header ships with our firmware and wins, but log the conflict:
a 7005 in the wild may be an execution failure rather than a bad name. The official 7004
gloss is *"Unsupport mode name"*, which is the code to expect if `SelectMode("ai")` is wrong
for this build.

### 3.3 Mode names

The vendor's own G1 example decodes `{form, name}` like this, for `form == "0"`: **[src]**

| `name`       | Service that owns the robot |
| ------------ | --------------------------- |
| `normal`     | `sport_mode`                |
| `ai`         | `ai_sport`                  |
| `advanced`   | `advanced_sport`            |
| *(empty)*    | "The motion control-related service is deactivated." |

**Live reading, 2026-08-14: `rpc_code 0`, `{'form': '0', 'name': ''}`.** **[live]** Empty
name — no controller loaded, the robot in what `xr_teleoperate` calls debug mode. The
official docs now confirm that reading from the other side: *"users can conveniently release
the G1 motion control mode via RPC and enter the debug mode developed by the user"*, and
*"the high-level motion service depends on the built-in operation control. After entering the
debugging mode, the built-in operation control is **completely exited** and the high-level
motion service becomes invalid."* **[web]** So the empty name plus 7001/7002 answering
nothing is not a fault — it is the documented consequence of debug mode. See §4.6.

**Our `normal`/`ai`/`advanced` decode is still the only source for these names.** The
official `motion_witcher_service_interface` page points at a *"Motion Control Mode Name"*
table for the valid `SelectMode` strings — **and that table does not exist**, on that page or
on any of the 45. The same page is Go2 copy-paste ("The current **Go2** form … **Wheel-Foot
Form**"), so it should not be treated as a G1 source at all. **[web]** The only indirect
corroboration is `robot_state`'s service list naming `ai_sport` the "Main Motion Control
Service" (§8), which lines up with our `ai` → `ai_sport` row.

### 3.4 Recovering a released mode — three routes, in increasing violence

1. **`SelectMode("ai")`** — motion_switcher 1002. What `xr_teleoperate`'s own
   `Exit_Debug_Mode()` does. **[src]** Expect `7004 Unsupport mode name` if the string is
   wrong for this build.
2. **The remote.** No documented button exits debug mode directly. The documented path back
   is **L2 + UP** from damping, which re-enters Lock Standing (fsm 4): *"press L2+R2 to enter
   debug mode, or press L2 + UP to re-enter ready mode."* **[web]** §4.5 has the full key set.
3. **`robot_state` `ServiceSwitch("ai_sport", 1, status)`** — a route we had not considered.
   If `ServiceList` (1003) ever shows `ai_sport` switched **off**, this turns it back on.
   **[web]** Errors `5201` (switch execution error) and `5202` (service is protected).
   ⚠️ **Polarity foot-gun:** the *input* `swit` is `1 = on, 0 = off`, but the *returned*
   `status` is documented as `0 = on, 1 = off` — **inverted**. §8 carries the same warning.

**Route 3 is a write and route 1 transfers ownership of the robot.** Neither belongs in an
LLM-callable tool. Register 1001 and nothing else (§3.5).

That state is reachable by accident from the other stack on this robot:
`xr_teleoperate`'s `Enter_Debug_Mode()` loops `ReleaseMode()` until `CheckMode` returns an
empty name, and it runs automatically whenever `teleop_hand_and_arm.py` is started
**without** `--motion`. `Exit_Debug_Mode()` calls `SelectMode(nameOrAlias='ai')`. **[src]**
So a teleop session deliberately leaves the robot with no controller loaded — and
`DEPLOYMENT.md` §2's interlock does not know about `xr_teleoperate` at all —
see `ROBOT-PERIPHERALS.md` §7.2 and §13.15 below.

### 3.5 We had to reimplement it

Our pinned `unitree_sdk2py` (commit `a7dff75`) ships only `core go2 idl rpc utils` — there is
no `comm/motion_switcher` and no `g1` package. **[live]** The vendored copy in
`xr_teleoperate` ships `a2 as2 b2 comm core g1 go2 h1 h2 idl r1 rpc test utils`. **[live]**

The getter is 20 lines against the generic RPC client we already have, and that is what was
run on 2026-08-14:

```python
from unitree_sdk2py.rpc.client import Client

class MotionSwitcherReader(Client):
    def __init__(self) -> None:
        super().__init__("motion_switcher", False)   # False = no lease

    def Init(self) -> None:
        self.SetTimeout(5.0)
        self._SetApiVerson("1.0.0.1")
        self._RegistApi(1001, 0)                     # CHECK_MODE only

    def check_mode(self):
        code, data = self._Call(1001, json.dumps({}))
        return code, (json.loads(data) if code == 0 and data else None)
```

**Register 1001 and nothing else.** 1002 and 1003 transfer ownership of the robot;
registering an api_id you do not intend to send is how it gets sent by accident later. This
belongs in the bridge as a `get_state`-adjacent read, not as a tool an LLM can call.

---

## 4. The FSM

### 4.1 The state table, and the fact that nobody agrees on the names

Three sources name these ids and **all three differ**. Our own names are the odd ones out
for the two states that matter most.

| id  | Vendor C++ header      | Official docs **[web]**            | Our `g1_protocol.Mode` | Evidence |
| --- | ---------------------- | ---------------------------------- | ---------------------- | -------- |
| 0   | `ZeroTorque()`         | Zero torque                        | `ZERO_TORQUE`          | **[live]** read alongside `posture=zero_torque` |
| 1   | `Damp()`               | Damping                            | `DAMP`                 | **[live]** sent 2026-08-13, `fsm_id → 1` |
| 2   | `Squat()`              | —                                  | `SQUAT`                | **[src]** — never sent by us |
| 3   | `Sit()`                | —                                  | `SEATING`              | **[src]** |
| 4   | **`StandUp()`**        | **"Lock Standing"** / **"Ready Mode"**, No Balance Control | **`PREPARATION`** | **[live]** sent 2026-08-13, robot physically stood (odom z 0.04 → ~1.00 m) |
| 500 | **`Start()`**          | **"Walk Motion"** — the **1-DoF-waist** program, remote **R1+X** | **`WALK`**         | **[live]** accepted `code 0`, **no transition** — §11 |
| 501 | *(absent from every header)* | **"Walk Motion-3Dof-waist"** — remote **R1+Y** | `WALK_WAIST`  | **[web]**, now **officially** documented — never sent |
| 503 | —                      | —                                  | `DANCE`                | **[?]** our enum only |
| 702 | `Lie2StandUp()` *(newer SDK)* | "Lie Down, Stand Up"        | `LIE_UP`               | **[src]**, in the official FSM table too **[web]** |
| 706 | `Squat2StandUp()` *(newer SDK)* | "Balance Squat, Squat Stand" | `SQUAT_UP`          | **[src]** for the id. The claim that the Python SDK sends 706 for **both** directions (i.e. it toggles) is **[web]** and unchecked here — read 7001 before and after either way |
| 801 | —                      | "Run", remote **R2+A** — *"The 29dof device `ai_sport` was updated to version 802 after version 8.6.x.x"* | `RUN` | **[web]** |
| 802 | —                      | **= 801 renumbered on 29-DoF `ai_sport` ≥ 8.6.x** | labelled `"run"`, marked suspect | **[live]** read on 2026-08-11 while the robot stood perfectly still |
| 812 | —                      | —                                  | `CLIMB`                | **[?]** our enum only |

The official **Expert interface** table (`sport_services_interface`, 2026-07-13) is the first
vendor-authored enumeration we have, and it is short: 0 Zero Torque, 1 Damping, 2 Position
Control Squat, 3 Position Control Sit Down, 4 Lock Standing (all five marked "No Balance
Control"), 706, 702, 500, 501, 801. **No 503, no 812** appear anywhere in 45 pages — our
`DANCE` and `CLIMB` enum members remain unsourced. **[web]**

**§6.5's "802 is not in {500, 501, 801}" anomaly is closed.** The remark on row 801 says the
29-DoF `ai_sport` renumbered Run to **802** after version 8.6.x.x. **[web]** Ours is a 29-DoF
machine (see `mode_machine` in §4.2) and we read 802 live. So **802 *is* Run**, the arm
header's gesture-permitted set should be read as **{500, 501, 802}** on this firmware, and
our live 802 gesture success is *consistent* with the header's polarity rather than
contradicting it. Two consequences: stop treating 802 as an anomaly, and **stop sending 801
on this chassis**.

Three names for id 4 (`StandUp` / `Lock Standing` / `PREPARATION`) and three for 500
(`Start` / `Walk Motion` / `WALK`). **When reading anyone's notes, translate to the number
first.** On id 4 the docs partly vindicate our label: the vendor's concept table calls it
**Ready Mode** — *"the robot will slowly swing out the **preparatory posture before the
motion mode** within 5 seconds"* **[web]** — so `PREPARATION` is closer to vendor intent than
this section previously credited. The rule stands anyway: use the number.

The vendor's own glossary for the rest, verbatim and worth having: **[web]**
*Zero Torque* — motors stop, **no** damping felt when swinging. *Damping* — motors stop,
**clear** damping felt, "which can enter the ready mode". *Squat* / *Seating* — assumed
slowly over 5 s, no balance control. *Continuous Walking* — always stepping. *Standing* —
stops stepping at zero stick, steps when disturbed or commanded. That damping-enters-ready
line officially confirms the **1 → 4** edge our client-side E-grade table encodes (§4.3).

Two historical ids to recognise but never send: **200** was `Start()` before 2025-06 (C++) /
2026-04 (Python) **[web]**, and **601** is `Start()` on the **H2** — which uses the same
service name, the same api_ids and the same error codes as the G1, so an H2-sourced recipe
looks perfectly plausible and puts the wrong id on the wire. **[web]**

### 4.2 `fsm_mode`, `mode_pr`, `mode_machine` — three fields, none of them the FSM id

- **`fsm_mode`** (api 7002) is a **documented gate on mode switching**, and we have never read
  it immediately before sending 7101. Official text: *"0: Static, allows switching to other
  modes / 1: Dynamic, switching to most modes is not allowed … **When the robot's current
  state/posture is unsuitable for mode switching, we prohibit the robot from changing
  modes.** … Damping mode, as the ultimate fallback, can always be activated."* **[web]** The
  same page glosses `GetFsmMode` differently — "0: standing state; 1: moving state" — two
  names for one field, so read it as *static/dynamic*, which is the safety-relevant framing.
  **Only 0 and 1 are documented, anywhere, in 45 pages.** The arm service's own error text
  implies a **3** exists ("in the state 801, the actions are only supported in the fsm mode
  {0, 3}") **[src]**, and the widely-repeated **2 = "feet unloaded"** claim rests on a single
  self-declared LLM-generated repo whose apparent on-robot corroboration is a copy of itself.
  **Strike 2 from anything load-bearing.** **[?]**
  → **Bridge change:** read 7002 (or subscribe `rt/sportmodestate`, §9.2) immediately before
  every 7101, log both together, and **refuse-and-report rather than send blind when
  `fsm_mode != 0`**. This is the cheapest untested explanation for "code 0 and nothing
  happens" (§11.3 Rank 3).
- **`mode_machine`** is the **chassis variant**, not a mode — and it now has a vendor decode.
  `basic_services_interface` (2025-10-21) comments the field in `LowCmd_` verbatim as
  **`// G1 Type：4：23-Dof; 5: 29-Dof; 6: 27-Dof (29Dof Fitted at the waist)`**. **[web]**
  Ours reads **5** at `fsm_id` 0, 4 and 802 alike **[live]**, i.e. **a 29-DoF machine with a
  3-DoF waist and the waist fastener *not* engaged** in firmware. That is the first
  variant statement we have that is a firmware self-report rather than a URDF two teams
  picked, and it independently corroborates §11.3 Rank 1.
  ⚠️ **A second vendor page gives an incompatible numbering.** `joint_motor_sequence`
  (2025-03-17) heads its tables *"23DOF Version (`mode_machine == 1`)"*, *"29DOF Version
  (== 2)"*, *"14DOF Version (== 9)"*. **[web]** They cannot both describe one firmware. Side
  with the newer page, because **our live value 5 is a member of only the newer set** — under
  the older numbering 5 means nothing at all. Two rules follow: surface `mode_machine`
  decoded (23/29/27-Dof) rather than as a bare integer, and **never hardcode it in a
  `LowCmd_` — echo back what `LowState_` reported**, which is what the vendor examples do
  anyway. If it ever reads **6**, the waist has been fastened and the 501/802 branch may no
  longer apply. `ROBOT-INVENTORY.md` §6's "they are genuinely independent fields" is right;
  this says *why*.
- **`mode_pr`** selects the parallel-mechanism control convention: `PR = 0` (series
  pitch/roll), `AB = 1` (parallel A/B). Must also be set correctly in any `LowCmd_`.
  **[src]** **Correction:** this governs the **ankles *and the waist***, not the ankles and
  wrists — the vendor comment reads *"Parallel mechanism (**ankle and waist**) control mode"*,
  and the joint table gives `WAIST_A`/`WAIST_B` as the `AB` names for indices 13/14. **[web]**

### 4.3 Transition rules

**What the firmware enforces is still unknown, and the vendor's answer is an unreadable
image.** Every `Start`/`Damp`/`Squat`/`Sit`/`StandUp`/`ZeroTorque` entry in
`sport_services_interface` carries the identical remark — *"The success of the final state
switch depends on the built-in operation control state switching logic, please refer to the
previous section 'Mode Switching'"* — and `SetFsmId`'s remark links it explicitly to
`remote_control#heading-4`, whose entire content is one JPEG:
`https://oss-global-cdn.unitree.com/static/98431a05f8e747709722e901d32d8ce3_11798x7046.jpg`
**[web]**

**Transcribing that image is the highest-value action available without robot access.** It is
the only authoritative statement of the legal transition graph in existence, and it should
answer directly whether 4 → 500 and 4 → 501 are legal edges and what their preconditions are.
Same for the three remote-control sticker PDFs linked at the top of `remote_control`, which
are versioned by Motion Control Version (> 8.6.0.0, ≥ 8.5.0.0, ≥ 8.4.2.222) with separate
**29dof** and 23dof sheets — the 29dof > 8.6.0.0 sheet is the one matching this machine, and
it may name the FSM ids behind the R1+X / R1+Y combos outright.

**What the docs *do* state as preconditions** for entering locomotion: **[web]**

| Precondition | Source |
| ------------ | ------ |
| Reach **Lock Standing (4)** first | both bring-up variants, and the `remote_control` chain ① → ② → ③/⑦/⑧ |
| **Feet on the ground, bearing weight** | *"After descending the suspension rope, the G1 feet touch the ground. Press R2 + A … and then the control program starts"* |
| **`fsm_mode == 0`** (static) | *"switching to most modes is not allowed"* while dynamic |
| **Built-in motion control running**, i.e. **not** debug mode | *"the high-level motion service becomes invalid"* in debug mode |
| **Waist DoF must match the id** — 500 for 1-DoF, 501 for 3-DoF | the R1+X / R1+Y split (§4.4) |
| Ready mode takes **~5 s** to assume its posture | *"within 5 seconds"* |

And what the docs **do not** require, which kills two of our own hypotheses: **[web]**

- **No battery or SOC threshold** is stated as a precondition for any FSM id.
- **No `BalanceStand` and no `SetStandHeight` before `Start()`.** `BalanceStand` is described
  only as "enter the balanced standing mode", nothing more. `g1pilot`'s stand-height ramp is
  that team's practice, not a documented precondition — §11.3 Rank 3 weakens accordingly.
- **No required intermediate state between 4 and 500/501** beyond 4 itself.

What we have on our side:

Client-side, `g1_protocol.py` records a rule set taken from `legion1581/unitree_ui`
(an **E-grade** reverse-engineered source) as **reference data only — nothing enforces
it**. A `can_transition()` helper built on it was never called by any skill and was
removed rather than wired up: encoding E-grade rules as a client-side gate would refuse
transitions the firmware would have accepted, which is the wrong failure while we are
still trying to tell robot problems from bridge problems (§11). **[web]**

```
not in Damp    → cannot enter ZeroTorque / Preparation / SquatUp / LieUp
in ZeroTorque  → only Damp
in Squat       → only Damp
in Damp        → only ZeroTorque / Preparation / SquatUp / LieUp
in Preparation → only Damp / Walk(500) / WalkWaist(501) / Run(801)
```

Observed live, 2026-08-13, with a controller loaded: **[live]**

| Sent                   | rpc | Result |
| ---------------------- | --- | ------ |
| `SetFsmId(1)` Damp     | 0   | `fsm_id → 1`, `posture=damp` |
| `SetFsmId(4)` StandUp  | 0   | `fsm_id → 4`, robot stood (odom z 0.04 → ~1.00 m) |
| `SetFsmId(500)` Start  | 0   | **`fsm_id` stayed 4** — repeatable, harness-supported, weight-bearing |
| `SetBalanceMode(0)`    | 0   | no `fsm_id` change; `Start` still no-ops afterwards |
| arm 7106 `{"data":26}` | **7404** | from firmware, 0.71 s round trip |

So the client-side table is consistent with everything observed, and also **untested** —
1→4 and 4→(refused 500) are the only two transitions ever exercised on this robot.

### 4.4 The canonical bring-up sequence

Unitree's own procedure, condensed to a numbered runbook but with the vendor's wording kept
where it carries information. All **[web]**, from `quick_start` (2025-11-12) and
`remote_control` (2026-06-25).

**Use the L2 forms.** `quick_start`'s "sitting in a chair" variant writes **L1**+A / L1+UP /
L1+LEFT where its own hanging variant and the whole of the newer `remote_control` key table
write **L2**. `remote_control` is L2-only and seven months newer. Prefer L2.

**Variant A — hanging on the protective rack. This is our situation.**

1. *"Use the protective rack to hang the G1 to ensure safety."* Fit the battery — *"when you
   hear the 'click ~' sound, the battery pack is installed."*
2. *"After hanging G1, put it in its natural position."*
3. Short-press the battery power switch once, then long-press it for **more than 2 seconds**.
4. Wait **~1 minute**. *"When the ankle hit the limit sound, the initialization is
   successful."* Then **wait another 30 seconds.**
5. **L2 + B** → damping. (LED **solid orange**.) This "unlocks the control".
6. **L2 + UP** → **Lock Standing / Ready Mode**, fsm 4. The robot rises over ~5 s.
7. **Lower the suspension rope until the feet touch the ground and bear weight.**
8. **Enter locomotion.** Two different entries exist and they are not interchangeable:
   - **R2 + A** → *Run Control*, i.e. **801 / 802**. This is what the vendor's own "regular
     boot process" chain uses (① → ② → ③), and it is very likely how this robot reached the
     `fsm_id = 802` we recorded on 2026-08-11.
   - **R1 + X** → *Main Operation Control* = **500**, the **1-DoF-waist** walk program.
   - **R1 + Y** → the **3-DoF-waist** equivalent = **501**. *"Only Used For 3-DOF Waist
     structure, recommended to use R1 + Y mode."*
   **On a 29-DoF machine, ⑧ R1 + Y is the one to press** — see §11.
9. *"After the G1 movement is stabilized, the hook can be completely released."* Sticks now
   drive it; **START** toggles standing ↔ walking.

**Variant B — starting seated in a chair.** Power on → wait ~1 min for zero torque → **L2+A**
damping → hold the shoulder and **L2+UP** to the ready state → *"After G1 is straightened and
standing, you can press R1 + X (1 degree of freedom waist) or R1 + Y (3 degrees of freedom
waist) to enter the operation control state."*

**The vendor's own three chains**, in its ①…⑧ symbols (① L2+B damping, ② L2+UP lock stand,
③ R2+A run control, ④ L2+LEFT seated, ⑤ L2+X lying-and-standing, ⑥ L2+A squat switch,
⑦ R1+X main operation control, ⑧ R1+Y 3-DoF main operation control):

```
regular:    boot → ① → ② → ③ → demo → ④ (chair seat) → power off
lying:      boot (crotch post flat on the ground) → ① → ⑤ → demo → ⑥ → power off
squatting:  boot (squatting) → ① → ⑥ → demo → ⑥ → power off
```

**Shutdown.** Hanging: **L2+B** to damping, then power off — *"or press L2+R2 to enter debug
mode, or press L2 + UP to re-enter ready mode."* Seated: **L2+LEFT**, help it sit, then
**L2+A** back to damping.

**Emergency stop: L2 + B.** *"G1 goes into damped mode, which will losing balance and falling
down."* It works **even inside debug mode**. It is the one combination everyone in the room
should know.

⚠️ **With dexterous hands fitted**, the vendor warns against starting the device in the lying
or squatting positions (risk of damaging the hands) and against "running or balance tests"
generally. Settle `ROBOT-PERIPHERALS.md` §4 *before* a locomotion window, not after.

### 4.5 Remote-control reference, and the LED strip

**Key description**, verbatim. The vendor writes every entry as *hold the first key, click the
second*. **[web]**

| Combination | Effect | | Combination | Effect |
| ----------- | ------ | - | ----------- | ------ |
| L2 + R2 | **Debug mode** | | SELECT + Y | Wave hand |
| L2 + Y | Zero torque | | SELECT + A | Handshake |
| L2 + B | ① Damping / **e-stop** | | SELECT + X | Turn around and wave |
| L2 + UP | ② Lock stand | | R2 + DOWN / R2 + UP | Slow / fast running (in ③) |
| L2 + LEFT | ④ Seated | | START + UP / START + DOWN | Forward / backward lean (in ③) |
| L2 + X | ⑤ Lying and standing | | Double-click START | Standing ↔ keep-stepping (in ⑦/⑧) |
| L2 + A | ⑥ Squat switch | | Double-click L2 / L1 | Low / high speed mode (in ⑦/⑧) |
| R1 + X | ⑦ Main operation control (1-DoF waist) | | R1 + arrow | Offset compensation (in ⑦/⑧) |
| R1 + Y | ⑧ Main operation control (**3-DoF waist**) | | | |

**Four notes that change how you operate it:** **[web]**

1. **"When in the standing position, certain button combinations need to be `held for two
   seconds` to take effect."** A tap does nothing. This alone may explain "the remote also
   failed" (§11.2).
2. Debug mode is enterable **only from zero-torque or damping**.
3. **L2 + B remains effective even in debug mode.**
4. To return to Main Operation Control after L2+A (squat), you must go through damping first.
   (The page's own text says "then switch back through L2+A" while its key table assigns L2+A
   to Squat — an internal contradiction; go through damping and re-enter via ⑦/⑧.)

Also stated: *"The robot's current walking mode does not include the function for climbing
stairs."* And **nothing anywhere hands control to or takes it from the SDK** — the SDK/remote
relationship is mediated *only* by debug mode.

**LED strip colour is a free, instant, zero-RPC readout of the robot's mode:** **[web]**

| Colour | Mode | | Colour | Mode |
| ------ | ---- | - | ------ | ---- |
| Solid **blue** | Normal operation | | Solid **yellow** | **Debug mode** |
| Solid **orange** | Damping | | Solid **purple** | Zero torque |
| Solid **green** | Seated | | Solid **dark blue** | Standby |
| Solid **red** | **Error state** | | | |

This costs nothing and it retroactively disambiguates every session we have logged. **Ask the
operator to record the LED colour at the top of every window**, and specifically to look for
**solid red** — no diagnostic we currently have surfaces a firmware-level error state at all.
Note the strip has other writers (§7): the voice assistant breathes blue/green, and our own
`SET_RGB_LED` would overwrite the operator's only state indicator.

### 4.6 Debug mode — what it is, and what it is not

**Definition, verbatim:** *"For low-level development: when using the SDK for development or
debugging, always verify that G1 is in debug mode (damping or zero-torque). Enter debug mode
by pressing L2 + R2 on the remote; this halts the motion-control program and prevents
potential command conflicts. To confirm debug mode is active, press L2 + A."* **[web]**

- **Entry:** L2 + R2, **only** from zero-torque or damping. If L2+A does not produce the
  diagnostic pose, *"press L2 + R2 several times to ensure entering the debugging mode."*
- **Confirmation:** L2 + A poses a specific diagnostic position; **or** the LED goes solid
  yellow; **or** `motion_switcher` `CheckMode` returns an empty `name` (§3.3).
- **Exit:** no button exits it directly. Documented routes are **L2 + UP** back to Ready
  Mode, or `SelectMode("ai")` over RPC (§3.4).
- **Why it exists:** *"once the G1 is turned on, the built-in motion control program will
  automatically start, even if you do not operate the remote control. The program
  periodically sends commands with a speed of 0. However, if you use the SDK in this state,
  you may cause conflicting instructions and thus cause G1 to jitter."* **[web]**

**Which of our paths need it — the decisive pair:** **[web]**

| Path | Debug mode? |
| ---- | ----------- |
| `rt/lowcmd` low-level (`g1_ankle_swing_example`) | **Required** |
| `rt/user_lowcmd` (7110 User Development Mode) | Required (it *is* a temporary debug mode) |
| **High-level RPC — `sport`, the loco client, i.e. our path** | *"there is **no need** to enter the debug mode"* |
| **`rt/arm_sdk`** | *"there is **no need** to enter debugging mode"*; it blends into the running controller |
| **`arm` action service (gestures)** | **Must be OFF** — *"After entering debug mode … the Arm Action Service becomes invalid"* |

So for everything C3PO does, **debug mode is a state to be out of, not into**. That is the
opposite of the instinct the phrase invites, and it is why debug mode is *ruled out* as the
explanation for 2026-08-13 (§11.1) while being the *confirmed* explanation for 2026-08-14.

**Stop citing `debugging_specification` for this.** Despite the name, that page (2024-12-05)
contains no software debug-mode content at all — it is about strapping the G1 to a bracket
and running an Ethernet cable to the shoulder RJ45.

---

## 5. `SET_VELOCITY` (7105) semantics

### 5.1 Shape

```json
{"velocity": [vx, vy, omega], "duration": 1.0}
```

`SetVelocity(vx, vy, omega, duration = 1.0f)` — note the **default duration of one second**.
`Move(vx, vy, vyaw, continuous)` maps to `duration = continuous ? 864000.f : 1.f`, and
**864000 s is exactly 10 days**: that is the entire "continuous move" idiom, a duration so
long it never expires. `Move()`'s own `continous_move_` flag defaults to **false**, and
`SwitchMoveMode()` — which is what would flip it — sends nothing to the robot at all.
`StopMove()` is just `SetVelocity(0,0,0)` with the same 1 s duration. **[src]**

### 5.2 The 1 s deadman is the primary stop, and it is not ours

`duration` is a **firmware-level deadman**: the control board stops driving when it expires,
regardless of what our process is doing. That is stronger than any watchdog we write in
Python, because it survives our process being SIGKILLed, the Jetson wedging, or the network
cable coming out.

Consequences we have already acted on:

- **Send `duration = 1.0` and re-send at 10 Hz. Never use 864000 for anything an LLM can
  trigger.** If the commanding process dies mid-stride, a 1 s duration brakes the robot
  within a second; 864000 walks it into a wall for ten days. This is exactly the reasoning
  the colleague's `cmd_vel_to_loco` already encodes (`ROBOT-INVENTORY.md` §5).
- **Our `LINK_WATCHDOG` is a second layer, not the primary**, and is correctly off by
  default (`ROBOT-INVENTORY.md` §6.3). Its real job is the *non-velocity* cases — a held
  gesture, a posture change — where no firmware deadman exists.
- **Contrast the `agv` service.** Its `1001 AGV_MOVE` takes `{"vx":f,"vy":f,"vyaw":f}` —
  named scalars, **no duration field**, therefore **no deadman**. **[src]** Ours is a legged
  G1 so that path is almost certainly absent, but if anyone ever ports code from a G1-D,
  the safety property silently disappears.

### 5.3 What is *not* documented

- **No vendor source states the sign or axis convention for `vx`/`vy`/`omega`** — and this
  survived all 45 official pages. `sport_services_interface` names them (*"vx: forward speed;
  vy: horizontal speed; omega: rotation speed"*) and never defines *positive*. **[web]**
  ROS REP-103 (x forward, y left, yaw CCW) is the near-universal default and almost certainly
  what Unitree used, and two official statements point the same way without settling it: the
  **odometry** frame is *"x-axis towards the front of the base, y-axis and z-axis towards
  left and upstraight, obeying the right-hand rule"*, and the **SLAM** output frame is
  *"X positive directly in front of the robot, Z positive vertically upward"*. **[web]** Both
  are output frames of other services, not the command frame of 7105. **Still inference.
  Measure it.**
- **A limit exists after all, but its bound is not published.** `SetVelocity`'s official
  remark is *"The program will automatically set the cropping to the allowed range"* — a
  firmware-side clamp — and `SetSpeedMode` selects a documented ladder of **1.0 / 2.0 / 2.7 /
  3.0 m/s** maxima, scoped to *running* mode. **[web]** That corrects this section's previous
  "no velocity limit exists in any vendor source": no clamp appears in the *headers*, but the
  firmware applies one and the docs say so. The two numbers usually quoted as limits are
  still not limits — the ~2 m/s marketing figure, and `unitree_rl_lab`'s training ranges
  (vx −0.5…1.0, vy −0.3…0.3, ωz −0.2…0.2), which apply to RL policies over `rt/lowcmd`, a
  different control path. (Note the official `rl_control_routine` page does **not** contain
  those ranges, so they remain uncorroborated by Unitree.) Use them as a conservative
  **ceiling**, never a target. **[web]**
- **Measured velocity is available for free and we ignore it.** `rt/odommodestate` populates
  `velocity[3]` (world-frame m/s) and `yaw_speed` (body-frame rad/s) alongside the position
  we already read. **[web]** Logging commanded-vs-measured is the direct instrument for the
  sign-convention question, and `state.py::_on_odom` currently drops both.
- **The response body is never parsed** by any vendor client, so a `code 0` from 7105 means
  "the request was accepted", not "the robot moved". **[src]**

### 5.4 Status on this robot

`SET_VELOCITY(0,0,0,1.0)` returned `code=0` on 2026-08-11 — that confirmed the JSON shape
against real firmware. **[live]** **A non-zero velocity has never been executed on this
robot.** Both because no supervised window has allowed it and because the robot has never
been in a state that would accept it (§11).

---

## 6. Arms and gestures

### 6.1 Two paths, and how to tell which one you hit

| | **Path A — `sport` service** | **Path B — `arm` service** |
| --- | --- | --- |
| Topic | `rt/api/sport/request` | `rt/api/arm/request` |
| api_id | 7106 `SET_ARM_TASK` | 7106 `EXECUTE_ACTION` |
| Values | task ids **0–3 only** | catalogue **11–27, 99** |
| Vendor entry point | `WaveHand()`, `ShakeHand()` | `ExecuteAction(id)` |
| Out-of-range error | `7303 Invalid task id` | `7402 Invalid action id` |
| Our bridge uses | — | **this one** (`_G1Client("arm", (7106,), timeout_s=15.0)`) |

**[src]** A useful diagnostic falls out of the error tables: **7404 exists only on the `arm`
service.** The loco error header declares 7301/7302/7303 and nothing else. So the 7404 we
received on 2026-08-13 is positive proof our request reached the arm service's
`EXECUTE_ACTION` — not `SET_ARM_TASK`, not a lost message. **[src]**

### 6.2 Path A: the sport task ids

```cpp
WaveHand(bool turn_flag = false) { return SetTaskId(turn_flag ? 1 : 0); }
```

Task 0 = wave, task 1 = wave with "turn" (no header says what turns; most likely the torso
toward the addressee). **[src]**

`ShakeHand` is staged, with the stage held **client-side**: **[src]**

```cpp
int32_t ShakeHand(int stage = -1) {
  switch (stage) {
    case 0:  first_shake_hand_stage_ = false; return SetTaskId(2);
    case 1:  first_shake_hand_stage_ = true;  return SetTaskId(3);
    default: first_shake_hand_stage_ = !first_shake_hand_stage_;
             return SetTaskId(first_shake_hand_stage_ ? 3 : 2);
  }
}
```

The flag initialises **true**, so a bare `ShakeHand()` sends **2** first, then 3, then 2 —
an alternating two-press handshake. Neither header says what 2 and 3 physically do; "2
opens, 3 closes" is inferred from the toggle's initial value, not documented. **[?]** Our
`shake_hand` tool is not this: it is a one-shot `arm` catalogue id 27 with no staging.

### 6.3 Path B: the gesture catalogue

The two vendor maps on this robot agree except at id 13, and the official catalogue agrees on
every **id** while disagreeing on five **names**: **[src]** + **[web]**

| id | C++ `action_map`              | Python `action_map` | **Official name [web]** | Our `Gesture` enum |
| -- | ----------------------------- | ------------------- | ----------------------- | ------------------ |
| 11 | two-hand kiss                 | two-hand kiss       | Double Hand Flying Kiss | **missing** |
| 12 | left kiss **and** right kiss  | left kiss           | Single Hand Flying Kiss | `BLOW_KISS` |
| 13 | *(absent)*                    | **right kiss**      | *(absent)*              | **missing** |
| 15 | hands up                      | hands up            | **Arms Horizontal**     | `HANDS_UP` ⚠ |
| 17 | clap                          | clap                | Applause                | `CLAP` |
| 18 | high five                     | high five           | High Five               | `HIGH_FIVE` |
| 19 | hug                           | hug                 | Hug                     | `HUG` |
| 20 | heart                         | heart               | Double Hand Heart       | `HEART_BOTH_HANDS` |
| 21 | right heart                   | right heart         | Single Hand Heart       | `HEART_SINGLE_HAND` |
| 22 | reject                        | reject              | **Double Hand Cross**   | `REFUSE` ⚠ |
| 23 | right hand up                 | right hand up       | **Right Hand Horizontal** | `SINGLE_HAND_UP` ⚠ |
| 24 | x-ray                         | x-ray               | **Dynamic Light Wave**  | `ULTRAMAN_RAY` ⚠ |
| 25 | face wave                     | face wave           | **Wave Hand in Front Chest** | `LOW_WAVE` ⚠ |
| 26 | high wave                     | high wave           | Wave Hand High          | `HIGH_WAVE` — **verified live 2026-08-11** |
| 27 | shake hand                    | shake hand          | Handshake               | `SHAKE_HANDS` |
| 99 | release arm                   | release arm         | Recover Initial Arm Pose | `RELEASE_ARM` |

The C++ map inserts `{"left kiss",12}` and `{"right kiss",12}` into one `std::map`, so the
second insert is silently dropped — the bug is present in the copy on this robot. **[src]**

⚠️ **The five marked rows imply different physical poses than our labels.** "Arms Horizontal"
is not "hands up"; "Dynamic Light Wave" is not an "x-ray". **The LLM selects a gesture by our
label**, so a wrong label is a wrong gesture, reliably, every time. Re-label the enum to the
official names and keep ours as aliases. Which set describes the actual motion is still
unverified — one supervised window watching each once settles it.

**There is no id 14, no id 16 and no id 36 in either map — or in the official catalogue.**
Our `Gesture.FORWARD_PUSH = 36`, used by `point_at`, has now failed to appear in a **fourth**
independent Unitree source; its only provenance is a decompiled Android app. **[src]** +
**[web]** Strike it and re-point `point_at`. Likewise id 13 is absent from the C++ map *and*
the official table, so the Python map's "13 = right kiss" is the outlier and the C++
duplicate-key bug is the more likely truth.

**The official table is explicitly not authoritative, and the firmware will tell us
directly.** *"Action IDs are updated periodically. Use `Get Action List` to see the actions
available in the current firmware version."* And `GetActionList(std::string &data)` returns
*"the action list (includes usable actions, **special action requirements for FsmID**, and
names and durations of taught actions)"*. **[web]**

That makes **`arm`/7107 `GET_ACTION_LIST` the highest-value unmade call on this robot.** One
zero-motion read returns (a) this firmware's real catalogue, settling `FORWARD_PUSH` and id
13; (b) **the per-action FsmID requirements**, which is a direct answer to the disputed 7404
polarity in §6.5 — and per-action gating would make *both* sides of that dispute partially
true; and (c) taught-action **durations**, which would let us size `ARM_TIMEOUT_S` from data
instead of guessing (§6.6). Run it in the same breath as `robot_state` 1003 and the 7302
calibration (§11.4).

**Parameter key.** Three vendor clients, two shapes: the ROS 2 example and the Python SDK
send `{"data": N}`, the newer C++ SDK sends `{"action_id": N}`. **[src]** Two of three say
`data`, and the live evidence agrees — the 2026-08-11 wave went out as `{"data":26}` and the
arm moved. **`{"data": N}` is correct on this firmware; do not "fix" our bridge to match
the C++ header.** **[live]**

The newer SDK also declares `7108 EXECUTE_CUSTOM_ACTION` (`{"action_name": "..."}`) and
`7113 STOP_CUSTOM_ACTION` (empty), with **no client method generated for either**, so no
parameter shape for 7113 exists on this machine, and neither id is in the firmware-matched
tree. **[src]** The docs confirm the **feature** exists but publish no api_ids, so the
numbers stay `[src]`-from-the-newer-SDK and unproven on 1.5.3.8. What they add is the
semantics: `ExecuteAction` is **overloaded and asymmetric** — by **id** it is *"blocking
execution"*, by **name** (an App-taught action, **case-sensitive**) it is *"non-blocking
execution"*, and `StopCustomAction()` returns the arm to its initial position. **[web]** That
finally makes `rt/arm/action/state`'s `id: 100` coherent: it means "a custom action is
running", identified by `name` (§6.6).

### 6.4 Arm error codes, and the holding latch

| Code | Symbol                          | Message (robot header **[src]**) | Official remark **[web]** |
| ---- | ------------------------------- | ------- | ------- |
| 7400 | `..._ERR_ARMSDK`                | "The topic rt/armsdk is occupied." | *"Topic is occupied — **an action is being executed**"* |
| 7401 | `..._ERR_HOLDING`               | "The arm is holding. Expecting release action(99) or the same last action id." | *"Applicable to **sustained actions** like Arms Horizontal, Heart, etc."* |
| 7402 | `..._ERR_INVALID_ACTION_ID`     | "Invalid action id." | "Action ID does not exist" |
| 7404 | `..._ERR_INVALID_FSM_ID`        | "Invalid fsm id." | *"Current FsmID cannot trigger this action. Some actions cannot be triggered under walking/running motion control."* |

There is no 7403 in either source. **Three deltas the official remarks force:**

- **7401 names the latching gestures.** The sustained ones are called out as *Arms Horizontal
  (15)* and *Heart (20/21)*, "etc." Mark those as holding gestures in our catalogue. The
  recovery is confirmed exactly as our header states — id **99** or a repeat of the same id.
  The **20 s auto-release** remains `[src]` from the robot header only; **the docs do not
  corroborate it, so do not rely on it** — send 99.
- **7400 is broader than we read it.** We treat it as "another process holds `rt/arm_sdk`";
  the vendor gloss is "an action is being executed". Both are consistent if the service
  occupies the topic while running, so **7400 means BUSY as well as CONTENDED** — our
  diagnostic text should offer both causes rather than only blaming `xr_teleoperate`.
  Distinguish them by firing two gestures back to back with nothing else running.
- **7404's polarity conflict is reaffirmed with a date** — see §6.5.

**[src]** Note **`FSM_UNAVAILABLE` is our own label**, not the vendor's: a filesystem-wide
search finds that token in exactly one place on the robot, our own `mcp_server.py`. **[live]**
The firmware's own string for 7404 is "Invalid fsm id". So a log line or note reading
"7404 `FSM_UNAVAILABLE`" — including the 2026-08-13 one — is **our** wording wrapped around
rpc code 7404, not something the control board sent. What the firmware put in the response
body that day was not recorded; do not treat the token as a firmware string.

**Two traps here, both of which we would currently misdiagnose:**

- **7401, the holding latch.** Some gestures hold their final keyframe. While held, the arm
  service accepts **only** id 99 (release) or a repeat of the same id; everything else gets
  7401 until a **20 s** auto-release. **[src]** Our bridge has no 7401 handling anywhere, so
  a second gesture after a held one surfaces as an unexplained `rpc_error_code_7401` and
  looks like a robot fault rather than "send `release_arm` first".
- **7400, `rt/arm_sdk` occupied.** The gesture catalogue is *implemented on top of* the
  low-level arm-SDK blend — the vendor says so directly: "The controller is based on the
  `rt/arm_sdk` interface." **[src]** So if `xr_teleoperate` (which publishes to `rt/arm_sdk`
  and `rt/lowcmd`) is running, **every gesture will start failing with 7400** while
  everything else about the robot looks healthy. That process was live on this robot during
  the last session and `run_c3po`'s commander check does not look for it
  (`ROBOT-PERIPHERALS.md` §7.2).
  Naming trap: the error string says **`rt/armsdk`** with no underscore; every publisher and
  subscriber in code uses **`rt/arm_sdk`**. The code spelling is the real one. **[src]**

### 6.5 7404: the disputed polarity

The vendor header states the condition verbatim: **[src]**

> "The actions are only supported in fsm id {500, 501, 801}. You can subscribe the topic
> `rt/sportmodestate` to check the fsm id. And in the state 801, the actions are only
> supported in the fsm mode {0, 3}."

So per this firmware's own header, gestures **require** a locomotion state. Unitree's official
`arm_action_interface` (2025-11-17 — **newer than either vendor tree on this robot**) says
the opposite: *"Current FsmID cannot trigger this action. **Some actions cannot be triggered
under walking/running motion control.**"* **[web]** Two Unitree-authored sources, opposite
polarities. A third source (`legion1581/unitree_ui`, E-grade) says gestures need a locomotion
state *and* that four of them are hidden specifically in Run.

This is not academic: it decides whether a gesture-capable state is one we are trying to
**enter** or one we are trying to **avoid**. **The robot's header wins** — it shipped with our
firmware — but record the conflict.

**Two things resolved since, and one hypothesis that reconciles them.**

1. **The "802 is not in {500, 501, 801}" half of the problem is gone.** 802 *is* 801,
   renumbered for 29-DoF `ai_sport` ≥ 8.6.x (§4.1). **[web]** So our live evidence — 7404 at
   `fsm_id=4`, success at `fsm_id=802` — now fits the header's polarity **and** its list, once
   the list is read as {500, 501, **802**} on this firmware. That is a clean corroboration of
   the header and a real problem for the docs' polarity.
2. **The reconciliation the docs themselves suggest: gating is per action, not global.**
   `GetActionList` returns *"special action requirements for FsmID"* **[web]** — i.e. the
   firmware holds a per-action rule. Under that model both statements are partially true
   (some actions require locomotion, some are blocked during it), which is also the only
   model consistent with the E-grade claim that four gestures are hidden specifically in Run.
   **One `arm`/7107 read settles it without moving the robot.**

**⚠️ Our single piece of live 7404 evidence may be void.** The docs state plainly that the
Arm Action Service is invalid in debug mode (§4.6). `CheckMode` was **not** run on 2026-08-13
(§11.1), so the 7404 we recorded at `fsm_id=4` could have been a debug-mode artifact carrying
no information about FSM gating at all. Re-run the observation with `CheckMode` first.

`g1_protocol.is_locomotion_state()` encodes the E-grade rule — **and is never called**.
`grep` finds it only at its own definition. The `wave` that failed went straight to the
wire; there is no client-side gate to remove. **[src]**

### 6.6 Ack semantics, and the false-failure it causes

`sport` acks promptly. **`arm` acks on completion of the motion** — 4.19 s for a wave. The
vendor now states this outright: `ExecuteAction(int32_t action_id)` is **"Blocking
execution."** **[web]** With the SDK's **documented 1 s default timeout** (§1.5) every gesture
returned `3104 RPC_ERR_CLIENT_API_TIMEOUT` *while the robot was visibly performing it*.
**[live]** So the fix we chose — sizing timeouts to motion duration rather than going
fire-and-forget — is the vendor-sanctioned one, and `GetActionList`'s durations would let us
size it from data. That is a false failure in the dangerous direction: an operator or an LLM reads "failed" and retries a command the robot already
obeyed. Fixed by sizing timeouts to motion duration (`g1_rpc.ARM_TIMEOUT_S`), **not** by
going fire-and-forget, which would discard genuine error reporting.

The proper fix exists and we are not using it. The arm client documents a push topic: **[src]**

```
rt/arm/action/state   { "holding": false, "id": 99, "name": "release_arm" }
```

`id` is the current action (always 100 for custom/teach actions, identified by `name`
instead), `holding` says whether the arm will latch. Subscribing to it gives non-blocking
completion *and* would let us handle 7401 properly. **The DDS message type is not stated in
any header** — `std_msgs::msg::dds_::String_` is the strong candidate by analogy with
`rt/audio_msg`, but that is **[?]**, and the topic's existence on this firmware is
unconfirmed.

By contrast the `voice` service looks like it acks on **receipt**: every vendor example
sleeps a fixed interval after `TtsMaker` rather than relying on the return, and paces
`PlayStream` chunks with an explicit 1 s sleep. **[src]** Not proven, but it means a `say()`
skill needs its own duration model and does **not** need `ARM_TIMEOUT_S`-style headroom.

### 6.7 The low-level path: `rt/arm_sdk`

Publish `unitree_hg::msg::dds_::LowCmd_` on `rt/arm_sdk`, subscribe `rt/lowstate`. The blend
weight — how much authority your stream has over the built-in controller — goes in
**`motor_cmd[29].q`**, clamped 0..1. Ramp it, never step it. Vendor gains for this path:
`control_dt = 0.02` (50 Hz), `kp = 60.0`, `kd = 1.5`, `max_joint_velocity = 0.5`;
`xr_teleoperate` uses `kp = 40.0` for the wrist motors specifically. **[src]** This robot is
the 7-DoF-arm / 29-DoF build, so left arm is 15–21 and right arm 22–28 (§9.3).

**Four things the official docs add, and one of them is a workaround for §11.** **[web]**

- **The valid index range is 12–28, not 15–28.** *"12 – 28: Waist and upper limb motor
  control parameter"*, with 29 carrying the weight. **Index 12 is waist yaw**, so the waist is
  controllable through `arm_sdk` too. Widen our range.
- **It works at Lock Standing.** *"The DDS interface supports upper limb control and can only
  be used in **Locked Stance, Movement Control 1 and Movement Control 2**."* Locked Stance is
  **fsm 4** — precisely the state where the arm *action* service gave us 7404. So if we are
  stuck at 4 again in a live window, **`rt/arm_sdk` is a documented way to still move the
  arms**, and `point_at` / `wave` / `hug` could be reimplemented as keyframes on it without
  resolving the 500/501 blocker at all. (Which ids "Movement Control 1 and 2" name is not
  stated; 500 and 501 are the obvious reading and are **not** confirmed.)
- **The vendor's recommended test state is exactly that:** *"it is recommended to suspend the
  robot and enter locked standing mode."* And this path needs **no debug mode** — it blends
  into the running controller.
- **Turn the arm action service off first.** *"If you need to independently develop upper
  limb actions via the `/arm_sdk` topic, you must first turn off Unitree's built-in Arm
  Control Service … The service name for the Arm Action Service is `g1_arm_example`."* Via
  `robot_state` 1001 `{"name":"g1_arm_example","switch":0}`. **This is the missing
  precondition we did not have, and it is also the clean explanation of 7400 contention: two
  owners of one topic.** Note the tension with the previous bullet is only apparent — you
  keep the motion controller, you drop the gesture service.
- The blend weight itself is vendor-described as we had it: *"When **weight** changes from 0
  to 1, the motor will gradually transition from the current position to the desired
  position. The faster the weight changes, the faster the transition."* The vendor's own
  routine ramps out over **2 s** at the end. Cite this instead of leaving "never step the
  weight" as SDK-example inference.

### 6.8 The hands — which ones are fitted is UNRESOLVED

`ROBOT-INVENTORY.md` §4 records **Dex3-1** hands; a `brainco_hand_server` was found running,
which implies **BrainCo**. Both sides have real evidence and **neither wins yet** — the full
argument is `ROBOT-PERIPHERALS.md` §4, and a hand skill must not be built until it is
settled, because the wrong choice silently mis-specifies every one of them.

What is observed, and only this: **[live]**

- A `brainco_hand_server` process holds `/dev/ttyUSB1` with **one BrainCo Revo2, medium
  RIGHT** hand (6 DoF, Modbus RTU 460800, slave 0x7f, fw 1.0.22.U, s/n BCXTR2124J2600024).
  No left hand answered on any of the four FTDI ports — but that probe speaks only Modbus RTU
  at 460800, so a Dex3 or Inspire hand would not have answered it either. Silence on
  ttyUSB0/2/3 is **not** evidence that nothing is attached.
- **No Dex3 driver exists anywhere on this Jetson** — a filesystem-wide search for `*dex3*`
  outside vendored SDK source returns nothing. ⚠️ **The inference we drew from that has been
  withdrawn.** Unitree states *"Unitree does **not** deploy services on the NVIDIA Jetson Orin
  module"* and describes the Dex3 driver as *"a resident service program"* the robot provides
  itself — i.e. it would run on the control board we cannot log into. **[web]** The search was
  aimed at the wrong host. The observation stands; "so nothing here would publish
  `rt/dex3/*/state`" does not.

From reading that server's source, its interface is: topics `rt/brainco/{left,right}/{cmd,
state}`, type `unitree_go::msg::dds_::MotorCmds_` / `MotorStates_`, **6 entries**, positions
and speeds normalised to **[0,1]**, finger order `[Thumb, Thumb_aux, Index, Middle, Ring,
Pinky]`. **[src]** Nobody has subscribed to those topics.

Against the BrainCo reading: `g1pilot` ships `g1_29dof_dx3.urdf` for *this* robot, and
`xr_teleoperate` carries `g1_body29_hand14.urdf` (2 × 7 DoF) and a working `Dex3_1_Controller`.
**[src]** For completeness, the Dex3 wire interface (correct for the product, unreachable on
this machine as configured) is: publish `unitree_hg::msg::dds_::HandCmd_` on
**`rt/dex3/{left,right}/cmd`**, subscribe `HandState_` on `rt/lf/dex3/{left,right}/state`,
7 motors, 9 pressure pads. **[src]** One look at each wrist settles the whole question —
three fingers and 7 DoF is Dex3-1, five fingers and 6 DoF is BrainCo Revo2.

**The one probe that settles this costs nothing and is now the highest-value action in the
section: passively subscribe `rt/dex3/{left,right}/state` (`HandState_`, already in our venv)
and `rt/inspire/state` (`MotorStates_`, likewise) for a few seconds.** Zero writes, and a
single message decides the whole argument — including the newly-raised possibility that a
Dex3 resident service is running on the control board where we cannot look. Full argument,
plus Unitree's G1-EDU variant→hand table, in `ROBOT-PERIPHERALS.md` §4.

**Two repo corrections follow.** `g1_protocol.REAL_TOPICS` has
`dex_left_cmd="rt/api/dex3/left/request"` — an RPC-shaped name with no support in any vendor
source, and now searched for in six official pages as well, with zero hits. **The hands are
not an RPC service**: there is no api_id and no JSON envelope, it is a raw `HandCmd_` publish
on `rt/dex3/{side}/cmd`. And `SPEC.md` §17.5's state type is wrong — it is `HandState_`, not
`MotorStates_`. Both confirmed by `basic_services_interface` and `dexterous_hand`. **[src]** +
**[web]**

---

## 7. The `voice` service

Not yet implemented in our bridge (`say` is a stub), but fully mapped and reachable today.
Service name is literally **`voice`** — not `audio`, not `vui`, and `/api/audiohub` does not
exist on this robot at all. **[live]**

| api_id | Call         | Parameter                                                    |
| ------ | ------------ | ------------------------------------------------------------ |
| 1001   | `TTS`        | `{"index": <uint32>, "text": "<utf8>", "speaker_id": <uint16>}` |
| 1002   | `ASR`        | registered by every vendor client, **called by none** — purpose unknown |
| 1003   | `START_PLAY` | `{"app_name": "...", "stream_id": "..."}` **plus raw PCM in `Request_.binary`** |
| 1004   | `STOP_PLAY`  | `{"app_name": "..."}` |
| 1005   | `GET_VOLUME` | empty → `{"volume": <uint8>}` — **range 0–100** |
| 1006   | `SET_VOLUME` | `{"volume": <uint8>}` — **clamp to 0–100** |
| 1010   | `SET_RGB_LED`| `{"R": <uint8>, "G": <uint8>, "B": <uint8>}` — each 0–255, **min 200 ms between calls** |

**[src]** Only one error code is declared for this service: **100 "Invalid parameter"**, and
the official docs add none — every AudioClient function is documented as *"returns 0 if the
call is successful, otherwise returns the relevant error code"* with no table anywhere.

**A naming trap in the official docs:** the page titled *"VuiClient Service Interface"*
(2025-10-22) documents **no VuiClient**. The only class it defines is
`unitree::robot::g1::AudioClient`, with exactly the six functions our api_id table maps —
`TtsMaker`, `GetVolume`, `SetVolume`, `LedControl`, `PlayStream`, `PlayStop`. **[web]** So our
`voice` reading is confirmed at the semantic level. It publishes **no api_ids**, has **no
`ASR` function** at all (our 1002 has no documented counterpart, consistent with "registered
by every client, called by none"), and no stream-query call. Three different things now carry
the letters *vui*: the Go2-only RPC service `vui` (§1.3), the switchable process
`vui_service` (below), and this mistitled page. None of them is the DDS service `voice`.

**Version floor.** *"Vui_Service ≥ 2.0.3.8, Vui Module ≥ 2.0.0.3. If the built-in service
version is low, please contact technical support."* **[web]** We have never obtained a
`vui_service` version — it lives on the control board — so "does this unit even serve these
calls" is a real, open question. **`GET_VOLUME` answers it: a value means alive and modern
enough, a `3203` means not.**

`speaker_id` **0 = Chinese, 1 = English**, and there is no third voice — the colleague
verified on this robot that neither reads Spanish intelligibly, which is why their stack
synthesises externally and pushes PCM through `PlayStream`. **[src]** The docs confirm both
halves independently and add a hard constraint: TTS is **local and offline**, and **"mixed
Chinese and English modes are not supported"** — so split a bilingual string by script or
fall back to `PlayStream`. **[web]** (Ignore the same page's contradictory capability bullet
claiming TTS "currently only supports Chinese"; its own table gives the English example.)
There is **no documented text length limit**, no utterance duration limit, and **no
documented behaviour for calling `TtsMaker` while speech is already playing** — so any cap we
impose is ours, and anything that must be interruptible should use `PlayStream` instead.

**`PlayStream`'s `stream_id` *is* the interrupt model, and this is the most useful thing the
audio docs contain:** *"the **same ID** means continuous playback from cache, **different
IDs** mean interrupting the current playback."* **[web]** So:

- one `stream_id` per utterance (the vendor uses a millisecond timestamp), reused for every
  chunk of that utterance so they concatenate gaplessly;
- to **barge in**, simply send the next utterance with a **new** `stream_id` — no `PlayStop`
  first. Design `say()` around this.

PCM must be **16 kHz mono 16-bit**; both vendor examples hard-reject anything else, and the
mobile-app path independently warns that *"the device only supports mono audio; stereo may
cause playback issues"*. **[web]** Our "96000 bytes (3 s) per chunk, roughly one chunk per
second of wall time" is an on-robot-example **convention, not a protocol requirement** — the
official example passes an entire ~5 s WAV in a **single** `PlayStream` call. Mark it
`[src, convention]`. Note that same example then `Sleep(3)`s and calls `PlayStop` on a 5 s
file, i.e. **the vendor does not wait for playback to finish**, which reinforces §6.6's read
that this service acks on receipt and needs its own duration model.

`PlayStop` takes **`app_name`** — the official example calls `PlayStream("example", <ts>,
pcm)` then `PlayStop("example")`, passing the app name, not the stream id. **[web]** Three of
four sources now agree; the on-robot C++ example is simply wrong. Since stopping is scoped by
`app_name`, we genuinely cannot stop `gemm-ai`'s stream and they cannot stop ours.

**`LedControl` is safety-relevant, not decorative.** R/G/B are 0–255 each and *"the interval
between calls to this interface must be greater than **200 ms**"* — enforce that in the
bridge, because an LLM-driven "pulse the lights" loop violates it trivially. **[web]** More
importantly the strip has **four uncoordinated writers**: the motion FSM's own state colours
(§4.5), the voice assistant (breathes blue on hearing, green on receiving an instruction),
this call, and us. Driving it **overwrites the operator's only indicator that the robot is in
Error State (solid red) or Debug Mode (solid yellow)**, and nothing documents how to hand the
strip back. Either do not expose LED control to the LLM, or expose it only as a short flash
that restores afterwards.

Three things to get right if we implement it:

- **`_CallRequestWithParamAndBin` already exists** in our installed `rpc/client.py`, so PCM
  playback needs no dependency change. Register
  `("voice", (1001, 1003, 1004, 1005, 1006, 1010))` on the existing `_G1Client` and call.
  **[src]** (The "missing `unitree_sdk2py.g1` package" that used to complicate this is gone —
  see the venv correction in §3.5.)
- **The vendored Python `TtsMaker` has a bug**: `self.tts_index += self.tts_index`, so
  `index` stays 0 forever. The A2 copy of the same file has the correct `+= 1`. If the
  firmware dedupes on index, repeated utterances silently do not play. **[src]** The official
  prototype is `TtsMaker(text, speaker_id)` with **no `index` parameter at all** **[web]**, so
  `index` is client-internal and the bug has no *documented* consequence — and no documented
  defence either. Fix it to `+= 1`.
- **Use our own `app_name`** (`"c3po"`): `gemm-ai.service` is a live writer on this service
  with `APP_NAME = "gemm-ai"`. **[live]**

`GET_VOLUME` (1005) is the one genuinely read-only call on the whole service. It now settles
three things at once: the value range (documented 0–100), whether the service is alive, and
whether it is new enough (§ version floor above).

### 7.1 `rt/audio_msg` has a full documented schema — including a signal we should be using

Type is `std_msgs::msg::dds_::String_`, carrying JSON. **Two different payload shapes ride the
same topic.** **[web]**

```json
{"index": 1, "timestamp": 29319303490, "text": "Hello", "angle": 90,
 "speaker_id": 0, "sense": "unknown", "confidence": 0.95,
 "language": "en-US", "is_final": true}

{"play_state": 1}
```

| Field | Meaning |
| ----- | ------- |
| `index` | unique message sequence number |
| `text` | speech recognition result |
| **`angle`** | **azimuth of the speaker, 0–180** |
| `speaker_id` | **speaker *recognition* (diarization) result** — see the trap below |
| `sense` | emotion recognition result |
| `confidence`, `language`, `is_final` | ASR confidence; language tag; end flag (streaming mode; non-streaming by default) |
| **`play_state`** | **`0` = playback stopped, `1` = playback started** |

Two wins and a trap:

- **`angle` is free sound direction-of-arrival**, on a topic we already know how to read — a
  `look_at_speaker` input with no extra subscription and no extra type.
- **`play_state` is a real playback start/stop event** — the audio equivalent of the
  `rt/arm/action/state` topic we want for gestures. Subscribing it gives `say()` true
  completion detection instead of a guessed duration model.
- ⚠️ **`speaker_id` means two different things on one service.** In `rt/audio_msg` it is the
  ASR's *diarization* output; in `TtsMaker` it is the *voice role* (0 = Chinese, 1 = English).
  Same key, unrelated meanings.

Unverified for this firmware: whether `play_state` is published at all here, and whether it
tracks **our** `PlayStream` or only the vendor assistant's playback.

### 7.2 The microphone, and the assistant that owns it

**The mic is not on this service and not on DDS.** Raw audio is a UDP multicast feed:
**239.168.123.161:5555, 16 kHz mono s16le** — in Unitree's own C++ example, and the official
docs reproduce the same group, port and the interface-selection rule (*pick the local address
starting `192.168.123.`*, i.e. eth0). **[src]** + **[web]** Joining on `INADDR_ANY` gets you
zero packets with no error. Our bridge's CycloneDDS config
(`<AllowMulticast>false</AllowMulticast>` + a unicast peer) is fine for the `voice` RPC and
for `rt/audio_msg`, and simply irrelevant to the mic — a future `listen()` opens its own
socket. **[src]**

**ASR output is gated on a mode we cannot set.** *"When the robot's microphone is turned on
(**switch to the wake-up mode in the APP or remote control**), the built-in microphone + ASR
module will recognize the human voice."* **[web]** The two modes are *wake-up conversation*
and *push-button conversation*, switched by **L1+L2** on the remote or in the App under
【Device】→【Data】→【Audio】→【Voice assistant】. Wake word is *"Hello Robot"*; the dialogue
ends after **15 s** of silence; **L2+Select** wakes it (or press-and-hold to record in
push-button mode) and **L1+Select** force-interrupts. So an ASR-over-DDS `listen()` has a
**human prerequisite we cannot satisfy over DDS**. Whether the raw multicast feed is gated the
same way is unknown and worth testing — if it is always live, our own STT is independent of
the vendor assistant entirely.

**The assistant competes with us, and cannot be disabled programmatically.** One 8 Ω speaker,
no arbitration — the same pattern as §1.4. Unitree's own advice when it is talking over you is
to interrupt it from the remote: *"you need to wait for the playback to complete before the
next command, or press 'L1+Select' to interrupt."* **[web]** The entire "close the interaction"
section of the vendor page is one sentence with no mechanism, no button and no API. The
assistant needs the Internet for its GPT path (firmware ≥ 1.3.0); on an air-gapped
192.168.123.x robot it degrades to an offline *"Hello, I am here"*, which is the quietest
practical state. **Do not plan on disabling it in software.**

### 7.3 `vui_service`, and why we must never switch it off

`vui_service` is the **switchable process** that provides TTS, `PlayStream`, volume **and the
light strip** — Unitree's service list calls it the *"Audio and Lighting Control Service"*.
**[web]** It is one service, not separable, so turning it off to silence the assistant would
silence **us** as well. Never do it. (And note again: this name is a `robot_state` service
name, not the RPC service `vui` of §1.3.)

### 7.4 Is audio FSM-gated? Almost certainly not — but test it

Nothing in 45 pages states that audio works in every FSM state. The evidence is **structural,
not stated**, and it is worth being precise about: **[web]**

- The arm page carries an explicit FSM caveat (7404, `GetActionList`'s per-action FsmID
  requirements) **and** an explicit debug-mode kill. The audio page carries **neither** —
  none of `TtsMaker`/`PlayStream`/`PlayStop`/`GetVolume`/`SetVolume`/`LedControl` has any
  state precondition, remark or error mentioning FsmID, motion control or debug mode.
- They are separate services in the vendor's own list: `ai_sport` (motion), `g1_arm_example`
  (arms), `vui_service` (audio + lighting).
- Audio's firmware dependencies (Vui Service, Webrtc Bridge, Audio Hub) name no motion
  component.

That is an argument from silence plus service separation. It **supports** the hypothesis that
speech is a safe acknowledgement channel when motion is refused — which would be genuinely
valuable, because it would make `say()` the bridge's fallback for every refused motion
command. **Test it cheaply rather than assume it:** call `GET_VOLUME` (read-only, zero
motion) once in each state we can reach — zero-torque (0), damp (1), preparation (4), and in
the empty-name debug state — and log the code. If it answers everywhere, wire `say()` into
every refusal path.

**One more official name worth recording:** the app-side playback page names an **"Audio Hub"**
firmware component (≥ 1.0.1.0), alongside Vui Service and Webrtc Bridge. **[web]** That is the
most plausible origin of the `/api/audiohub` name §1.3 correctly found absent on the Jetson —
it is an app/WebRTC-side component, **not a DDS RPC service**. Keep the "does not exist on
this robot" finding; stop calling the name invented. (Do **not** cite that page for the
`PlayStream` contract — it documents the mobile app's Player, not the SDK path.)

---

## 8. `robot_state` — the probe we should run first, and never have

Service `robot_state`, `rt/api/robot_state/request`. **This service is the B2 lineage** — the
vendor says so: *"This interface is reused from the device status service interface of
**B2**"*, pointing at `unitree_sdk2py/b2/robot_state`. **[web]** That matters, because the b2
client is a **superset** of the go2 one we planned to use, and both are installed:

| api_id | Call             | Parameter                          | Response | In go2 client? |
| ------ | ---------------- | ---------------------------------- | -------- | -------------- |
| 1001   | `SERVICE_SWITCH` | `{"name": "<svc>", "switch": 0\|1}` | `{"name":…, "status": int}` — **a write, do not call** | yes |
| 1002   | `SET_REPORT_FREQ`| `{"interval": int, "duration": int}` — both **seconds** | — | yes |
| 1003   | `SERVICE_LIST`   | `{}`                               | JSON array of `{"name": str, "status": 0\|1, "protect": bool}` | yes |
| 1004   | `LOWPOWER_SWITCH`| `{"switch": <int>}`                | — **a write, do not register** | **no** |
| 1005   | `LOWPOWER_STATUS`| `{}`                               | `{"status": int}` — **a pure read** | **no** |
| 1006   | `GET_PKG_VERSION`| `{}`                               | `{"packageVersion": …, "moduleVersionMap": …}` — **a pure read** | **no** |

**[src]**, verified in our own venv (below); the B2 provenance and the 1002 units are
**[web]**. `status == 5` from 1001 means the service is protected (client maps it to `5202
SERVICE_PROTECTED`); any other non-0/1 status maps to `5201`. **[src]**

⚠️ **Polarity foot-gun on 1001:** the input `swit` is documented `1 = on, 0 = off`, while the
returned `status` is documented `0 = on, 1 = off` — **inverted**. **[web]** Our Python client
tests `status != 0 and status != 1` and otherwise accepts either, so it will not catch a
mis-read; anything we write on top of it must not assume the two fields share a convention.

**Two new pure reads, both high value:**

- **1006 `GET_PKG_VERSION`** returns a package version **and a `moduleVersionMap` from the
  service itself**, i.e. from the control board. That is exactly the gap §1 declares
  unclosable ("this does not give the `ai_sport` or `vui_service` versions — those live on
  the control board, which has no SSH"). **It is an RPC, so SSH is irrelevant.** Between this
  and `GetServerApiVersion()` (§1.5) we could finally version the half of the robot we cannot
  log into — including the `vui_service ≥ 2.0.3.8` floor in §7.
- **1005 `LOWPOWER_STATUS`** is a candidate explanation for §11 that is not on the ranked
  list at all: **a robot in a low-power state accepting `SetFsmId(500)` with `code 0` and not
  moving is exactly our signature**, and it would also explain the operator's remote failing.
  One read. Do **not** register 1004 — same reasoning as "register 1001 and nothing else".

**1003 is still the highest-value zero-motion probe on this entire robot**, and we now know
roughly what a healthy answer looks like. Unitree publishes the expected names: **[web]**

| Service name | Description |
| ------------ | ----------- |
| `ai_sport` | **Main Motion Control Service** |
| `basic_service` | Basic Service |
| `g1_arm_example` | Upper Limb Motion Service |
| `vui_service` | Audio and Lighting Control Service |
| `unitree_slam` | Navigation Service |
| `lidar_driver` | *(named in the SLAM page, not the list)* |

Two things fall out. `ai_sport` being *"Main Motion Control Service"* corroborates §3.3's
`ai` → `ai_sport` row and makes `SelectMode("ai")` the expected §11 Rank-5 fix. And
`g1_arm_example` is the shipped name of the arm service — **a vendor *example* promoted to a
product service**, which is a plausible common cause for §6.4/§6.5's rough edges: the
`rt/armsdk` vs `rt/arm_sdk` string mismatch, the duplicate-key bug in the C++ action map, and
the contradictory 7404 polarity. Note the naming spread for SLAM: `unitree_slam` here,
`slam_operate` as the RPC service, `slam_nav` in `master_service`'s protect config — three
names, and only the ServiceList response tells us which exists on this unit.

1003 also proves a structural point already confirmed indirectly — **topics can be absent
until a service is switched on**: `/utlidar/*` only exists while `lidar_driver` is enabled,
which is why a 2026-08-07 conclusion that those topics "do not exist in any DDS domain" was
wrong. **[src]**

**Use the b2 client, not the go2 one** — both are installed, and only b2 has 1005/1006:

```python
from unitree_sdk2py.b2.robot_state.robot_state_client import RobotStateClient
c = RobotStateClient(); c.SetTimeout(3.0); c.Init()
code, services = c.ServiceList()              # 1003, parameter "{}"
code, status   = c.LowPowerStatus()           # 1005, pure read
code, pkg, mods = c.GetPkgVersion()           # 1006, pure read
```

⚠️ **`RobotStateClient.Init()` registers all six api_ids, including the two writes (1001
`ServiceSwitch`, 1004 `LowPowerSwitch`).** Registering an api_id you do not intend to send is
how it gets sent by accident later (§3.5). If this goes anywhere near a tool an LLM can
reach, build the reads on our own `_G1Client` with only 1003/1005/1006 registered.

**Venv correction, verified while folding in these findings.** `apps/bridge/.venv`'s
`unitree_sdk2py` is now pinned to commit **`65691c8`**, not `a7dff75`, and ships
`a2 as2 b2 comm core g1 go2 h1 h2 idl rpc utils` — including a working
`unitree_sdk2py.g1` (`arm`, `audio`, `loco`) and `unitree_sdk2py.b2.robot_state` at api
version **`1.0.0.2`**. **[live]** This supersedes §3.5's and `ROBOT-PERIPHERALS.md` §5.5's
claim that the pin ships "only `core go2 idl rpc utils`" and that "there is no `g1` package
at all" — that was true of `a7dff75` and is no longer true of the venv on this machine. The
`__init__.py` root cause described there is still the right explanation for why `a7dff75`
behaved that way; it just no longer applies. **The go2 client's api version `1.0.0.1` is also
superseded by b2's `1.0.0.2`.**

QoS, per the colleague's own note in `g1_service.py` recording a `ros2 topic info -v` against
this unit on 2026-08-11 (their observation, not ours): the vendor's
`/request` **subscriber** is BEST_EFFORT and its `/response` **publisher** is RELIABLE. So
publish RELIABLE to `/request` (compatible) and subscribe BEST_EFFORT to `/response`
(compatible with either). **[src]**

---

## 9. Types and message layouts

### 9.1 `unitree_hg` vs `unitree_go`

`unitree_hg` is the humanoid family and `unitree_go` the quadruped — but **the G1 uses both**,
and the same type *name* means different things in each. Getting this wrong does not raise an
error; DDS matches by type, so a wrong type silently never delivers.

| Type name           | `unitree_hg` (G1)                              | `unitree_go` (Go2)                        |
| ------------------- | ---------------------------------------------- | ----------------------------------------- |
| `LowState_`         | 9 fields, 35 motors, **no battery**            | 20 motors, embeds `bms_state`, `foot_force`, `power_v/a` |
| `MotorState_`       | `temperature` is `int16[2]`, has `vol`         | `q_raw/dq_raw/ddq_raw`, single `int8` temperature, `lost` counter |
| `IMUState_`         | `temperature` **int16**                        | `temperature` **int8** — different wire size |
| `SportModeState_`   | **4 fields**: `fsm_id, fsm_mode, task_id, task_time` | **16 fields**: pose, velocity, foot_force, `error_code`… |

**[src]** Do not port Go2 field access to the G1. `ROBOT-INVENTORY.md`'s battery gap is
exactly this difference (§9.4).

**A named, Unitree-authored example of exactly this mistake.** `basic_services_interface`
(2025-10-21) publishes a `MotorState_` under the `unitree_hg` heading that carries Go2-only
`q_raw` / `dq_raw` / `ddq_raw` **and** swaps `vol` with `sensor[2]`. **[web]** Anyone reading
per-motor temperature or voltage from that page lands 12 bytes off and gets garbage that
still decodes without error. The same page's `IMUState_` and `LowCmd_` are correct; its
`LowState_`, `HandState_`, `HandCmd_`, `MotorCmd_` and `PressSensorState_` are not (§9.3,
`ROBOT-PERIPHERALS.md` §4.4). This is why rule 3 in the citation guide exists.

Which family each G1 topic uses is listed in §10's census. The mixed ones to remember:
`rt/odommodestate` and `rt/hand_sdk` and `rt/wirelesscontroller` are **go** types on a
humanoid.

### 9.2 What Python can consume today

`unitree_sdk2py` 1.0.1 (identical in our bridge venv and the newer checkout) **ships**: **[src]**

- `unitree_hg`: `BmsCmd_ BmsState_ HandCmd_ HandState_ IMUState_ LowCmd_ LowState_
  MainBoardState_ MotorCmd_ MotorState_ PressSensorState_`
- `unitree_go`: `AudioData_ BmsCmd_ BmsState_ Error_ HeightMap_ IMUState_ LidarState_ LowCmd_
  LowState_ MotorCmd_ MotorCmds_ MotorState_ MotorStates_ PathPoint_ SportModeState_
  TimeSpec_ UwbState_ WirelessController_` (and more)
- `unitree_api`: `Request_ Response_` and their sub-structs
- ROS: `std_msgs Header_/String_`, `builtin_interfaces Time_`, the `geometry_msgs` pose/twist
  set, `nav_msgs MapMetaData_/OccupancyGrid_/Odometry_`, `sensor_msgs PointCloud2_/PointField_`

**Does not ship** — would need a hand-written `cyclonedds` IdlStruct: **[src]**

| Missing type                                | Blocks |
| ------------------------------------------- | ------ |
| `unitree_hg::msg::dds_::SportModeState_`     | passive FSM readback (see below) |
| `sensor_msgs::msg::dds_::Imu_`               | `rt/utlidar/imu_livox_mid360` from Python |
| `tf2_msgs::msg::dds_::TFMessage_`            | any TF consumption (no `tf2_msgs` package at all) |
| `unitree_hg_doubleimu::doubleIMUState_`, `AgvBmsState_` | nothing we want |
| `sensor_msgs CompressedImage_/Image_/CameraInfo_`, `nav_msgs Path_` | image and path topics |

**Correction to `MENTAL-MODEL.md` (lines ~180–183):** it says the `rt/state_estimator/*`
topics "carry `nav_msgs::msg::dds_::Odometry_`, which would mean hand-writing the ROS IDL."
**That is false** — `Odometry_` and all its dependencies are already in our venv, so
`rt/state_estimator/odom_pelvis` (live at ~51 Hz, 2.5× our current pose rate, with a
covariance) is directly consumable today. **[live]**

**The one type worth hand-writing.** `unitree_hg::msg::dds_::SportModeState_` is the entire
humanoid FSM state and it is four fields:

```python
@dataclass
@annotate.final
@annotate.autoid("sequential")
class SportModeState_(idl.IdlStruct, typename="unitree_hg.msg.dds_.SportModeState_"):
    fsm_id:    types.uint32
    fsm_mode:  types.uint32
    task_id:   types.uint32
    task_time: types.float32
```

**[src]** That is ~20 lines and it buys: continuous FSM observation **with zero writes to the
robot** (exactly what the suspect-802 question needs — "watch `fsm_id` transition during a
supervised motion window"), replacement of our 7001/7002 RPC poll with a push subscription,
and `task_id`/`task_time`, which is **real gesture progress** and retires the false-3104
problem in §6.6.

**Officially confirmed, including the name and the firmware floor.** *"After the firmware
version `1.5.1` update, you can obtain the current state of G1 through the topic
`rt/sportmodestate`"*, with the IDL published verbatim and identical to the four fields
above. **[web]** Ours is **1.5.3.8**, past the floor. Field semantics: `task_id` is the upper
limb interaction action id; `task_time` is *"execution time of upper limb actions, seconds,
increments from 0 until the action is completed"* — and, usefully, *"**when the action is a
handshake, this value remains constant during the holding period**"*, which is exactly the
signal needed to detect the 7401 holding latch (§6.4).

**So `g1_protocol.REAL_TOPICS` is wrong here:** it uses `rt/lf/sportmodestate`; the vendor
documents the **bare `rt/sportmodestate`**. Both spellings appear in the wild — the
`rt/`-vs-`rt/lf/` pairing is a systematic convention (§10), so an `lf` twin plausibly also
exists — but write the bare name, since that is the one Unitree documents and the one the SDK
examples use. Whether the `lf` twin is published on this unit remains unconfirmed. **[?]**

**A trap on that topic.** The colleague's stack documents, twice and in two files, that
`/lf/sportmodestate` has **two types registered at once** (`unitree_go` *and* `unitree_hg`),
which breaks `ros2 bag record --all` and sends their foxglove bridge into a "bad optional
access" loop. **[src]** So our earlier failure there is not evidence the go type is absent —
we subscribed `String_`, the wrong type entirely. Their notes also put the robot's total
topic count at **~121**, far more than any source enumerates.

### 9.3 `LowState_` — exact field layout

Identical in the firmware-matched vendor `.msg` and in the newest C++ SDK header: **[src]**

```
uint32[2]      version
uint8          mode_pr          # 0 = PR (series pitch/roll ankles), 1 = AB (parallel)
uint8          mode_machine     # ROBOT TYPE (ours = 5), not a mode — §4.2
uint32         tick             # ms counter
IMUState       imu_state
MotorState[35] motor_state      # fixed array of 35, not a sequence
uint8[40]      wireless_remote  # raw joystick frame — §9.5
uint32[4]      reserve
uint32         crc
```

Nine fields, ending at `crc`. Per-motor: **[src]**

```
uint8     mode
float32   q, dq, ddq, tau_est
int16[2]  temperature   # [0] casing (vendor limit 85 C), [1] winding (limit 120 C)
float32   vol           # motor bus voltage — the only voltage inside LowState_
uint32[2] sensor
uint32    motorstate    # per-motor error/status word
uint32[4] reserve
```

IMU: `float32[4] quaternion` (**w,x,y,z** order), `float32[3] gyroscope`, `float32[3]
accelerometer`, `float32[3] rpy` (ZYX Euler, body frame; `rpy[2]` is yaw), `int16
temperature`. The quaternion order was previously inferred from the vendor building
`Eigen::Quaternionf(q[0],q[1],q[2],q[3])`; it is now **vendor-stated** — the IDL comment
reads `// Quaternion QwQxQyQz`. **[web]** Drop the inference caveat. There is a **second
IMU** on its own topic, `rt/secondary_imu`, same `IMUState_` type, used by vendor examples as
the torso IMU while the pelvis IMU rides inside `LowState_`. **[src]**

⚠️ **The official `LowState_` omits the leading `version` field**, listing eight fields from
`mode_pr`. **[web]** **Our IDL wins** — it is what CycloneDDS actually deserialises with, and
our live `rt/lf/lowstate` subscription decodes cleanly, which it could not if the wire layout
were missing eight leading bytes. The value of the conflict is diagnostic: that page was
written against a different IDL revision, so treat every struct on it as suspect (§9.1).

`MotorCmd_.mode` semantics, newly documented: **`0 = Disable, 1 = Enable`**. **[web]** Note
this is the *body* motor convention — the same `unitree_hg::msg::dds_::MotorCmd_` struct
carries a completely different bit-packed `mode` when it rides in a `HandCmd_`
(`ROBOT-PERIPHERALS.md` §4.4).

**Motor index map** (`G1JointIndex`, 29 real motors in a 35-slot array). The two name columns
are selected by `mode_pr`; the official table gives both, and **the AB names cover the waist,
not just the ankles**: **[src]** + **[web]**

| idx | `mode_pr == 0` (PR) | `mode_pr == 1` (AB) |
| --- | ------------------- | ------------------- |
| 0–3 | L hip pitch / roll / yaw, L knee | same |
| 4 / 5 | L ankle **pitch** / **roll** | L ankle **B** / **A** |
| 6–9 | R hip pitch / roll / yaw, R knee | same |
| 10 / 11 | R ankle **pitch** / **roll** | R ankle **B** / **A** |
| 12 | waist yaw | waist yaw |
| 13 / 14 | waist **roll** / **pitch** | **WAIST_A** / **WAIST_B** |
| 15–21 | L shoulder pitch/roll/yaw, elbow, wrist roll/pitch/yaw | same |
| 22–28 | R arm, same order | same |
| **29** | **not a joint — the `rt/arm_sdk` blend-weight slot** (`motor_cmd[29].q`, 0..1) | |
| 30–34 | no documented meaning anywhere — the official tables **stop at 28** | |

**Variant differences, newly documented** (`joint_motor_sequence`, 2025-03-17): **[web]**

- **23-DoF** blanks **13, 14** *and* **20, 21, 27, 28** — so it loses waist roll/pitch **and
  both wrist pitch+yaw pairs**, keeping only wrist roll per arm. Our previous note flagged
  only 13/14; widen it.
- **14-DoF** blanks 0–14 entirely and keeps only 15–28 — an **arms-only** build.
- The **hand** order (`HandCmd_`/`HandState_.motor_state`) is
  `thumb_0, thumb_1, thumb_2, middle_0, middle_1, index_0, index_1` for **both** hands —
  see `ROBOT-PERIPHERALS.md` §4.4, where this settles a long-standing contradiction.

Caveat on that page: it also carries the **incompatible `mode_machine` numbering** discussed
in §4.2. The *index* tables agree with our vendored `G1JointIndex` exactly and are safe; only
its variant→integer mapping is in dispute.

`motor_count = 35` in our live `get_state` is consistent with the fixed array, not with the
motor count. **[live]**

Publish rates: `rt/lowstate` is **500 Hz** per vendor source (`HIGH_FREQ` flag, `control_dt =
0.002`); `rt/lf/lowstate` (`lf` = low frequency) measured **~20 Hz** in two rosbags recorded
off this robot. **[live]** Our bridge subscribes the `lf` one and reports `lowstate_age_s`
0.02–0.04, consistent. Whether the 500 Hz topic exists on *this* robot is unconfirmed — the
colleague's foxglove config annotates `^/lowstate$` as "the one the sim publishes".

### 9.4 Battery — definitively located, and it is not in `LowState_`

**This question is settled, not guessed.** `unitree_hg::msg::dds_::LowState_` has **no
battery field at all** — no `bms_state`, no `power_v`/`power_a`, no `foot_force`, no
`temperature_ntc`. All of those exist in `unitree_go`'s `LowState.msg`, which is why Go2 code
reads battery from lowstate and why ours reads `null`. **[src]**

State of charge is on its own topic:

| | |
| --- | --- |
| Topic | `rt/lf/bmsstate` |
| Type | `unitree_hg::msg::dds_::BmsState_` |
| Rate | **~20 Hz** — 580 msgs / 28.97 s and 302 / 15.07 s in two rosbags recorded off this robot (2026-08-11 and 2026-08-13) **[live]** |
| Field | **`soc`, `uint8`, percent** |

Layout: **[src]**

```
uint8 version_high; uint8 version_low; uint8 fn
uint16[40] cell_vol; uint32[3] bmsvoltage; int32 current
uint8  soc            <-- battery_pct
uint8  soh
int16[12] temperature
uint16 cycle; uint16 manufacturer_date
uint32[5] bmsstate    <-- five status words, no decoder shipped
uint32[3] reserve
```

`soc` is 0–100, not 0–255 — the vendor's own predicate is
`low_battery(bms) { return bms.soc() < 20.0; }`. **[src]** The Python type is **already in our
venv**, so the fix needs no new IDL and no pin bump:

```python
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import BmsState_
ChannelSubscriber("rt/lf/bmsstate", BmsState_)   # int(msg.soc)
```

`g1_protocol.REAL_TOPICS.bmsstate` already carries the right name — previously `[web]`-only
from the WebRTC reverse engineering, now upgraded by the bags to observed-on-this-robot.
`SIM_TOPICS.bmsstate` is `None`, so create the subscriber only when the profile provides a
topic, and log a message counter the way `pose_messages_received` already does — a wrong type
silently never delivers.

**Say plainly what is still not verified:** we have never subscribed to this topic
ourselves. The evidence is the type (definitive, three independent copies agree) plus two
recordings made off this robot by the colleague's stack. Whether `current` is mA or 10 mA,
whether `bmsvoltage[3]` is mV, and how many of the 40 `cell_vol` / 12 `temperature` entries
this pack populates are all **unknown** and settle with one decoded message.

**Both halves of this are now vendor-confirmed, and the open half is confirmed unanswerable
by reading.** The topic/type pair `rt/lf/bmsstate` → `unitree_hg BmsState_` appears in
Unitree's own G1 topic table, and the absence of battery from `LowState_` is visible in their
own struct listing. **[web]** But **no official page documents the `BmsState_` fields at
all** — a grep across all 45 returns exactly that one topic-table row, and `BmsState_` is
missing from the page that enumerates the `unitree_hg` structures. So the units of `current`,
the scaling of `bmsvoltage[3]`, the population of `cell_vol[40]`/`temperature[12]`, and the
meaning of the five `bmsstate` words **cannot be looked up**. Make decoding one live message
a first-class goal of the next window rather than something a doc might answer. Note also
there is **no high-frequency `rt/bmsstate` twin** in the vendor's table: `rt/lf/bmsstate` is
the only battery topic they list.

**One indirect corroboration on units, worth knowing but not authoritative.** The vendor SLAM
service broadcasts a JSON telemetry blob on `rt/slam_info` (`String_`, so no IDL work) whose
`robot_data` payload states units explicitly: `batteryAmp` in **mA**, `batteryVol` in **mV**,
`batteryPower` in **percent**, `batteryTemp` and `motorTemp[]` in **°C**, plus `motorError[]`
and CPU temp/usage/memory/frequency. **[web]** It only publishes while `unitree_slam` runs,
and it is that service's own reporting rather than raw `BmsState_` fields — so it corroborates
plausible units, it does not settle the struct's scaling. Its `sportMode` and `gaitType`
fields are documented as `-1` / "temporarily unavailable"; do not build on them.

While we are there: worth surfacing `soh`, `max(temperature)` as a thermal fault, and a
`low_battery` fault at `soc < 20` to match the vendor's own threshold. Today the reassuring
`faults: none, battery: null` in `ROBOT-INVENTORY.md` §6 is **not evidence of a healthy
pack** — `battery_pct` is hardcoded `None` and the only value ever appended to `faults` is
`stale_lowstate_*`. It is evidence that we never looked. **[src]**

### 9.5 `wireless_remote[40]` — the field we throw away

The 40-byte blob inside `LowState_` decodes with the vendor's own on-robot header: **[src]**

```c
typedef struct { uint8_t head[2]; BtnUnion btn;
                 float lx, rx, ry, L2, ly;      // NOTE the order
                 uint8_t idle[16]; } xRockerBtnDataStruct;
```

**Axis-order trap:** inside the packet the floats are **lx, rx, ry, L2, ly** — *not* the
lx/ly/rx/ry order of the `rt/wirelesscontroller` DDS message. Reading it in message order
silently swaps axes. **[src]** Unitree's `remote_control_data` page reproduces the identical
struct and the identical bit order, so this warning is now **doubly sourced** and should be
stated as fact, not caution. **[web]** Two additions from that page: the joystick range is
**[−1.0, 1.0]**, and the `L2` float is an **analog axis distinct from the L2 button bit** —
our mask table lists the bit but our prose never mentioned the analog channel.

⚠️ **One flag on that page:** its decode snippet types the message as
`unitree_go::msg::dds_::LowState_` — the **Go2** LowState, not the G1's `unitree_hg` one. The
offset arithmetic differs between the two. Copy the *struct definitions* from the page and
the *accessor* from our own hg IDL; never the snippet as written.

**`rt/wirelesscontroller` does not appear in the official G1 documentation at all.** Zero
hits across all 45 pages, and it is absent from Unitree's own G1 topic table, while the
`LowState_.wireless_remote[40]` path is documented **twice**. **[web]** So its census row
(§10) should be demoted from "Go2 example only, presence unverified" to **positive evidence
of absence**: treat it as Go2-only unless a live `DCPSPublication` scan proves otherwise.
That makes decoding the 40-byte blob not merely the cheap option — **it is the only
vendor-documented route to remote state on the G1.**

16-bit key field, bit 0 → bit 15: **[src]**

| Mask | Btn | Mask | Btn | Mask | Btn | Mask | Btn |
| ---- | --- | ---- | --- | ---- | --- | ---- | --- |
| 0x0001 | R1 | 0x0010 | R2 | 0x0100 | A | 0x1000 | up |
| 0x0002 | L1 | 0x0020 | L2 | 0x0200 | B | 0x2000 | right |
| 0x0004 | start | 0x0040 | F1 | 0x0400 | X | 0x4000 | down |
| 0x0008 | select | 0x0080 | F2 | 0x0800 | Y | 0x8000 | left |

The SDK also treats an all-zero 40-byte block for **3000 ms** as `isJoystickTimeout_` — a
ready-made "no controller present" predicate to copy. **[src]**

`state.py::_on_lowstate` currently keeps only `tick`/`mode_machine`/`motor_count`/`has_imu`
and discards this. Decoding it is free (no new subscription, no new type) and it converts
"the operator's remote didn't work" from an anecdote into a measurement — whether the R3 is
transmitting at all, and **exactly which buttons were pressed** (crucially **X vs Y** — see
§11.3, and note the two-second hold rule in §4.5). **Nothing in any source, ours or
Unitree's, documents which combinations the firmware itself intercepts.** The official
`remote_control` page maps combinations to its own ①…⑧ symbols and never to FSM ids, so open
question 14 survives the documentation intact. **[src]** + **[web]**

### 9.6 The fault bit tables — a decoder for a field we already receive and discard

Unitree publishes two bitmask tables (`common_istakes_and_definitions`, 2024-10-25 — the
vendor's own typo). Neither table says which DDS field carries its word, which is the one
thing that would make them immediately usable. **[web]**

**Per-motor status word.** This one *does* have an obvious carrier: `LowState_.motor_state[i]
.motorstate`, the `uint32` §9.3 lists and `state.py` throws away. Bits:

| | | | |
| --- | --- | --- | --- |
| `0x1` overcurrent | `0x2` transient overvoltage | `0x4` sustained overvoltage | `0x8` transient undervoltage |
| `0x10` chip overheat | `0x20` MOS overheat | `0x40` MOS temp anomaly | `0x80` shell overheat |
| `0x100` shell temp anomaly | `0x200` winding overheat | `0x400` rotor encoder 1 error | `0x800` rotor encoder 2 error |
| `0x1000` output encoder error | **`0x2000` calibration/BOOT data error** | `0x4000` abnormal reset | `0x8000` motor locked / master auth error |
| `0x10000` chip validation error | **`0x20000` calibration mode warning** | `0x40000` comms verification error | `0x80000` driver version too low |
| `0x40000000` motor: PC connection timeout | `0x80000000` PC: motor disconnection timeout | | |

**Ship this as a decoder in `state.py` now** — zero new subscriptions, zero new types. It
would surface, among other things, `0x2000` and `0x20000` **on the two waist motors (indices
13/14)**, which is a direct test of §11.3's waist-calibration hypothesis. It also replaces
`faults.py`'s web-grade catalogue with a vendor table.

**Total-device status word.** Carrier unknown; candidates are `unitree_go SportModeState_
.error_code` on `rt/odommodestate` (which we already receive) or `MainBoardState_` on
`rt/lf/mainboardstate`. Bits: `0x1` upper control command error, `0x2` lower-layer feedback
timeout, `0x4` IMU feedback timeout, `0x8` motor feedback timeout, `0x10` battery feedback
timeout, `0x20` remote-control feedback timeout, `0x40` battery model error, `0x80` soft-start
error, `0x100` motor state error, `0x200`/`0x400`/`0x800` motor over/under-voltage and
overcurrent protections, **`0x1000` soft emergency-stop switch is pressed**, `0x2000` SN
error, `0x4000`/`0x8000` upper/lower model error, `0x10000` USB device error, `0x40000` crotch
IMU timeout, `0x80000`/`0x100000` motherboard battery/motor undervoltage protection.

**`0x1000` is the interesting one**: a latched soft e-stop would stop the SDK **and** the
remote — the only §11 candidate that explains both with no extra assumption, and it pairs
with the **solid red "Error State"** LED (§4.5) as a free visual tell.

⚠️ **The page's markdown is transcription-damaged** — rows `0x800`/`0x1000`/`0x2000`/`0x4000`
appear twice in the motor table. Treat the bit values above as the vendor's intent, verify
against a real non-zero word before acting on any single bit.

---

## 10. DDS topic census

Exact type names. Rates are measured where marked `[live]`.

### State — robot to us

| Topic | Type | Rate | Evidence |
| ----- | ---- | ---- | -------- |
| `rt/lowstate` | `unitree_hg::msg::dds_::LowState_` | 500 Hz | **[src]** (existence on this unit unconfirmed) |
| `rt/lf/lowstate` | `unitree_hg::msg::dds_::LowState_` | ~20 Hz | **[live]** — what our bridge uses |
| `rt/lf/bmsstate` | `unitree_hg::msg::dds_::BmsState_` | ~20 Hz | **[live]** (bags) |
| `rt/secondary_imu` **and** `rt/lf/secondary_imu` | `unitree_hg::msg::dds_::IMUState_` | — | **[src]** + **[web]** torso IMU. **Both spellings are vendor-documented** — the earlier note that "vendor code uses `rt/secondary_imu`, no `lf/`" is superseded; presence on this firmware still unverified |
| `rt/sportmodestate` | `unitree_hg::msg::dds_::SportModeState_` | — | **[web]** confirmed, with the IDL, for firmware **≥ 1.5.1** (ours is 1.5.3.8) — §9.2 |
| `rt/lf/sportmodestate` | **two types registered at once** (`unitree_go` + `unitree_hg`) | — | **[src]** — see §9.2. Not the name Unitree documents |
| `rt/lf/mainboardstate` | `unitree_hg::msg::dds_::MainBoardState_` | — | **[web]** — promoted from the unconfirmed WebRTC list; type is already in our venv, cheap to subscribe |
| `rt/odommodestate` | `unitree_go::msg::dds_::SportModeState_` | **500 Hz** | **[live]** — our pose source; rate **[web]** |
| `rt/lf/odommodestate` | `unitree_go::msg::dds_::SportModeState_` | **20 Hz** | **[web]** — identical content, and the one we should be using |
| `rt/state_estimator/odom_pelvis` | `nav_msgs::msg::dds_::Odometry_` | ~51 Hz | **[live]** (bags) |
| `rt/lf/dex3/{left,right}/state` | `unitree_hg::msg::dds_::HandState_` | — | **[src]** — no driver on this robot |
| `rt/brainco/{left,right}/state` | `unitree_go::msg::dds_::MotorStates_` | 100 Hz poll | **[src]** — from `brainco_hand_server`, which was running **[live]**; never subscribed. Only a *right* hand answered (§6.8) |
| `rt/arm/action/state` | JSON `{holding,id,name}`; type unstated | — | **[src]** / type **[?]** |
| `rt/inspire/state` | `unitree_go::msg::dds_::MotorStates_` | — | **[src]** + **[web]** — **12 entries covering BOTH hands, right first**; see `ROBOT-PERIPHERALS.md` §4 |
| `rt/dex3/{left,right}/state` | `unitree_hg::msg::dds_::HandState_` | full rate | **[web]** — the bare name is what both official prose pages use |
| `rt/slam_info` / `rt/slam_key_info` | `std_msgs::msg::dds_::String_` (JSON) | — | **[web]** — motor temps/errors + battery mA/mV/% + CPU; only while `unitree_slam` runs (§9.4) |

**Odometry: the vendor's own topic table gives the wrong type.** It lists both
`rt/odommodestate` and `rt/lf/odommodestate` as `go2 IMUState_`, while the dedicated
`odometer_service_interface` page ships compiling example code declaring
`unitree_go::msg::dds_::SportModeState_` for both. **[web]** **Our bridge is right and the
newer page is wrong** — an `IMUState_` has no position field, so it cannot carry odometry, and
our live reading (position + `imu_state.rpy` populated, 2026-08-11) agrees. The likely origin
of the error is that `SportModeState_` *embeds* an `imu_state` member. Do not let that row
talk anyone into re-typing the pose subscriber; a wrong type silently never delivers.

**Switch our pose subscription to `rt/lf/odommodestate`.** We currently take a 500 Hz
firehose for a pose sampled at a few Hz; the 20 Hz twin carries identical content and matches
the `lf` choices we already made for `lowstate` and `bmsstate`. (Confirm it is actually
published on this unit first — only the bare name has been observed live.) Documented field
semantics, which we should also start using: `position` and `velocity` are base-centre x/y/z
in the **world** frame (m, m/s); Euler angles are body-frame rotations about the world axes
(rad); `yaw_speed` is body-frame yaw rate (rad/s); the quaternion is normalised w,x,y,z. The
world frame is *"established at the ground projection point of the robot's base centre, x
forward, y left, z up, right-handed"*. Requires **State Estimator ≥ 1.0.0.1**. **[web]**

### Command — us to robot

| Topic | Type | Note |
| ----- | ---- | ---- |
| `rt/lowcmd` | `unitree_hg::msg::dds_::LowCmd_` | full-body low level, 500 Hz / 2 ms |
| `rt/arm_sdk` | `unitree_hg::msg::dds_::LowCmd_` | upper body injected into the running controller; weight at `motor_cmd[29].q` |
| `rt/user_lowcmd` | `unitree_hg::msg::dds_::LowCmd_` | only after 7110 |
| `rt/hand_sdk` | `unitree_go::msg::dds_::MotorCmds_` | 4 motors; `Motor_real = w*Hand_SDK + (1-w)*G1_Cmd`. **For the Dex2-5 and Dex1-1, not our hands** — and the weight is an int **0..100 in `cmds[0].mode`** |
| `rt/dex3/{left,right}/cmd` | `unitree_hg::msg::dds_::HandCmd_` | **not** `rt/api/dex3/*/request` — confirmed **[web]** |
| `rt/inspire/cmd` | `unitree_go::msg::dds_::MotorCmds_` | **[web]** — 12 entries, both hands, **right occupies 0–5**; only `q` is honoured; **1.0 = open, 0.0 = closed** |
| `rt/brainco/{left,right}/cmd` | `unitree_go::msg::dds_::MotorCmds_` | 6 entries, q/dq normalised 0..1 |

**`rt/hand_sdk` is scoped hardware, not a generic hand interface.** The vendor names it for
the *"Dex2-5 five-finger 2-DOF hand and the Dex1-1 parallel gripper"* — an `ai_sport`
blending injection, categorically different from the per-hand driver topics (`rt/dex3/*`,
`rt/brainco/*`, `rt/inspire*`) which talk to a serial driver. **[web]** Its preconditions:
`ai_sport` running, robot **not** in damping, a compatible hand installed. So on 2026-08-14
(no controller loaded) it would have been inert — a poor diagnostic and a bad first hand
experiment. Its **weight encoding is a trap**: `weight × 100` as an integer 0..100 stuffed
into `cmds[0].mode`, the other three motors' `mode` unused. That is structurally the same
idea as `rt/arm_sdk`'s `motor_cmd[29].q` weight but a **different encoding in a different
place** — the two are not interchangeable. It also auto-falls-back to `ai_sport` on publish
timeout, which is the **third** instance of the same firmware-side deadman pattern alongside
`SetVelocity`'s `duration` and the Dex3 `RIS_Mode.timeout` bit. Worth naming as a pattern:
**the firmware gives us free safety whenever a command carries its own expiry.** Our sim
profile's `rt/dex1/{left,right}/*` is the Dex1-1 family, i.e. the same hardware `hand_sdk`
names.

**[src]** `LowCmd_` is `uint8 mode_pr; uint8 mode_machine; MotorCmd[35] motor_cmd;
uint32[4] reserve; uint32 crc`, with `MotorCmd_ = {mode, q, dq, tau, kp, kd, reserve}`. CRC is
`crc32_core((uint32_t*)&msg, (sizeof(MsgType)>>2)-1)` computed immediately before send, and
publishers check `mode_machine` against the subscribed `LowState_`'s (0 means simulation and
matches anything). **[src]**

### RPC — `unitree_api::Request_` / `Response_` both directions

`rt/api/{sport,arm,voice,agv,motion_switcher,robot_state}/{request,response}` — see §1.3.

### Sensors, input, audio

| Topic | Type | Rate | Evidence |
| ----- | ---- | ---- | -------- |
| `rt/utlidar/cloud_livox_mid360` | `sensor_msgs::msg::dds_::PointCloud2_` | 10 Hz | **[live]** (bags) — gated on the `lidar_driver` service |
| `rt/utlidar/imu_livox_mid360` | `sensor_msgs::msg::dds_::Imu_` | 200 Hz | **[live]** — type **not** shipped in Python |
| `rt/unitree/slam_mapping/points` / `…/odom` | `PointCloud2_` / `Odometry_` | 10 Hz | **[src]** vendor SLAM output |
| `rt/unitree/slam_relocation/points` / `…/odom` / `…/global_map` | `PointCloud2_` / `Odometry_` / `PointCloud2_` | — | **[web]** — `global_map` is *"only sent once after start relocation"* |
| `rt/frontvideostream` | `unitree_go::msg::dds_::Go2FrontVideoData_` | — | **[src]** — the head `videohub_pc4` binary does create this writer |
| `rt/wirelesscontroller` | `unitree_go::msg::dds_::WirelessController_` | — | **[src]**, Go2 example only. **Absent from all 45 official G1 pages** — treat as Go2-only; use `LowState_.wireless_remote[40]` (§9.5) |
| `rt/audio_msg` | `std_msgs::msg::dds_::String_` (JSON `text` / `play_state`) | — | **[src]** |
| mic PCM | **not DDS** — UDP multicast `239.168.123.161:5555`, 16 kHz mono s16le | — | **[src]** |

Sim-only, Isaac on domain 1: `rt/sim_state`, `rt/sim_state_cmd`, `rt/run_command/cmd`,
`rt/reset_pose/cmd`, `rt/dex1/{left,right}/{state,cmd}`.

**The LiDAR DDS republish is officially confirmed, and it settles the sharing question.**
`lidar_services_interface` (2026-06-04, the newest page in the set): the point cloud is
`rt/utlidar/cloud_livox_mid360` at **10 Hz**, frame `livox_frame`, and the IMU is
`rt/utlidar/imu_livox_mid360` at **200 Hz**, requiring **Lidar Driver ≥ 1.0.0.5**. **[web]**
That is a definitive **yes** to the long-standing question of whether we can consume the
cloud without running a Livox driver: **subscribe the DDS topic and never touch the Livox
config.** The raw UDP path is single-destination by construction — one `host_ip` in
`MID360_config.json`, and writing it steals the feed from whoever holds it — whereas the DDS
republish is an ordinary pub/sub topic with unlimited subscribers. Details, extrinsics and
the post-April-2026 **Mid360s** hardware change are in `ROBOT-PERIPHERALS.md` §1.

**The vendor's navigation is a second motion commander — do not run it.** `slam_operate`'s
1102 pose navigation closes its own velocity loop: its `ctrl_info` broadcast carries
`{"ctrName":"pid","vx":…,"vy":…,"vyaw":…}`, with documented limits of ≤ 10 m per target,
straight-line motion, obstacles ≥ 50 cm tall, indoor maps < 45 m, and *"please do not use the
navigation function on the App at the same time"*. **[web]** Since nothing arbitrates (§1.4),
running it alongside Nav2 or our `walk_to` gives two controllers one set of legs. **Extend
`DEPLOYMENT.md` §2's one-commander invariant and `run_c3po`'s commander check to cover
`unitree_slam`**, alongside the `xr_teleoperate` / `brainco_hand_server` / `rt/arm_sdk` gap
already noted in §13.15. Subscribing to its cloud costs nothing; running its navigation costs
the invariant. The docs also warn that large maps degrade *"basic operation and control
services"* — relevant if we ever suspect resource starvation behind FSM misbehaviour.

### Topic names we inherited from the WebRTC reverse engineering, still unconfirmed

`rt/lf/battery_alarm`, `rt/multiplestate`, `rt/selftest`, `rt/servicestate`, `rt/uwbstate`,
`rt/utlidar/{switch,voxel_map_compressed,lidar_state,robot_pose}`, the `rt/uslam/*` family,
and most `rt/api/*` names beyond those in §1.3. **[web]** Four names from that list have now
been confirmed independently — `rt/lf/bmsstate` (bags), `rt/api/robot_state/request`
(colleague), and **`rt/lf/mainboardstate` + `rt/lf/secondary_imu` (Unitree's own topic
table)** — which raises the list's credibility without establishing any remaining entry.

**The `rt/` ↔ `rt/lf/` pairing is a documented systematic convention**, not ad-hoc naming:
Unitree's table pairs the bare high-rate name with a `rt/lf/` "low-frequency mode" twin for
`lowstate`, `secondary_imu`, `odommodestate` and `dex3/*/state`. **[web]** That supports
reading `rt/sportmodestate` / `rt/lf/sportmodestate` as one such pair rather than as
competing names. ⚠️ **Two known defects in that table**: the `odommodestate` type is wrong
(above), and the Info column is scrambled for the Dex3 rows — the two `rt/dex3/left/state`
entries have their lf/non-lf labels swapped and `rt/dex3/right/state` is described as "left".
Use it for topic **names**; use the per-service pages for types and rates.

### QoS and transport

Publisher-side QoS recorded in the bags for `/lf/lowstate`, `/lf/bmsstate`,
`/state_estimator/odom_pelvis` and both `utlidar` topics: **KEEP_LAST, depth 1, RELIABLE,
VOLATILE**, infinite deadline/lifespan/liveliness. **[live]** Depth 1 is the part that
matters: there is no history to catch up on, so a slow subscriber silently drops samples.
Our reader depth of 10 is legal but buys nothing. **Conflict to be aware of:** the
colleague's prose insists the `utlidar` topics are BEST_EFFORT while the bag metadata they
themselves produced records RELIABLE. Trust the metadata over the prose, but verify.

Transport parameters (see also `ROBOT-INVENTORY.md` §2 and `DEPLOYMENT.md` §4): **domain 0**
on the real robot (1 is Isaac Sim), **interface pinned to `eth0`**, CycloneDDS **0.10.2**,
`<AllowMulticast>false</AllowMulticast>` with a unicast `<Peer address="192.168.123.161"/>`.
**[live]** The vendor pins `eth0` in its own module config using the same 0.10.2 schema —
independent confirmation that our `DDS_INTERFACE` decision matches what Unitree does on this
box. **[live]** Domain 0 and the pinned interface are corroborated by every routine in the
official docs (`Init(0, argv[1])`, "eth0: the network card with network segment 123"), and
the 0.10.2 pin is stated outright: *"The cyclonedds version of Unitree robot is 0.10.2."*
**[web]**

⚠️ **Our multicast setting is an undocumented divergence, and onboard it may be hiding
publishers.** No official page mentions `AllowMulticast`, `NetworkInterface` or a `<Peer>`
element anywhere in 45 pages; the vendor's own config is **plain multicast on a named
interface**, and `videohub_pc4`'s on-robot `cyclonedds.xml` is exactly that. **[web]** +
**[src]** Disabling multicast and listing a **single** peer means discovery reaches exactly
one participant address (`192.168.123.161`). **Anything publishing from another address on
192.168.123.x — the NX itself, `unitree_slam` if it runs on PC2, a colleague's node — is
invisible to us, with no error.** The comment in `connection.py` justifies the unicast peer
by macOS multicast unreliability; **that rationale does not apply onboard the Jetson.** Make
the setting conditional on `SIM_MODE`: vendor-style multicast on the real robot, the unicast
workaround for the Mac/Isaac path only. At minimum add `127.0.0.1` so same-host participants
are discoverable. (The exact vendor `CYCLONEDDS_URI` XML body was stripped by the docs'
HTML→markdown conversion and is not recoverable from the corpus — see open questions.)

---

## 11. SOLVED: `Start()` / `fsm_id = 500` — it was the wrong walk program

**Resolved 2026-08-15. The robot walked.** **[live]**

The answer is one number. **500 and 501 are two different walk programs, chosen
by how many degrees of freedom the waist has** — not a generic "start" and a
variant of it. `mode_machine` reports the body: `4 = 23-DoF, 5 = 29-DoF,
6 = 27-DoF`. This robot has reported **5** in every `get_state` we have ever
taken, going back to the first session. 501 is that variant's program. We had
only ever sent 500, which belongs to the other body.

The working sequence, executed on the gantry with feet loaded: **[live]**

```
damp  ->  prepare (4)  ->  start_walking_waist (501)  ->  walk_to
 fsm 1      fsm 4            fsm 501, walk_waist         0.17 m travelled
```

Everything else we chased was a wrong turn: debug mode, weight-bearing,
`BalanceStand`, motion authority, and the remote. Each was plausible and each
was wrong. The evidence that mattered was sitting in every state read we ever
took.

### 11.0 What this cost, and the reasoning error worth keeping

Two sessions. The error was not any single wrong hypothesis — it was treating
`rpc code 0` as evidence. We inferred "500 returns 0, therefore 500 is
recognised, therefore the failure is at the transition", and built a ranked
candidate list on top of it.

**That inference was false.** Probed 2026-08-15: `SetFsmId(99999)` — an id that
cannot exist — also returns **code 0**. **[live]** The sport service does not
validate FSM ids at all. Every conclusion resting on "code 0 means recognised"
was unsupported, including the one that made 501 the favourite (501 turned out
to be right, but not for the reason we believed).

The cheap probe that killed it took one call and no motion. It should have been
the first thing we ran, not the twentieth.


### 11.1 Two different situations, one surface symptom

The most important thing to understand before reading anything else about this: **there are
two separate observations a day apart, and only the second one is explained.**

| | **2026-08-13** | **2026-08-14** |
| --- | --- | --- |
| Controller loaded? | **Yes** — getters answered, `Damp` and `StandUp` physically executed | **No** — `CheckMode` returned `{'form':'0','name':''}` **[live]** |
| `SetFsmId(500)` | `code 0`, `fsm_id` stayed 4 | (not retried) |
| 7001 / 7002 | answered normally | **returned nothing at all** |
| `get_state` | `fsm_id=4`, `posture=preparation` | `fsm_id=None`, `posture=unknown` |
| Explained? | **No** | **Yes** — no controller is loaded, so nothing executes an FSM transition |

Anyone re-reading these logs will be tempted to collapse them into one story ("the robot was
in debug mode the whole time"). **Do not** — and Unitree's own documentation now rules that
story out for 2026-08-13 on its own terms. On that day a controller *was* loaded and doing
work (`SetFsmId(4)` lifted the robot from odom z 0.04 to ~1.00 m), and **a robot in debug mode
cannot execute `StandUp` at all**, because debug mode means the built-in operation control has
*"completely exited"*. Separately, the high-level RPC path we use *"has no need to enter the
debug mode"* — it needs debug mode to be **off**. **[web]** So debug mode is the **confirmed**
explanation for 2026-08-14 and a **ruled-out** one for 2026-08-13. See §4.6.

`CheckMode` was still **not** run on 2026-08-13, so we do not know *which* controller was
loaded. **Running `CheckMode` at the top of the next window is the single most important
step**, because it is the only thing that distinguishes the two situations retrospectively.

### 11.2 Ruled out on 2026-08-13

- **Feet unloaded on the gantry** — retested with the robot weight-bearing. **[live]**
- **Missing `BalanceStand`** — `SetBalanceMode(0)` sent, `code 0`, no effect on `Start()`.
  **[live]** The docs now agree it should not have mattered: no page requires `BalanceStand`
  or `SetStandHeight` before `Start()`. **[web]**
- **Malformed request** — our wire bytes are byte-for-byte what the vendor sends:
  `7101 {"data":500}` against `js["data"] = fsm_id; req.parameter = js.dump();`. Not a
  float-vs-int, not a wrong key, not a wrong service. **[src]**
- **SDK-generation skew (`Start()` used to be 200)** — both SDK generations on this machine
  are post-2025-06 and both say `Start() = SetFsmId(500)`. **[src]**
- **Debug mode** — newly ruled out, see §11.1 above. **[web]**

**Demoted from "ruled out" to *inconclusive*: "the operator's handheld remote also failed."**
That was our evidence that the failure is not SDK-specific. It is now much weaker, for two
independent reasons, both from Unitree's own `remote_control` page: **[web]**

1. **The operator may have pressed the wrong combination.** R1+X is Main Operation Control for
   a **1-DoF waist**; a 3-DoF-waist machine needs **R1+Y**. If R1+X was pressed, the remote
   failing is the *same* root cause as the SDK failing — which corroborates Rank 1 rather than
   ruling it out.
2. **They may have tapped instead of held.** *"When in the standing position, certain button
   combinations need to be `held for two seconds` to take effect."*

Until we know which combination was pressed and for how long, that observation carries no
weight against any candidate. `LowState_.wireless_remote[40]` (§9.5) turns it back into a
measurement.

### 11.3 Ranked candidates

**Nothing here is solved. We have no hardware access, and every rank below is a hypothesis
with a named experiment.** What changed with the official documentation is the *evidence
grade*: Rank 1 moved from one third-party site to Unitree's own text, and **two entirely new
candidates appeared** that this section did not previously contain.

---

**Rank 1 — 500 is the wrong walk program for this chassis; this machine's walk is 501.**
*Grade: vendor-documented mechanism, untested on this robot.*

Previously this rested on a two-family model sourced from **one** third-party site (two
firmware revisions of it), which a general search could not corroborate. It is now **Unitree's
own text**, from three pages: **[web]**

- `quick_start` (2025-11-12), verbatim: *"After G1 is straightened and standing, you can press
  **R1 + X (1 degree of freedom waist)** or **R1 + Y (3 degrees of freedom waist)** to enter
  the operation control state."*
- `sport_services_interface` (2026-07-13) lists **500 "Walk Motion"** and **501 "Walk
  Motion-3Dof-waist"** as two separate FSM entries.
- `remote_control` (2026-06-25) labels the combos ⑦ *R1 + X (Main Operation Control)* and ⑧
  *R1 + Y (**Only Used For 3-DOF Waist Structure**)*, adding *"recommended to use R1 + Y"*.

The chain closes: `Start()` == "main operation control" == remote ⑦ R1+X == 1-DoF waist ==
FSM **500**. The 3-DoF equivalent is ⑧ R1+Y == FSM **501**, and **no Unitree source provides
an SDK convenience method for 501** — only `SetFsmId(int)`. Under this model 500 is a valid
enum member (so the lookup passes and you get `code 0`, not `7302`) naming a policy not built
for this chassis (so no transition occurs). **That is exactly the observed signature.**

**And this is a 29-DoF / 3-DoF-waist machine on the firmware's own account.** Previously we
had only four *configuration* artifacts from two teams, none of them a hardware read
(§13.4) — `g1pilot`'s URDF declaring `waist_yaw/roll/pitch_joint`; `xr_teleoperate` launched
`--arm=G1_29` whose enum has real `kWaistRoll`/`kWaistPitch` where `G1_23` names those slots
`NotUsed`; the `g1_body29_hand14.urdf` asset. **[src]** Now add: **`mode_machine = 5`, which
Unitree documents as `5 = 29-Dof`**, with `6` reserved for a waist-fastened 27-DoF build
(§4.2). **[live]** + **[web]** That is a firmware self-report, not a file someone chose. Plus
the live observation that **this robot has already reached `fsm_id = 802`** — which the docs
now identify as Run on a 29-DoF `ai_sport` ≥ 8.6.x, i.e. the 3-DoF branch (§4.1). **[live]** +
**[web]**

**The fix is already in our own code**: `g1_protocol.Mode.WALK_WAIST = 501` exists and
`_PREPARATION_TARGETS` already lists 501 as legal from state 4.
`SKILL_REQUESTS["start_walking"]` is simply wired to `Mode.WALK` (500). Make it
chassis-conditional — try 501 first on this machine, keep 500 as the fallback for a 23-DoF /
1-DoF-waist unit. **[src]**

> **Experiment:** at `fsm_id == 4`, having read `fsm_mode == 0` immediately beforehand, send
> `7101 {"data": 501}` and poll 7001 every 250 ms for 3 s. Then, only if that does nothing,
> have the operator **hold R1+Y for two seconds** (not R1+X).

---

**Rank 2 — the waist configuration or calibration disagrees with what we assume.**
*Grade: NEW. A documented G1-29 failure mode, checkable without SSH or DDS.*

This candidate did not exist in the previous version of this section, and it explains **both**
the SDK and the remote failing.

The `waist_fastener` page describes a physical clamp for the two waist parallel motors (idx
13/14), paired with a **"waist motor lock" switch in the Unitree Explore APP**
(【Settings】→【Robot】, requiring a restart). **That switch is the firmware's declaration of
this machine's waist DoF** — independent of any URDF the two teams configured. The FAQ then
documents the failure directly: **[web]**

> *"G1-29 DOF device, after unlocking the waist fixator (APP synchronously closes the waist
> lock switch), report the joint out-of-limit position error. **Reason: The two joint motors
> at the waist are not calibrated.**"*

> *"After the G1 device is upgraded with the latest firmware **≥ 1.3.0** … The new firmware
> optimizes the joint calibration accuracy. The previous calibration accuracy does not meet
> the requirements and needs to be **re-calibrated**."*

Ours is firmware **1.5.3.8**. An uncalibrated or mis-declared waist is a clean reason a 3-DoF
walk policy would refuse to load, and it would refuse identically from the SDK or the remote.
Note the fastener cannot *mechanically* block walking — it touches no leg joint — so the risk
is the **declaration and the calibration**, not the clamp. The page adds a related trap: a
robot ever calibrated in the 27-joint (locked) configuration needs a fresh 29-joint
calibration after unlocking, and nothing in our records says which this unit had.

> **Experiments, all free:** (a) have the operator open the Unitree Explore APP and read the
> **waist motor lock switch** — the fastest way to learn which of 500/501 the firmware will
> accept, needing neither SSH nor DDS; (b) photograph the lower back for a physically fitted
> fastener while `mode_machine` reads 5 — the two disagreeing would be the smoking gun;
> (c) decode `motor_state[13]/[14].motorstate` for `0x2000` calibration/BOOT data error or
> `0x20000` calibration mode warning (§9.6), zero new subscriptions.
> **Add "APP waist-lock switch state" to the pre-window checklist.**

---

**Rank 3 — `fsm_mode` was 1 (Dynamic) at the moment of the send.**
*Grade: documented gate, never measured. The cheapest untested explanation we have.*

*"0: Static, allows switching to other modes / 1: Dynamic, switching to most modes is not
allowed … When the robot's current state/posture is unsuitable for mode switching, we prohibit
the robot from changing modes."* **[web]** (§4.2.)

**We have never read 7002 immediately before sending 7101.** A silent gate that answers
`code 0` and refuses the transition is precisely our signature, and one extra read per attempt
eliminates it.

> **Experiment:** read 7002 in the same breath as every 7101 and log both. Make the bridge
> refuse-and-report rather than send blind when `fsm_mode != 0`. Bonus: if it only ever
> returns 0 or 1, the third-hand "`fsm_mode == 2` = feet unloaded" claim dies with it.

---

**Rank 4 — a latched error or soft e-stop.**
*Grade: NEW. The only candidate that explains SDK **and** remote with no extra assumption.*

Unitree documents `0x1000` **"Soft emergency stop switch is pressed"** in the total-device
status word (§9.6), and a **solid red "Error State"** on the LED strip (§4.5). **[web]** Either
would stop everything, from every source, while the robot otherwise looks healthy — and we
have no diagnostic that surfaces a firmware-level error state at all.

> **Experiments, both nearly free:** photograph the LED strip at the top of the window — solid
> red or solid yellow answers a great deal before any RPC is sent — and decode the device
> status word once we know its carrier (candidates: `unitree_go SportModeState_.error_code` on
> `rt/odommodestate`, which we already receive and ignore, or `MainBoardState_` on
> `rt/lf/mainboardstate`).

---

**Rank 5 — a low-power state.**
*Grade: NEW, completely untested, one pure read.*

`robot_state` **1005 `LOWPOWER_STATUS`** exists in the b2 client installed in our venv (§8) and
has never been called. A robot in a low-power state accepting `SetFsmId(500)` with `code 0` and
not moving is our signature exactly, and like Rank 4 it would also explain the remote failing.

Related, and a downgrade: **no official page states a battery or SOC threshold as a
precondition for any FSM id.** **[web]** The old "battery or thermal" candidate is therefore
*unsupported speculation* rather than a documented gate — while remaining trivially cheap to
check, since `faults: none, battery: null` is an artifact of our own code and not a health
reading (§9.4).

> **Experiment:** call 1005; subscribe `rt/lf/bmsstate` and log `soc`, `soh`,
> `temperature[12]`, `bmsstate[5]`. Expect `soc` comfortably above ~30.

---

**Rank 6 — motion_switcher authority: the wrong controller, or none.**

The 2026-08-14 empty-name reading proves the robot *can* be in a no-controller state, and that
the co-tenant stack puts it there by default (§3.3). Against this candidate for the 2026-08-13
observation: `Damp` and `StandUp` physically executed, which a released mode cannot permit
(§11.1). It survives only in the weaker form "the active mode was `normal` / `sport_mode`
rather than `ai` / `ai_sport`". **[src]**

> **Experiment:** `CheckMode` — **first thing, before anything is touched, every window.**
> Expect `{"form":"0","name":"ai"}`. Anything else promotes this to Rank 1 and the fix becomes
> `SelectMode("ai")` rather than any FSM id we guess. Be ready for `7004 Unsupport mode name`.

---

**Rank 7 — an unmet precondition around stand height.** *Grade: weakened by the docs.*

`g1pilot` ramps `SetStandHeight` in 0.02 m steps until `get_fsm_mode() == 0` **and** height
≥ 0.2, then calls `BalanceStand(1)` — i.e. `SetBalanceMode(1)`, continuous gait — then
`SetStandHeight`, then `Start()`. We sent `SetBalanceMode(0)` and never ramped. **[src]** But
**no Unitree page requires either call before `Start()`** **[web]**, so that ramp is one team's
practice, not a documented precondition. Its sibling `test.py` still early-outs on
`cur_id == 200`, i.e. it is a copy of the pre-2025-06 community script and **not** independent
corroboration of the `fsm_mode == 2` claim, which no vendor source documents.

---

**Rank 8 — the remote never reached the robot.**

`LowState_.wireless_remote[40]` (§9.5) turns the anecdote into a measurement, from a topic we
already subscribe to and a field we already discard. Weaker now as a *robot-side* explanation
and stronger as an *operator-procedure* one (§11.2).

> **Experiment:** decode it while the operator presses keys. Expect `head == {0xFE, 0xEF}` and
> a changing `btn`; confirm **which** combination was pressed and that it was held ≥ 2 s.

### 11.3a What would move this forward without a robot

Three actions, none of which needs hardware, a session, or SSH:

1. **Transcribe the mode-switch diagram** at `https://oss-global-cdn.unitree.com/static/
   98431a05f8e747709722e901d32d8ce3_11798x7046.jpg`. It is the *only* authoritative statement
   of the legal FSM transition graph in existence, and it is what every `SetFsmId` / `Start` /
   `Damp` remark in Unitree's docs points at (§4.3). It should say outright whether 4 → 500 and
   4 → 501 are legal edges and what gates them.
2. **Obtain the 29-DoF remote sticker PDF for Motion Control Version > 8.6.0.0**, linked at the
   top of `remote_control`. It may name the FSM ids behind ⑦ and ⑧ directly.
3. **Ask the operator to read the Explore APP's waist-lock switch** (Rank 2) — phone only, no
   robot session required.

### 11.4 The calibration step that everything else depends on

Send `7101 {"data": 99999}` and expect **`7302 Invalid fsm id`**.

This is the load-bearing zero-motion check. It proves the firmware *rejects* unknown ids —
which is what licenses the inference that `code 0` on 500 means 500 **is** in the enum and
the failure is at the transition, not the lookup. Every candidate above is built on that
inference and **it has never been tested**. If 99999 also returns 0, the entire reading of
the evidence changes and the right move is to stop and re-plan.

**The zero-motion read set to run in the same breath**, now larger than it was and worth
scripting once so nobody improvises it in a live window:

| Call | Why |
| ---- | --- |
| `motion_switcher` **1001 CheckMode** | **First, always.** Distinguishes "wrong FSM id" from "no controller loaded" (§3.1) |
| `sport` **7001 / 7002** | fsm_id **and** fsm_mode, read together, before and after every write (§4.2) |
| `robot_state` **1003 ServiceList** | Which services exist, run and are protected on *this* firmware; expected names in §8 |
| `robot_state` **1005 LowPowerStatus** | §11.3 Rank 5, never called |
| `robot_state` **1006 GetPkgVersion** | The only route to control-board module versions (§8) |
| `arm` **7107 GetActionList** | This firmware's real catalogue **plus per-action FsmID requirements** — settles §6.5 (§6.3) |
| `voice` **1005 GetVolume** | Is the audio service alive and ≥ 2.0.3.8, and is audio FSM-gated (§7.4) |
| `sport` **7101 {"data": 99999}** | The 7302 calibration above — the one write in the set |
| *(passive)* `rt/sportmodestate` | Push FSM observation with zero writes (§9.2) |

`7008 GET_AVAILABLE_FSM_IDS` — which would settle the whole 500/501/801/802 argument in one
call — is declared for **H2**, not G1, and is absent from this robot's G1 header. Worth one
probe (a `3203` answers cleanly at zero risk) but not expected to exist. **[src]**

### 11.5 One thing this is *not*

`master_service.service` was found **stopped** (deliberately, at 01:40:34 CST on
2026-08-14, to free the head camera — documented in `xr_teleoperate`'s README). **[live]** It
is tempting to connect that to the FSM. Don't: `strings` on the binary shows it contains no
FSM, `ai_sport`, `motion_switcher` or `loco` code whatsoever, and it supervises only
`ota_pipe` and the two video-hub nodes. **[live]** Restarting it changes camera behaviour and
nothing else. (It does also run `amixer set Speaker 75%` at boot, so while it is dead the
Jetson's speaker volume is unset — relevant to audio, not to motion.)

---

## 11.9 Zero-write recon, 2026-08-15

Run while the robot stood in 501. Getters and passive subscribes only — no
setter, velocity, FSM change, arm action or mode switch. **[live]**

| Probe | Result | What it means |
| --- | --- | --- |
| `7001` GET_FSM_ID | `0`, `{"data":501}` | works |
| `7002` GET_FSM_MODE | `0`, `{"data":0}` | works. Read **1** once, at fsm 4 — first time we have seen a non-zero sub-mode |
| `7003` GET_BALANCE_MODE | **`7301`** | "loco state not available" — *even in 501 with a controller loaded* |
| `7004` GET_SWING_HEIGHT | **`7301`** | same |
| `7005` GET_STAND_HEIGHT | **`7301`** | same |
| `7006` GET_PHASE | **`7301`** | deprecated, and unavailable |
| `7008` GET_AVAILABLE_FSM_IDS | **`3203`** | **not implemented on this build.** No authoritative FSM table from the robot |
| voice `1005` GET_VOLUME | `0`, `{"volume":100}` | volume is at maximum — why the TTS was clearly audible |

Two consequences worth carrying:

- **The 7301 cluster is a trap.** Those four getters return a plausible body
  (`{"data":0}`, `{"data":0.0}`) *alongside* the error code. Read the code, not
  the payload, or you will record a stand height of 0.0 m as fact.
- **`fsm_id` 550 remains unexplained.** It was read once, on 2026-08-15, and
  appears in no table we have. `7008` was the call that would have settled it
  and this firmware does not implement it. Still open.

### Hands — still unresolved, but the balance has shifted

Subscribed for 6 s to `rt/lf/dex3/{left,right}/state` and
`rt/dex3/{left,right}/state`. **Nothing delivered on any of them.** **[live]**
No hand driver was running at the time, and the FTDI FT4232H is present with
`/dev/ttyUSB0-3` — so the RS485 bus exists, unused.

That is evidence toward BrainCo rather than Dex3, but it is not proof: BrainCo
publishes `MotorStates_` on `rt/brainco/right/state`, a type this SDK does not
ship, so we cannot subscribe to it to confirm. **Settled by looking at the
wrists** — three fingers means Dex3, five means BrainCo Revo2.

### Thermals and load, first readings

`imu_state.temperature` **78 °C**; hottest motor **47 °C**; `mode_pr` 0.
Battery draw is state-dependent and material: **~2.1 A in damp, ~2.9 A while
balancing in 501**. At 49% and ~2.9 A, a standing robot is not a free thing to
leave running.

### The interlock has another gap

`gemm-bringup` was found running again alongside our bridge on domain 0 —
`gemm_robot_server`, `gemm_lidar_live_relay`, `realsense2_camera_node`,
`foxglove_bridge`. `run_c3po` had stopped it earlier in the same session.

Checked before leaving the robot standing: `gemm_robot_server` contains **no**
reference to `SetFsmId`, `SetVelocity`, `LocoClient`, `cmd_vel`, `7101` or
`7105`, and `cmd_vel_to_loco` is not running. So nothing in the returned stack
can command the legs. But `warn_if_other_commander` does not watch
`gemm_robot_server` either, and the container coming back on its own — after an
explicit `docker stop` — is worth understanding before it happens during motion.

## 12. Corrections this document makes to our own repo

Collected so they are actionable rather than scattered.

| Where | Claim | Correction |
| ----- | ----- | ---------- |
| `ROBOT-INVENTORY.md` §3 | `7110 SWITCH_TO_USER_CTRL` listed as part of the loco surface | Absent from the firmware-matched header; exists only in a newer SDK clone, unproven on 1.5.3.8 (§2.4) |
| `ROBOT-INVENTORY.md` §4, `MENTAL-MODEL.md` | `/api/dex3_msg_controller` | Appears in no vendor source, binary or config on this robot. Unsourced — strike it (§1.3) |
| `ROBOT-INVENTORY.md` §4 | Dex3-1 hands fitted, stated as fact | **Unresolved.** One BrainCo Revo2 right hand answers on `/dev/ttyUSB1` and no Dex3 driver exists on the Jetson — but nothing rules out a Dex3 being physically fitted. Mark it `[?]` until someone looks at the wrists (§6.8, `ROBOT-PERIPHERALS.md` §4) |
| `MENTAL-MODEL.md` ~180 | `rt/state_estimator/*` would need hand-written IDL | `nav_msgs Odometry_` is already in our venv (§9.2) |
| `SPEC.md` §17.5 | Dex3 command topic `rt/api/dex3/*/request`, state type `MotorStates_` | `rt/dex3/*/cmd`, type `HandState_`; not an RPC service (§6.8) |
| `g1_protocol.REAL_TOPICS` | `dex_left_cmd`/`dex_right_cmd` as `rt/api/dex3/*/request` | Same as above |
| `g1_protocol.Gesture` | `FORWARD_PUSH = 36` (used by `point_at`) | No backing in any vendor artifact on this machine; 11 and 13 are missing from our enum (§6.3) |
| `g1_protocol.is_locomotion_state` | Encodes the E-grade gesture gate | Defined but never called; the polarity itself is disputed (§6.5) |
| `state.py` | `battery_pct: None`, `faults` only checks lowstate staleness | `rt/lf/bmsstate` → `BmsState_.soc`, type already shipped (§9.4) |
| `mcp_server.py` | Reports 7404 as `FSM_UNAVAILABLE` | That label is ours; the vendor string is "Invalid fsm id" (§6.4) |
| **`g1_protocol.REAL_TOPICS`** | `sportmodestate` as `rt/lf/sportmodestate` | Unitree documents the bare **`rt/sportmodestate`** for firmware ≥ 1.5.1 (§9.2) |
| **`g1_protocol.REAL_TOPICS`** | `odom` as `rt/odommodestate` (500 Hz) | The 20 Hz twin `rt/lf/odommodestate` carries identical content (§10) |
| **`g1_protocol`** | 802 "labelled run, marked suspect"; 801 sendable | 802 **is** Run, renumbered for 29-DoF `ai_sport` ≥ 8.6.x. Stop sending 801 on this chassis (§4.1) |
| **`SKILL_REQUESTS["start_walking"]`** | Wired to `Mode.WALK` (500) | Make chassis-conditional: **501 first** on this machine, 500 as the 1-DoF fallback (§11.3) |
| **`g1_protocol.Gesture`** | Labels `HANDS_UP`, `REFUSE`, `SINGLE_HAND_UP`, `ULTRAMAN_RAY`, `LOW_WAVE` | Official names imply different poses — Arms Horizontal, Double Hand Cross, Right Hand Horizontal, Dynamic Light Wave, Wave Hand in Front Chest. **The LLM picks by label** (§6.3) |
| **`g1_rpc` / arm 7107** | `SET_SPEED_MODE` takes `{"data": <int>}`, no range | Documented **0/1/2/3** = 1.0/2.0/2.7/3.0 m/s, scoped to *running* (§2.1) |
| **`state.py::_on_lowstate`** | Discards `motor_state[i].motorstate` | Vendor bit table now available — ship a decoder (§9.6) |
| **`state.py::_on_odom`** | Reads only `position` and `rpy[2]` | `velocity[3]` and `yaw_speed` are populated and free (§5.3) |
| **`faults.py`** | Web-grade fault catalogue, unwired | Replace with Unitree's motor/device bit tables (§9.6) |
| **`connection.py`** | `AllowMulticast=false` + single `<Peer>` unconditionally | That workaround is for the Mac; onboard it can hide publishers. Make it `SIM_MODE`-conditional (§10) |
| **`ROBOT-API.md` §3.5 / `ROBOT-PERIPHERALS.md` §5.5** | Venv pin `a7dff75`, "no `g1` package at all", robot_state api `1.0.0.1` | Pin is now **`65691c8`**; `g1`, `b2` and the rest are present; use the **b2** `RobotStateClient` at api **`1.0.0.2`** (§8) **[live]** |
| **`ROBOT-API.md` §1.3** | `slam_operate` listed as unsourced | Vendor-documented; "not enabled or not installed on this unit" (§1.3) |
| **`ROBOT-API.md` §4.2** | `mode_pr` selects the "ankle/wrist" convention | Ankle **and waist** (§4.2) |
| **`ROBOT-API.md` §6.7** | `rt/arm_sdk` indices 15–28 | **12–28** — index 12 is waist yaw (§6.7) |
| **`ROBOT-API.md` §6.8** | Dex3 "9 pressure pads" | `PressSensorState_` carries `pressure[12]`; pad *count* is contested 6 vs 9 (`ROBOT-PERIPHERALS.md` §4.4) |
| **`DEPLOYMENT.md` §2 / `run_c3po`** | One-commander check greps `cmd_vel_to_loco` only | Must also cover `xr_teleoperate`, `brainco_hand_server`, **`unitree_slam`** and any publisher on `rt/arm_sdk` / `rt/lowcmd` (§10, §13.15) |

---

## 13. Open questions

Ordered by how much each unblocks.

### Answerable with no robot at all

These moved to the top because the official documentation made them possible, and because
every one of them is currently blocking a hardware question we cannot otherwise reach.

1. **Transcribe the mode-switch diagram** —
   `https://oss-global-cdn.unitree.com/static/98431a05f8e747709722e901d32d8ce3_11798x7046.jpg`.
   The only authoritative statement of the legal FSM transition graph in existence, and the
   thing every `SetFsmId`/`Start`/`Damp` remark points at (§4.3, §11.3a).
2. **Get the 29-DoF remote sticker PDF for Motion Control Version > 8.6.0.0**, linked at the
   top of `remote_control`. It may name the FSM ids behind ⑦ R1+X and ⑧ R1+Y directly.
3. **Ask the operator to read the Unitree Explore APP's waist motor lock switch**, and whether
   a ≥ 1.3.0-era calibration was ever done on this unit. Phone only — no SSH, no DDS. It
   decides which of 500/501 the firmware will accept (§11.3 Rank 2).
4. **Get the vendor's `CYCLONEDDS_URI` XML body.** The docs' HTML→markdown conversion stripped
   the element contents, and no page in the corpus contains it. It would tell us what we are
   diverging from in `connection.py` (§10). The robot's own
   `/unitree/module/video_hub_pc4/cyclonedds.xml` is the fallback source.

### Zero-motion reads, next window

5. **Which controller was loaded on 2026-08-13?** `motion_switcher` 1001 `CheckMode`, at the
   top of the window, before anything else is touched, every time (§3.1, §11.1).
6. **Does `7101 {"data": 99999}` return 7302?** The inference the whole §11 analysis rests on.
   One call (§11.4).
7. **What does `arm` 7107 `GET_ACTION_LIST` return?** Promoted: per Unitree it contains the
   real catalogue, **the per-action FsmID requirements**, and taught-action **durations** — so
   one read settles the 7404 polarity dispute, `FORWARD_PUSH = 36`, the id-13 disagreement and
   our arm timeout sizing (§6.3, §6.5).
8. **What does `robot_state` 1003 `ServiceList` return?** Now with expected names to check
   against: `ai_sport`, `basic_service`, `g1_arm_example`, `vui_service`, `unitree_slam`,
   `lidar_driver` (§8). Also settles whether `slam_operate` is servable here (§1.3).
9. **`robot_state` 1005 `LowPowerStatus` and 1006 `GetPkgVersion`** — never called, both pure
   reads. 1005 is §11.3 Rank 5; 1006 is the only route to control-board module versions,
   including the `vui_service ≥ 2.0.3.8` floor (§8, §7).
10. **Does `voice` 1005 `GET_VOLUME` answer in every FSM state**, including the empty-name
    debug state? If yes, `say()` becomes the bridge's universal acknowledgement channel for
    refused motion — a real product decision, one read per state (§7.4).
11. **Does `rt/sportmodestate` publish here?** The name and type are now vendor-confirmed for
    firmware ≥ 1.5.1 (ours is 1.5.3.8); write the 20-line IDL and subscribe. Zero writes, and
    it gives push FSM observation plus `task_time` gesture progress (§9.2).
12. **Is `rt/wirelesscontroller` published on the G1 at all?** Zero hits in 45 official pages
    while the `LowState_` path is documented twice — a passive `DCPSPublication` scan settles
    it in the same pass as Q13 (§9.5).
13. **Is `rt/lowstate` (500 Hz) published, and what types really sit on
    `rt/lf/sportmodestate`?** One passive `DCPSPublication` read produces the definitive live
    census — a probe script was left on the robot at `/tmp/c3po_audio_probe.py` and never run.
14. **Do the two waist motors report live?** Read `motor_state[13]`/`[14]` off
    `rt/lf/lowstate`: a real 3-DoF waist reports live q/temperature/vol there, and
    `motorstate` would show `0x2000`/`0x20000` if the waist is uncalibrated (§9.6, §11.3
    Rank 2). This is the *hardware* check that `mode_machine = 5` only self-reports.

### Needs a supervised motion window

15. **Does `7101 {"data": 501}` transition this robot?** Rank 1. If yes, the fix is one enum
    member (§11.3).
16. **Does an `rt/arm_sdk` stream move the arms at `fsm_id = 4`?** The docs say Locked Stance
    is a supported state for that path — so this is a route to gestures that **does not
    require solving the 500/501 blocker first** (§6.7). Switch `g1_arm_example` off first.
17. **What is the vx/vy/omega sign convention, and the real clamp bound?** Still undocumented
    after 45 pages. Measure it, logging `rt/lf/odommodestate`'s `velocity`/`yaw_speed` against
    what we commanded (§5.3).
18. **Do our gesture labels match the physical poses?** Five official names imply different
    motions than our enum, and the LLM selects by label (§6.3). Watch each once and rename.
19. **Does the arm service still work while something else holds `rt/arm_sdk`, and is 7400
    "occupied" or "busy"?** Two back-to-back gestures with nothing else running discriminates
    (§6.4). Widen `run_c3po`'s commander check regardless (§12).
20. **Which button combinations does the firmware intercept?** Unresolved by the docs — they
    map combos to ①…⑧ symbols and never to FSM ids. Log `wireless_remote` while the operator
    presses each combo (**held ≥ 2 s**) and watch `fsm_id` with no RPC from us (§9.5).
21. **Are 7110/7111 and arm 7108/7113 served by firmware 1.5.3.8?** `3203` vs `3204` vs `0`
    discriminates; each is a write. Low priority — 7110 is not a route to walking (§2.4).

### Settle with one decoded message

22. **Battery decoding:** `current` in mA or 10 mA, `bmsvoltage[3]` in mV, how many
    `cell_vol`/`temperature` entries this pack populates, what the five `bmsstate` words mean.
    **Confirmed unanswerable by reading** — Unitree documents `BmsState_`'s fields nowhere in
    45 pages (§9.4). One live message.
23. **Which DDS field carries the total-device status word** whose `0x1000` is "soft emergency
    stop switch is pressed"? Candidates: `unitree_go SportModeState_.error_code` on
    `rt/odommodestate` (already received and ignored), or `MainBoardState_` on
    `rt/lf/mainboardstate` (§9.6). This subsumes the old "does the vendor fault stream have a
    DDS source" question — `faults.py`'s web-grade catalogue should be replaced by the vendor
    bit tables either way.
24. **Does `rt/arm/action/state` exist, and what type?** The clean fix for the false-timeout
    problem, and it would let us handle 7401 (§6.6).
25. **Does `rt/audio_msg` carry `play_state` on this firmware, and does it fire for *our*
    `PlayStream` or only the assistant's playback?** If ours, `say()` gets true completion
    detection instead of a guessed duration model (§7.1).
26. **Is the raw mic multicast at 239.168.123.161:5555 gated on wake-up mode**, the way ASR
    output on `rt/audio_msg` explicitly is? Decides whether a future `listen()` has a human
    prerequisite we cannot satisfy over DDS (§7.2).

### Answered by the official documentation — kept only as a record

- ~~Is this a 3-DoF-waist machine?~~ `mode_machine = 5` = 29-DoF per Unitree's own comment
  (§4.2). Still a firmware self-report, not a hardware read — Q14 remains the hardware check.
- ~~Why is 802 not in {500, 501, 801}?~~ 802 **is** 801, renumbered on 29-DoF `ai_sport`
  ≥ 8.6.x (§4.1).
- ~~Which FSM topic name and type?~~ `rt/sportmodestate`, `unitree_hg SportModeState_`,
  firmware ≥ 1.5.1 (§9.2). Whether it publishes here is Q11.
- ~~Is there a velocity limit?~~ Yes, a firmware clamp, plus a documented speed ladder for
  running. The bound is still unpublished (§5.3).
- ~~Does audio queue or interrupt?~~ For `PlayStream`, `stream_id` decides (§7). For
  `TtsMaker`, still undocumented.
- ~~Is the `voice` volume range 0–255?~~ 0–100 (§7).
- ~~Was the robot in debug mode on 2026-08-13?~~ It cannot have been — a robot in debug mode
  cannot execute `StandUp` (§11.1).
