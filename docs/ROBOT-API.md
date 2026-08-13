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
- **[web]** — published documentation, i.e. `G1-WEB-RESEARCH.md`, which is explicitly
  unverified. A `[web]` claim here is a hypothesis, never a fact.
- **[?]** — believed, not verified. Do not build safety-critical logic on these.

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
grep over `/unitree`, both vendor trees and both SDKs returns zero hits: `slam_operate`,
`action_store`, `/api/gesture`, `/api/gpt`, `/api/vla`, `/api/audiohub`,
`/api/dex3_msg_controller`. **[live]** The last one is cited in `ROBOT-INVENTORY.md` §4 and
`MENTAL-MODEL.md`; its only occurrences anywhere are our own docs. **Treat it as unsourced
and strike it.** The one survivor of that word list is `slam_nav`, and only as a key in
`/unitree/etc/master_service/protect` (`{"slam_nav": 0}`) — a service name in a supervisor
config with no code behind it on this host. **[live]**

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
| 3201 | Send response error        | server     | |
| 3202 | Server internal error      | server     | |
| 3203 | **Api not implement**      | **server** | The firmware does not serve that api_id. The discriminator for every "does this firmware have X?" question |
| 3204 | Api parameter error        | server     | |
| 3205–3207 | Lease errors          | server     | Should be unreachable — see §1.4 |

**[src]**

---

## 2. The `sport` service (loco)

Authoritative source: `~/gemm/ros2_ws/src/external/unitree_ros2/example/src/include/g1/
g1_loco_client.hpp`, the firmware-matched tree. **[src]** Note the G1's locomotion service
is literally named **`sport`** — it was renamed from `loco` in mid-2025 and H1 still uses
`loco`. Material calling the G1 service `loco` is stale. **[web]**

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
| 7107   | `SET_SPEED_MODE`       | `{"data": <int>}`                               | " |

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
`InternalFsmMode`), but it does **not** promote 7110 to something worth trying: it hands the
robot to low-level control on `rt/user_lowcmd`, which is the *opposite* of entering a
built-in walk policy, and the vendor example for it exits unless `fsm_id == 1` (PASSIVE).
**[src]/[web]** Both ids are absent from the firmware-matched tree. Do not spend a supervised
window on them.

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

### 3.3 Mode names

The vendor's own G1 example decodes `{form, name}` like this, for `form == "0"`: **[src]**

| `name`       | Service that owns the robot |
| ------------ | --------------------------- |
| `normal`     | `sport_mode`                |
| `ai`         | `ai_sport`                  |
| `advanced`   | `advanced_sport`            |
| *(empty)*    | "The motion control-related service is deactivated." |

**Live reading, 2026-08-14: `rpc_code 0`, `{'form': '0', 'name': ''}`.** **[live]** Empty
name — no controller loaded, the robot in what `xr_teleoperate` calls debug mode.

That state is reachable by accident from the other stack on this robot:
`xr_teleoperate`'s `Enter_Debug_Mode()` loops `ReleaseMode()` until `CheckMode` returns an
empty name, and it runs automatically whenever `teleop_hand_and_arm.py` is started
**without** `--motion`. `Exit_Debug_Mode()` calls `SelectMode(nameOrAlias='ai')`. **[src]**
So a teleop session deliberately leaves the robot with no controller loaded — and
`DEPLOYMENT.md` §2's interlock does not know about `xr_teleoperate` at all —
see `ROBOT-PERIPHERALS.md` §7.2 and §13.15 below.

### 3.4 We had to reimplement it

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
| 4   | **`StandUp()`**        | **"Lock Standing"**, No Balance Control | **`PREPARATION`** | **[live]** sent 2026-08-13, robot physically stood (odom z 0.04 → ~1.00 m) |
| 500 | **`Start()`**          | **"Walk Motion"** (1-DoF-waist policy) | **`WALK`**         | **[live]** accepted `code 0`, **no transition** — §11 |
| 501 | *(absent from every header)* | "Walk Motion-3Dof-waist"     | `WALK_WAIST`           | **[web]** only — never sent |
| 503 | —                      | —                                  | `DANCE`                | **[?]** our enum only |
| 702 | `Lie2StandUp()` *(newer SDK)* | "Lie Down, Stand Up"        | `LIE_UP`               | **[src]** |
| 706 | `Squat2StandUp()` *(newer SDK)* | "Balance Squat, Squat Stand" | `SQUAT_UP`          | **[src]** for the id. The claim that the Python SDK sends 706 for **both** directions (i.e. it toggles) is **[web]** and unchecked here — read 7001 before and after either way |
| 801 | —                      | "Run"                              | `RUN`                  | **[web]** |
| 802 | —                      | one remark: 29-DoF `ai_sport` renumbered Run 801 → 802 after 8.6.x | labelled `"run"`, marked suspect | **[live]** read on 2026-08-11 while the robot stood perfectly still |
| 812 | —                      | —                                  | `CLIMB`                | **[?]** our enum only |

Three names for id 4 (`StandUp` / `Lock Standing` / `PREPARATION`) and three for 500
(`Start` / `Walk Motion` / `WALK`). **When reading anyone's notes, translate to the number
first.** Our `PREPARATION` in particular invites the reading "a preparatory state on the way
to walking", which is an interpretation, not a vendor claim.

Two historical ids to recognise but never send: **200** was `Start()` before 2025-06 (C++) /
2026-04 (Python) **[web]**, and **601** is `Start()` on the **H2** — which uses the same
service name, the same api_ids and the same error codes as the G1, so an H2-sourced recipe
looks perfectly plausible and puts the wrong id on the wire. **[web]**

### 4.2 `fsm_mode`, `mode_pr`, `mode_machine` — three fields, none of them the FSM id

- **`fsm_mode`** (api 7002): official docs say 0 = static, 1 = dynamic. **[web]** The arm
  service's own error text implies a **3** exists ("in the state 801, the actions are only
  supported in the fsm mode {0, 3}"). **[src]** The widely-repeated claim that **2 = "feet
  unloaded"** rests on a single self-declared LLM-generated repo, and the second on-robot
  file that appears to corroborate it is a copy of the first. **No vendor source documents
  the value 2.** **[?]**
- **`mode_machine`** is the **robot type**, not a mode. The vendor's low-level example prints
  it as `"G1 type: "` and every `LowCmd_` publisher must read it from `LowState_` and echo it
  back. **[src]** Ours reads **5**, and read 5 at `fsm_id` 0, 4 and 802 — three different FSM
  ids, same value, which is exactly what a type field does. **[live]** `ROBOT-INVENTORY.md`
  §6's "they are genuinely independent fields" is right; this says *why*.
- **`mode_pr`** selects the ankle/wrist control convention: `PR = 0` (series pitch/roll),
  `AB = 1` (parallel A/B). Must also be set correctly in any `LowCmd_`. **[src]**

### 4.3 Transition rules

**What the firmware enforces is unknown.** No vendor source states the legal transition
graph; the only refusal code that exists is 7302 for an invalid id, and we have never seen
it. What we have:

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

- **No vendor source states the sign or axis convention for `vx`/`vy`/`omega`.** The
  parameters are named and never explained anywhere. ROS REP-103 (x forward, y left, yaw
  CCW) is the near-universal default and almost certainly what Unitree used — but that is
  inference. **Measure it.** **[web]**
- **No velocity limit exists in any vendor source** for this path: no clamp, no constant, no
  range comment under `include/unitree/robot/g1/`. The two numbers usually quoted as limits
  are not limits — the ~2 m/s marketing figure, and `unitree_rl_lab`'s training ranges
  (vx −0.5…1.0, vy −0.3…0.3, ωz −0.2…0.2), which apply to RL policies over `rt/lowcmd`, a
  different control path. Use the RL ranges as a conservative **ceiling**, never a target.
  **[web]**
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

The two vendor maps on this robot agree except at id 13: **[src]**

| id | C++ `action_map`              | Python `action_map` | In our `Gesture` enum |
| -- | ----------------------------- | ------------------- | --------------------- |
| 11 | two-hand kiss                 | two-hand kiss       | **missing** |
| 12 | left kiss **and** right kiss  | left kiss           | `BLOW_KISS` |
| 13 | *(absent)*                    | **right kiss**      | **missing** |
| 15 | hands up                      | hands up            | `HANDS_UP` |
| 17 | clap                          | clap                | `CLAP` |
| 18 | high five                     | high five           | `HIGH_FIVE` |
| 19 | hug                           | hug                 | `HUG` |
| 20 | heart                         | heart               | `HEART_BOTH_HANDS` |
| 21 | right heart                   | right heart         | `HEART_SINGLE_HAND` |
| 22 | reject                        | reject              | `REFUSE` |
| 23 | right hand up                 | right hand up       | `SINGLE_HAND_UP` |
| 24 | x-ray                         | x-ray               | `ULTRAMAN_RAY` |
| 25 | face wave                     | face wave           | `LOW_WAVE` |
| 26 | high wave                     | high wave           | `HIGH_WAVE` — **verified live 2026-08-11** |
| 27 | shake hand                    | shake hand          | `SHAKE_HANDS` |
| 99 | release arm                   | release arm         | `RELEASE_ARM` |

The C++ map inserts `{"left kiss",12}` and `{"right kiss",12}` into one `std::map`, so the
second insert is silently dropped — the bug is present in the copy on this robot. **[src]**

**There is no id 14, no id 16 and no id 36 in either map.** Our `Gesture.FORWARD_PUSH = 36`,
used by `point_at`, has **zero backing in any vendor artifact on this machine**; its only
provenance is a decompiled Android app. **[src]** `point_at` is on materially weaker ground
than `wave`, `hug` or `clap`, and the honest fix is not to guess a replacement — it is to
call **`arm`/7107 `GET_ACTION_LIST`** (no parameter, returns the whole catalogue as JSON)
and let the firmware answer. That call has never been made. **[src]**

**Parameter key.** Three vendor clients, two shapes: the ROS 2 example and the Python SDK
send `{"data": N}`, the newer C++ SDK sends `{"action_id": N}`. **[src]** Two of three say
`data`, and the live evidence agrees — the 2026-08-11 wave went out as `{"data":26}` and the
arm moved. **`{"data": N}` is correct on this firmware; do not "fix" our bridge to match
the C++ header.** **[live]**

The newer SDK also declares `7108 EXECUTE_CUSTOM_ACTION` (`{"action_name": "..."}`) and
`7113 STOP_CUSTOM_ACTION` (empty), with **no client method generated for either**, so no
parameter shape for 7113 exists on this machine, and neither id is in the firmware-matched
tree. **[src]**

### 6.4 Arm error codes, and the holding latch

| Code | Symbol                          | Message |
| ---- | ------------------------------- | ------- |
| 7400 | `..._ERR_ARMSDK`                | "The topic rt/armsdk is occupied." |
| 7401 | `..._ERR_HOLDING`               | "The arm is holding. Expecting release action(99) or the same last action id." |
| 7402 | `..._ERR_INVALID_ACTION_ID`     | "Invalid action id." |
| 7404 | `..._ERR_INVALID_FSM_ID`        | "Invalid fsm id." |

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

So per this firmware's own header, gestures **require** a locomotion state. But Unitree's
official documentation site says the opposite — 7404 is "Current FsmID cannot trigger this
action", remarked *"Some actions cannot be triggered under walking/running motion
control"*, i.e. gestures are **blocked during** locomotion. **[web]** Two Unitree-authored
sources, opposite polarities. A third source (`legion1581/unitree_ui`, E-grade) says
gestures need a locomotion state *and* that four of them are hidden specifically in Run.

This is not academic: it decides whether a gesture-capable state is one we are trying to
**enter** or one we are trying to **avoid**.

Our own live evidence fits the header's polarity but not its list: a gesture failed 7404 at
`fsm_id=4` and the same gesture succeeded at `fsm_id=802` — and **802 is not in
{500, 501, 801}**. **[live]** Either the header's set is stale for this firmware or 802 is a
sub-state of 801. Unresolved.

`g1_protocol.is_locomotion_state()` encodes the E-grade rule — **and is never called**.
`grep` finds it only at its own definition. The `wave` that failed went straight to the
wire; there is no client-side gate to remove. **[src]**

### 6.6 Ack semantics, and the false-failure it causes

`sport` acks promptly. **`arm` acks on completion of the motion** — 4.19 s for a wave. With
the SDK's default timeout every gesture returned `3104 RPC_ERR_CLIENT_API_TIMEOUT` *while
the robot was visibly performing it*. **[live]** That is a false failure in the dangerous
direction: an operator or an LLM reads "failed" and retries a command the robot already
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
  outside vendored SDK source returns nothing. So even if a Dex3 *were* plugged in, nothing
  here would publish `rt/dex3/*/state` or consume `rt/dex3/*/cmd`.

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

**Two repo corrections follow.** `g1_protocol.REAL_TOPICS` has
`dex_left_cmd="rt/api/dex3/left/request"` — an RPC-shaped name with no support in any vendor
source. **The hands are not an RPC service**: there is no api_id and no JSON envelope, it is
a raw `HandCmd_` publish. And `SPEC.md` §17.5's state type is wrong — it is `HandState_`,
not `MotorStates_`. **[src]**

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
| 1005   | `GET_VOLUME` | empty → `{"volume": <uint8>}` |
| 1006   | `SET_VOLUME` | `{"volume": <uint8>}` |
| 1010   | `SET_RGB_LED`| `{"R": <uint8>, "G": <uint8>, "B": <uint8>}` |

**[src]** Only one error code is declared for this service: **100 "Invalid parameter"**.

`speaker_id` **0 = Chinese, 1 = English**, and there is no third voice — the colleague
verified on this robot that neither reads Spanish intelligibly, which is why their stack
synthesises externally and pushes PCM through `PlayStream`. **[src]** PCM must be **16 kHz
mono 16-bit**; both vendor examples hard-reject anything else. Chunk at 96000 bytes (3 s),
roughly one chunk per second of wall time.

Three things to get right if we implement it:

- **`_CallRequestWithParamAndBin` already exists** in our installed `rpc/client.py`, so PCM
  playback needs no dependency change — the missing `unitree_sdk2py.g1` package is not
  actually required. Register `("voice", (1001, 1003, 1004, 1005, 1006, 1010))` on the
  existing `_G1Client` and call. **[src]**
- **The vendored Python `TtsMaker` has a bug**: `self.tts_index += self.tts_index`, so
  `index` stays 0 forever. The A2 copy of the same file has the correct `+= 1`. If the
  firmware dedupes on index, repeated utterances silently do not play. **[src]**
- **`PlayStop` takes `app_name`**, per the header, the Python client and the JSON key — but
  the vendor C++ example passes `stream_id`. Follow the header. **[src]** And use our own
  `app_name`: `gemm-ai.service` is a live writer on this service with
  `APP_NAME = "gemm-ai"`, so we cannot stop their stream and they cannot stop ours, but the
  speaker will interleave. **[live]**

`GET_VOLUME` (1005) is the one genuinely read-only call on the whole service and would
settle both the value range and whether the service is alive at all.

**The microphone is not on this service and not on DDS.** Raw audio is a UDP multicast feed:
**239.168.123.161:5555, 16 kHz mono s16le** — vendor-documented, in Unitree's own C++
example, not community lore. The join must pin `imr_interface` to eth0's address or the
kernel picks a default route and you get zero packets with no error. **[src]** `rt/audio_msg`
is a `std_msgs::msg::dds_::String_` carrying **JSON with a `text` key** — the embedded ASR's
output, never audio. **[src]** Our bridge's CycloneDDS config sets
`<AllowMulticast>false</AllowMulticast>` with a unicast peer, which is fine for the `voice`
RPC and for `rt/audio_msg`, and simply irrelevant to the mic feed — a future `listen()` must
open its own socket. **[src]**

---

## 8. `robot_state` — the probe we should run first, and never have

Service `robot_state`, `rt/api/robot_state/request`, api version `"1.0.0.1"`: **[src]**

| api_id | Call             | Parameter                          | Response |
| ------ | ---------------- | ---------------------------------- | -------- |
| 1001   | `SERVICE_SWITCH` | `{"name": "<svc>", "switch": 0\|1}` | `{"name":…, "status": int}` — **a write, do not call** |
| 1002   | `SET_REPORT_FREQ`| `{"interval": int, "duration": int}` | — |
| 1003   | `SERVICE_LIST`   | `{}`                               | JSON array of `{"name": str, "status": 0\|1, "protect": bool}` |

`status == 5` from 1001 means the service is protected (client maps it to `5202
SERVICE_PROTECTED`); any other non-0/1 status maps to `5201`. **[src]**

**1003 is the highest-value zero-motion probe on this entire robot.** One call answers, for
*this* firmware: does `motion_switcher` exist, does `slam_nav`, does `lidar_driver`, which
are running, which are protected. It also proves a structural point already confirmed
indirectly — **topics can be absent until a service is switched on**: `/utlidar/*` only
exists while `lidar_driver` is enabled, which is why a 2026-08-07 conclusion that those
topics "do not exist in any DDS domain" was wrong. **[src]**

The client is **already installed** in our bridge venv, no dependency change needed:

```python
from unitree_sdk2py.go2.robot_state.robot_state_client import RobotStateClient
c = RobotStateClient(); c.SetTimeout(3.0); c.Init()
code, services = c.ServiceList()      # api_id 1003, parameter "{}"
```

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
problem in §6.6. The catch is the topic name: the vendor SDK uses bare `rt/sportmodestate`
while our `REAL_TOPICS` uses `rt/lf/sportmodestate`. Both spellings are real elsewhere in
the vendor examples; which one this firmware publishes is unconfirmed. **[?]**

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

IMU: `float32[4] quaternion` (**w,x,y,z** order — the vendor builds
`Eigen::Quaternionf(q[0],q[1],q[2],q[3])`), `float32[3] gyroscope`, `float32[3]
accelerometer`, `float32[3] rpy` (ZYX Euler, body frame; `rpy[2]` is yaw), `int16
temperature`. There is a **second IMU** on its own topic, `rt/secondary_imu`, same
`IMUState_` type, used by vendor examples as the torso IMU while the pelvis IMU rides inside
`LowState_`. **[src]**

**Motor index map** (`G1JointIndex`, 29 real motors in a 35-slot array): **[src]**

| idx   | joint                          | idx   | joint |
| ----- | ------------------------------ | ----- | ----- |
| 0–5   | left hip pitch/roll/yaw, knee, ankle pitch(B), ankle roll(A) | 15–21 | left shoulder pitch/roll/yaw, elbow, wrist roll/pitch/yaw |
| 6–11  | right, same order              | 22–28 | right arm, same order |
| 12    | waist yaw                      | **29** | **not a joint — the `rt/arm_sdk` blend weight slot** (`motor_cmd[29].q`, 0..1) |
| 13/14 | waist roll (A) / waist pitch (B) — marked "INVALID for 23-DoF or waist-locked builds" | 30–34 | no documented meaning anywhere |

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
silently swaps axes. **[src]**

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
transmitting at all, and **exactly which buttons were pressed** (crucially X vs Y — see
§11.3). **Nothing on this robot documents which combinations the firmware itself
intercepts**, and I will not guess: the only combo found anywhere on the machine is an
application-level demo choosing its own mapping. **[src]**

---

## 10. DDS topic census

Exact type names. Rates are measured where marked `[live]`.

### State — robot to us

| Topic | Type | Rate | Evidence |
| ----- | ---- | ---- | -------- |
| `rt/lowstate` | `unitree_hg::msg::dds_::LowState_` | 500 Hz | **[src]** (existence on this unit unconfirmed) |
| `rt/lf/lowstate` | `unitree_hg::msg::dds_::LowState_` | ~20 Hz | **[live]** — what our bridge uses |
| `rt/lf/bmsstate` | `unitree_hg::msg::dds_::BmsState_` | ~20 Hz | **[live]** (bags) |
| `rt/secondary_imu` | `unitree_hg::msg::dds_::IMUState_` | — | **[src]** torso IMU — newer SDK examples only; presence on this firmware unverified |
| `rt/sportmodestate` | `unitree_hg::msg::dds_::SportModeState_` | — | **[src]**, type inferred from the 7404 error text |
| `rt/lf/sportmodestate` | **two types registered at once** (`unitree_go` + `unitree_hg`) | — | **[src]** — see §9.2 |
| `rt/odommodestate` | `unitree_go::msg::dds_::SportModeState_` | — | **[live]** — our pose source |
| `rt/state_estimator/odom_pelvis` | `nav_msgs::msg::dds_::Odometry_` | ~51 Hz | **[live]** (bags) |
| `rt/lf/dex3/{left,right}/state` | `unitree_hg::msg::dds_::HandState_` | — | **[src]** — no driver on this robot |
| `rt/brainco/{left,right}/state` | `unitree_go::msg::dds_::MotorStates_` | 100 Hz poll | **[src]** — from `brainco_hand_server`, which was running **[live]**; never subscribed. Only a *right* hand answered (§6.8) |
| `rt/arm/action/state` | JSON `{holding,id,name}`; type unstated | — | **[src]** / type **[?]** |
| `rt/inspire/state` | `unitree_go::msg::dds_::MotorStates_` | — | **[src]** |

### Command — us to robot

| Topic | Type | Note |
| ----- | ---- | ---- |
| `rt/lowcmd` | `unitree_hg::msg::dds_::LowCmd_` | full-body low level, 500 Hz / 2 ms |
| `rt/arm_sdk` | `unitree_hg::msg::dds_::LowCmd_` | upper body injected into the running controller; weight at `motor_cmd[29].q` |
| `rt/user_lowcmd` | `unitree_hg::msg::dds_::LowCmd_` | only after 7110 |
| `rt/hand_sdk` | `unitree_go::msg::dds_::MotorCmds_` | 4 motors; `Motor_real = w*Hand_SDK + (1-w)*G1_Cmd` |
| `rt/dex3/{left,right}/cmd` | `unitree_hg::msg::dds_::HandCmd_` | **not** `rt/api/dex3/*/request` |
| `rt/brainco/{left,right}/cmd` | `unitree_go::msg::dds_::MotorCmds_` | 6 entries, q/dq normalised 0..1 |

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
| `rt/frontvideostream` | `unitree_go::msg::dds_::Go2FrontVideoData_` | — | **[src]** — the head `videohub_pc4` binary does create this writer |
| `rt/wirelesscontroller` | `unitree_go::msg::dds_::WirelessController_` | — | **[src]**, Go2 example only; presence on the G1 unverified |
| `rt/audio_msg` | `std_msgs::msg::dds_::String_` (JSON `text` / `play_state`) | — | **[src]** |
| mic PCM | **not DDS** — UDP multicast `239.168.123.161:5555`, 16 kHz mono s16le | — | **[src]** |

Sim-only, Isaac on domain 1: `rt/sim_state`, `rt/sim_state_cmd`, `rt/run_command/cmd`,
`rt/reset_pose/cmd`, `rt/dex1/{left,right}/{state,cmd}`.

### Topic names we inherited from the WebRTC reverse engineering, still unconfirmed

`rt/lf/battery_alarm`, `rt/lf/mainboardstate`, `rt/lf/secondary_imu` (vendor code uses
`rt/secondary_imu`, no `lf/`), `rt/multiplestate`, `rt/selftest`, `rt/servicestate`,
`rt/uwbstate`, `rt/utlidar/{switch,voxel_map_compressed,lidar_state,robot_pose}`,
the `rt/uslam/*` family, and most `rt/api/*` names beyond those in §1.3. **[web]** Two names
from that same list have since been confirmed independently (`rt/lf/bmsstate` by the bags,
`rt/api/robot_state/request` by the colleague), which raises the list's credibility without
establishing any single entry.

### QoS and transport

Publisher-side QoS recorded in the bags for `/lf/lowstate`, `/lf/bmsstate`,
`/state_estimator/odom_pelvis` and both `utlidar` topics: **KEEP_LAST, depth 1, RELIABLE,
VOLATILE**, infinite deadline/lifespan/liveliness. **[live]** Depth 1 is the part that
matters: there is no history to catch up on, so a slow subscriber silently drops samples.
Our reader depth of 10 is legal but buys nothing. **Conflict to be aware of:** the
colleague's prose insists the `utlidar` topics are BEST_EFFORT while the bag metadata they
themselves produced records RELIABLE. Trust the metadata over the prose, but verify.

Transport parameters (all confirmed, see also `ROBOT-INVENTORY.md` §2 and `DEPLOYMENT.md`
§4): **domain 0** on the real robot (1 is Isaac Sim), **interface pinned to `eth0`**,
CycloneDDS **0.10.2**, `<AllowMulticast>false</AllowMulticast>` with a unicast
`<Peer address="192.168.123.161"/>`. **[live]** The vendor pins `eth0` in its own module
config using the same 0.10.2 schema — independent confirmation that our `DDS_INTERFACE`
decision matches what Unitree does on this box. **[live]**

---

## 11. UNRESOLVED: `Start()` / `fsm_id = 500`

**This is the blocker. Nothing walks until it is resolved, and it is not resolved.**

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
in debug mode the whole time"). **Do not.** On 2026-08-13 a controller *was* loaded and doing
work — `SetFsmId(4)` lifted the robot from odom z 0.04 to ~1.00 m — and `Start()` was still
accepted-and-ignored. `CheckMode` was **not** run that day, so we do not know *which*
controller. **Running `CheckMode` at the top of the next window is the single most important
step**, because it is the only thing that distinguishes the two situations retrospectively.

### 11.2 Ruled out on 2026-08-13

- **Feet unloaded on the gantry** — retested with the robot weight-bearing. **[live]**
- **Missing `BalanceStand`** — `SetBalanceMode(0)` sent, `code 0`, no effect on `Start()`.
  **[live]**
- **The operator's handheld remote** — also failed to move the robot, so this is not
  "the SDK path specifically is broken". **[live]**
- **Malformed request** — our wire bytes are byte-for-byte what the vendor sends:
  `7101 {"data":500}` against `js["data"] = fsm_id; req.parameter = js.dump();`. Not a
  float-vs-int, not a wrong key, not a wrong service. **[src]**
- **SDK-generation skew (`Start()` used to be 200)** — both SDK generations on this machine
  are post-2025-06 and both say `Start() = SetFsmId(500)`. **[src]**

### 11.3 Ranked candidates

**Rank 1 (hypothesis, untested) — 500 may be the wrong walk program for this chassis, and
this machine's walk may be 501.** It ranks first because it explains the signature, not
because anything on the robot has confirmed it; the two-family model itself is `[web]`.

The firmware exposes two parallel locomotion families selected by waist DoF: 500 "Walk
Motion" / 801 "Run" for the 1-DoF-waist G1, and 501 "Walk Motion-3Dof-waist" / 802 for the
3-DoF-waist G1. **[web]** Under that model, 500 is a member of the enum (so the lookup
passes and you get `code 0`, not 7302) naming a policy that is not built for this chassis (so
no transition happens) — which is precisely the observed signature.

Four on-robot artifacts, from two teams, say this is the 3-DoF-waist / 29-DoF build — all of
them *configuration*, none of them a hardware read (§13.4): **[src]**
`g1pilot`'s URDF for this robot declares `waist_yaw_joint`, `waist_roll_joint` and
`waist_pitch_joint`; `xr_teleoperate` is launched `--arm=G1_29`, whose joint enum has real
`kWaistRoll`/`kWaistPitch` where the `G1_23` enum names those slots `NotUsed`; the asset is
`g1_body29_hand14.urdf`. Two teams configured this machine that way independently. And the
strongest single piece: **this robot has already been observed at `fsm_id = 802`** with a
working arm gesture — a state in the 3-DoF branch, never the 1-DoF 500. **[live]**

The same frame explains the remote for free: a third-party controls reference lists a
separate section, "3-DOF Waist Structure Only (R1 + Y)", alongside the usual R1+X "Main
Operation Control". **[web]** If the operator pressed R1+X — the combo every popular doc
names, including Unitree's own wiki — the remote failing is the *same* root cause, not an
independent second blocker. That corroboration comes from **one site** (two firmware
revisions of it) and a general search could not confirm it elsewhere; grade it accordingly.

**The fix, if this holds, is already in our own code**: `g1_protocol.Mode.WALK_WAIST = 501`
exists, and `_PREPARATION_TARGETS` already lists 501 and 801 as legal from state 4.
`SKILL_REQUESTS["start_walking"]` is simply wired to `Mode.WALK` (500). **[src]**

*Next experiment:* at `fsm_id == 4`, send `7101 {"data": 501}` and poll 7001 every 250 ms for
3 s. Then, only if that does nothing, have the operator press **R1+Y** (not R1+X).

**Rank 2 — motion_switcher authority: the wrong controller, or none.**

The 2026-08-14 empty-name reading proves the robot *can* be in a no-controller state, and
that the co-tenant stack puts it there by default (§3.3). Against this candidate for the
2026-08-13 observation: `Damp` and `StandUp` physically executed, which a fully released mode
should not permit. It survives only in the weaker form "the active mode was `normal` /
`sport_mode` rather than `ai` / `ai_sport`". **[src]**

*Next experiment:* `CheckMode` — first thing, before anything is touched. Expect
`{"form":"0","name":"ai"}`. Anything else promotes this to rank 1 and the fix becomes
`SelectMode("ai")` rather than any FSM id we guess.

**Rank 3 — an unmet precondition around stand height / feet loading.**

`g1pilot`'s production path is not what we sent: it ramps `SetStandHeight` in 0.02 m steps
until `get_fsm_mode() == 0` **and** height ≥ 0.2, then calls `BalanceStand(**1**)` — i.e.
`SetBalanceMode(1)`, continuous gait — then `SetStandHeight`, then `Start()`. We sent
`SetBalanceMode(0)` and **never ramped stand height at all**. **[src]** Caveat: its sibling
`test.py` still early-outs on `cur_id == 200`, so that file is a copy of the pre-2025-06
community script and is **not** independent corroboration of the `fsm_mode == 2` "feet
unloaded" claim, which no vendor source documents.

*Next experiment:* read 7002 with the gantry carrying weight and again with the feet loaded.
If it only ever returns 0 or 1, this candidate dies for free.

**Rank 4 — battery or thermal, never ruled out.**

`faults: none, battery: null` is an artifact of our own code, not a health reading (§9.4).
A low-SOC or over-temp guard would explain *both* the SDK and the remote failing, which no
other candidate does as neatly. Everything needed is one subscription away (§9.4). **[src]**

*Next experiment:* subscribe `rt/lf/bmsstate`, log `soc`, `soh`, `temperature[12]`,
`bmsstate[5]`. Expect `soc` comfortably above ~30.

**Rank 5 — the remote never reached the robot.**

"The remote also failed" is currently an anecdote. `LowState_.wireless_remote[40]` (§9.5)
turns it into a measurement, from a topic we already subscribe to and a field we already
discard.

*Next experiment:* decode it while the operator presses keys. Expect `head == {0xFE,0xEF}`
and a changing `btn`. If it never changes, the remote is not linked and its "failure" is not
evidence about the FSM at all.

### 11.4 The calibration step that everything else depends on

Send `7101 {"data": 99999}` and expect **`7302 Invalid fsm id`**.

This is the load-bearing zero-motion check. It proves the firmware *rejects* unknown ids —
which is what licenses the inference that `code 0` on 500 means 500 **is** in the enum and
the failure is at the transition, not the lookup. Every candidate above is built on that
inference and **it has never been tested**. If 99999 also returns 0, the entire reading of
the evidence changes and the right move is to stop and re-plan.

Two other pure queries worth running in the same breath: `robot_state` **1003 ServiceList**
(§8) and `arm` **7107 GET_ACTION_LIST** (§6.3). Both are reads; both would retire several
open questions each. Note that `7008 GET_AVAILABLE_FSM_IDS` — which would settle the whole
500/501/801/802 argument in one call — is declared for **H2**, not G1, and is absent from
this robot's G1 header. It is worth one probe (a `3203` answers cleanly at zero risk) but it
is not expected to exist. **[src]**

### 11.5 One thing this is *not*

`master_service.service` was found **stopped** (deliberately, at 01:40:34 CST on
2026-08-14, to free the head camera — documented in `xr_teleoperate`'s README). **[live]** It
is tempting to connect that to the FSM. Don't: `strings` on the binary shows it contains no
FSM, `ai_sport`, `motion_switcher` or `loco` code whatsoever, and it supervises only
`ota_pipe` and the two video-hub nodes. **[live]** Restarting it changes camera behaviour and
nothing else. (It does also run `amixer set Speaker 75%` at boot, so while it is dead the
Jetson's speaker volume is unset — relevant to audio, not to motion.)

---

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

---

## 13. Open questions

Ordered by how much each unblocks. Every one is blocked on robot access.

1. **Which controller was loaded on 2026-08-13?** Settled by `motion_switcher` 1001
   `CheckMode` at the top of the next window — and by running it *before* anything else is
   touched, every time, from now on. Without it, "wrong FSM id" and "no controller loaded"
   are indistinguishable (§3.1, §11.1).
2. **Does `7101 {"data": 99999}` return 7302?** The inference the whole §11 analysis rests
   on. One zero-motion call (§11.4).
3. **Does `7101 {"data": 501}` transition this robot?** The rank-1 candidate. If yes, the fix
   is one enum member (§11.3).
4. **Is this physically a 3-DoF-waist machine?** Everything we have is configuration files two
   teams chose, not a hardware read. Settle by reading `motor_state[13]`/`[14]`
   (`kWaistRoll`/`kWaistPitch`) off `rt/lf/lowstate` — a real 3-DoF waist reports live
   q/temperature/voltage there — or by counting joints physically.
5. **What does `robot_state` 1003 `ServiceList` return?** One read; tells us which vendor
   services exist and are running on *this* firmware, retiring most of §1.3's inferences (§8).
6. **What does `arm` 7107 `GET_ACTION_LIST` return?** The firmware's own gesture catalogue.
   Settles `FORWARD_PUSH = 36` and the id-13 disagreement (§6.3).
7. **What is the vx/vy/omega sign convention, and the real velocity scaling?** Nothing
   documents either. Must be measured, and the sim gains will not transfer (§5.3).
8. **7404 polarity:** do gestures *require* a locomotion state (vendor header) or are they
   *blocked during* one (official docs)? Our own 802 evidence fits neither list exactly.
   Settle by reading 7001/7002 immediately before and after a supervised gesture (§6.5).
9. **Which FSM topic exists — `rt/sportmodestate` or `rt/lf/sportmodestate` — and what type?**
   Settle with the 20-line hand-written IDL in §9.2 and a passive subscribe; zero writes.
10. **Battery decoding:** is `current` mA or 10 mA, is `bmsvoltage[3]` mV, how many
    `cell_vol`/`temperature` entries this pack populates. One decoded message (§9.4).
11. **Does `rt/arm/action/state` exist, and what type?** It is the clean fix for the
    false-timeout problem and would also let us handle 7401 (§6.6).
12. **Is `rt/lowstate` (500 Hz) published on this robot, or only `rt/lf/lowstate`?** And what
    types really sit on `rt/lf/sportmodestate`? A passive `DCPSPublication` read answers both
    and would produce the definitive live census in one shot — a probe script for exactly
    this was left on the robot at `/tmp/c3po_audio_probe.py` and never run.
13. **Are 7110/7111 and arm 7108/7113 served by firmware 1.5.3.8?** `3203` vs `0` discriminates
    cleanly, but each is a write, so it belongs in a supervised window. Low priority — 7110 is
    not a route to walking (§2.4).
14. **Which button combinations does the firmware intercept?** Nothing on the robot documents
    it. Log `wireless_remote` while an operator presses each combo and watch whether `fsm_id`
    changes with no RPC from us — that distinguishes firmware-intercepted from free (§9.5).
15. **Does the arm service still work while something else holds `rt/arm_sdk`?** The 7400
    string says it will not. Worth testing deliberately rather than discovering mid-demo
    (§6.4) — and worth widening `run_c3po`'s commander check to cover `xr_teleoperate`,
    `brainco_hand_server` and any publisher on `rt/arm_sdk` / `rt/lowcmd`, which is a real
    hole in the one-commander invariant of `DEPLOYMENT.md` §2.
16. **Does the vendor fault stream have a DDS source at all?** `faults.py`'s catalogue
    (sources 100–1000, including 1000 = Emergency Stop) is web-grade and unwired. One
    candidate we already receive and ignore: `uint32 error_code` in the `unitree_go`
    `SportModeState_` on `rt/odommodestate`. Log that field before building anything more
    elaborate.
