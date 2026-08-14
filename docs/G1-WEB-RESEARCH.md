# G1 Web Research — leads, not facts

> **STATUS: WEB-SOURCED AND UNVERIFIED.** Nothing in this file has been tested against our
> robot. Every claim here is a **lead to test**, not a fact to build on. Compiled
> **2026-08-12** from vendor repos, Unitree's docs site, issue trackers and third-party
> writeups.
>
> **`ROBOT-INVENTORY.md` and `ROBOT-API.md` hold what we have actually confirmed.** This
> file must never be cited as if it were one of them, quoted into code comments as
> established behaviour, or used to justify a motion command. Where this document and those
> two disagree, **they win, always** — see §3.
>
> The reason for the hard line: G1 firmware moves fast, most sources online do not state
> which firmware or which robot variant they describe, and several of them contradict each
> other on the exact numbers we are about to put on the wire.

---

## 0. How to read the confidence column

Everything here would carry `[web]` in inventory notation, so that tag is replaced by a
grade. The grade describes **the source**, not our belief that it applies to our robot —
even an A-grade claim may be about a different firmware generation than ours.

| Grade | Meaning                                                                                                                                                     |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A** | Primary vendor source read directly — header, example, or commit diff, quoted verbatim. Tells us what the **SDK** does. Says nothing about what firmware does. |
| **B** | Unitree's own documentation site. Authoritative in intent, but every G1 page is **undated and carries no firmware stamp**, and the site is a JS SPA that only rendered through a text proxy. |
| **C** | Vendor-adjacent: Unitree's issue tracker or project wikis. Maintainer replies are stronger; unanswered user reports are weaker and are marked as such.        |
| **D** | Third-party lab or integrator writeup. Careful work, but single-source.                                                                                      |
| **E** | Single forum post, unsourced third-party table, or a search-engine summary that could not be traced to an original. Hypothesis generator only.                |

### Version anchors — and why we cannot use any of them yet

Almost every disagreement below is **version-gated**, and we currently cannot place our
robot on a single one of these timelines. Reading these numbers off the robot is the
cheapest thing on the whole experiment list and would resolve several conflicts without
touching the legs.

| Anchor                                        | What it gates                                                              | Source grade |
| --------------------------------------------- | -------------------------------------------------------------------------- | ------------ |
| `ai_sport` **8.6.x.x**                        | 29-DoF machines renumber Run from 801 to **802** after this version         | B            |
| Firmware **v1.4.9**                           | 1-DoF-waist G1 reportedly loses access to FSM **501**                       | D            |
| Firmware **v1.3.0**                           | Claimed minimum for SDK audio/LED; EDU edition claimed required for SDK use | C            |
| `vui_service` ≥ **2.0.3.5**, `vui_module` ≥ **2.0.0.3** | English TTS and microphone acquisition                            | A            |
| Motion switcher **1.1.6**                     | Mode-name scheme changed (`ai`/`normal`/`advanced` are the legacy naming)   | B (Go2 page) |
| `unitree_sdk2` **2025-06-09**                 | C++ `Start()` changed `SetFsmId(200)` → `SetFsmId(500)`                     | A            |
| `unitree_sdk2_python` **2026-04-20**          | Python `Start()` changed `SetFsmId(200)` → `SetFsmId(500)`                  | A            |
| `unitree_sdk2` **2026-02-26**                 | api_ids **7110/7111** (user ctrl) added                                     | A            |
| `unitree_sdk2_python` **2026-05-11** (`d801b12`) | `unitree_sdk2py/g1/` subpackages became installable                      | A            |
| Our own `g1_protocol.py` header               | Claims the catalogue targets "firmware ≥ 1.5.1" — **our assumption**, inherited from a reverse-engineered UI | — |

---

## 1. Leads on the `Start()` / `fsm_id=500` blocker

### What we are explaining

The evidence any candidate has to fit:

| # | Observation                                                                                  |
| - | -------------------------------------------------------------------------------------------- |
| a | `SetFsmId(1)` (Damp) and `SetFsmId(4)` both **physically execute** — the robot moved          |
| b | `SetFsmId(500)` returns **`code=0`** and `fsm_id` **stays 4**                                  |
| c | api_id 7106 arm gesture at `fsm_id=4` returns **7404**; the same gesture at `fsm_id=802` succeeded (inventory §3) |
| d | `fsm_id=802` was reached at least once on 2026-08-11, and 7003 returned **7301** there        |
| e | The operator's **hand controller** bring-up also failed                                       |

Observation (a) is the load-bearing one for ranking: it means the high-level motion service
**is alive and obeying us**. That demotes every "something else owns the controller"
explanation and promotes "we are asking for the wrong state".

### Ranked candidates

#### Candidate 1 — 500 is the wrong walk target for this build; this machine's states are 501/801/802

**Fit: strong.** Unitree's own FSM table gives 500 = "Walk Motion" and 501 = "Walk
Motion-3Dof-waist" — two *different walk policies selected by waist DoF*, not a generic
"start". The same table's 801 "Run" row carries the remark *"The 29dof device ai_sport was
updated to version 802 after version 8.6.x.x"*. Our robot has been seen at 802, which
identifies it as a 29-DoF machine on ai_sport ≥ 8.6 — exactly the configuration for which
500 is the *other* variant's policy. That would make 500 a recognised-but-unenterable id:
recognised, so not `7302 Invalid fsm id`; unenterable, so no transition. Grade **B** for the
table; grade **D** for CMU's independent recommendation to replace every `Start()` with
`SetFsmId(501)` or `SetFsmId(801)`.

Source: <https://support.unitree.com/home/en/G1_developer/sport_services_interface> (undated,
no firmware stamp) and <https://roboticsknowledgebase.com/wiki/common-platforms/unitree-g1/>
(page updated 2026-03-12; SDK version not stated).

**Caveat that must not be skipped:** 802 appears in **no other document anywhere** — not in
any vendor header, not in any vendor example, not in any third-party FSM table, and not in
the CMU page that discusses 801 at length. The single sentence above is the entire published
evidence for its existence.

**Experiments, cheapest first:**

1. **Zero-motion.** Probe sport api_id **7008 `GET_AVAILABLE_FSM_IDS`**. It is declared for
   H2, not G1 — but G1 and H2 share the service name, the api space and the error codes, so
   the firmware may implement it. *Expected:* a list, which retires the entire
   200-vs-500-vs-501-vs-801-vs-802 argument in one call; or `3203`, which tells us cleanly
   that it is unimplemented. *Proves:* the authoritative FSM table for **this** firmware.
2. **Zero-motion.** Send `7101` with `{"data": 99999}`. *Expected:* `7302 Invalid fsm id`.
   *Proves:* the firmware rejects unknown ids rather than ignoring them — which in turn
   proves that the `code=0` we get for 500 means 500 **is** known, and the failure is at the
   transition, not the lookup. This single call is what separates candidate 1 from
   "500 doesn't exist here".
3. **Gantry, weight supported, Damp ready.** From standing at 4, send `SetFsmId(802)`, poll
   7001 with a 1–2 s settle. Then, on separate runs, `801` and `501`. *Expected:* one of them
   changes `fsm_id`. *Proves:* which walk policy this build actually implements.

#### Candidate 2 — `fsm_id=4` is a "No Balance Control" state and is not a legal source for a walk policy; the vendor's own path never goes through it

**Fit: strong, and it is the only candidate that also explains (c).** The official mode table
tags 0, 1, 2, 3 **and 4** as "No Balance Control"; 500/501/702/706/801 are not so tagged. So
at fsm 4 the balance controller is not running, which is a coherent reason both for a refused
walk transition and for `7404 FSM_UNAVAILABLE` on an arm gesture that worked at 802.

Independently: **Unitree's own G1 example never calls `Start()` at all.** Its path to walking
is `Damp()` → `sleep 0.5` → `Squat2StandUp()` **[706]** → `Move()`. Our sequence
(Damp → 4 → 500) uses two states the vendor example never touches and skips the one it does.
Grade **A** for the example, **B** for the table.

Sources: <https://github.com/unitreerobotics/unitree_sdk2_python/blob/master/example/g1/high_level/g1_loco_client_example.py>
and the FSM table above.

**Experiments:**

1. **Gantry.** Run the vendor sequence verbatim instead of ours: `Damp()` [1] → sleep 0.5 s →
   `SetFsmId(706)` → poll 7001 → `SetVelocity(0,0,0,1.0)` → small non-zero `SetVelocity`.
   *Expected:* the robot stands and accepts velocity without ever visiting 4 or 500.
   *Proves:* that the blocker is our sequence, not the robot. Note **706 is a toggle** — the
   Python SDK sends 706 for *both* `Squat2StandUp` and `StandUp2Squat` — so read 7001 before
   and after.
2. **Zero-motion, high information.** At fsm 4, drive the arms over the low-level
   **`rt/arm_sdk`** topic instead of the 7106 gesture RPC. Official docs say that path works
   in "Locked Stance" (= 4). *Expected:* arms move. *Proves:* the motion service is alive and
   the problem is purely FSM-target selection. If the arms do **not** move either, motion
   authority really has been taken away and candidate 6 jumps to the top. This is the highest
   information-per-risk test on the entire list.

#### Candidate 3 — precondition unmet: the robot's feet are unloaded on the gantry

**Fit: good for (b), and it would be embarrassing to miss.** Sentdex's
`hanger_boot_sequence.py` exists specifically to bring a *hanging* G1 to a walk-ready state,
and its core loop ramps `SetStandHeight` upward until the firmware stops reporting
`fsm_mode == 2`, which the author labels "feet unloaded". If a walk policy refuses to engage
while the gantry carries the weight, that alone produces `code=0` with no transition.

**This is supported by exactly one source, and that source is weak.** Grade **E**. The repo
is self-declared LLM-generated ("this entire repo, readme, code…etc is all coded via Codex
using o3"), the author reports it working on hardware but pins no firmware version, and the
docstring slides between `SportModeState.mode` and the 7002 RPC — so the two may be
conflated. Worse, **no vendor source documents an `fsm_mode` value of 2 at all**: official
docs define `fsm_mode` as 0 = Static / 1 = Dynamic (elsewhere worded 0 = standing state,
1 = moving state).

Source: <https://github.com/Sentdex/unitree_g1_vibes/blob/main/hanger_boot_sequence.py>
(repo created 2025-05-02, last pushed 2025-07-22 — i.e. it predates the `Start()` 200→500
change; robot DoF and firmware not stated).

**Experiment (zero-motion, do it early because it costs one read):** poll **7002 alone**
while the robot hangs, and again with the gantry lowered so the feet take weight. *Expected
if true:* the value differs, and one of the readings is `2`. *Proves:* whether an undocumented
load-dependent sub-mode exists at all. If 7002 only ever returns 0 or 1, this candidate dies
for free.

#### Candidate 4 — waist-DoF variant, possibly app-locked, decides 500 vs 501

**Fit: moderate.** CMU reports that 500 is Regular Mode for the **1-DoF-waist** G1 and 501 for
the **3-DoF-waist** G1; that locking the waist in the Unitree Explore app makes a 3-DoF G1
behave as a 1-DoF one; and that *"Starting in firmware version v1.4.9, it seems the 1 DoF G1
can no longer use state 501"*. If our machine is waist-locked in the app, unlocking it may be
the whole fix.

**Single source.** Grade **D**, hedged in its own language ("it seems", "It is unknown why").
No vendor source acknowledges 501 or 801 as valid `SetFsmId` arguments — neither appears in
any header read.

Source: <https://roboticsknowledgebase.com/wiki/common-platforms/unitree-g1/>

**Experiment:** determine the variant definitively — 23-DoF vs 29-DoF, waist 3-DoF vs locked —
and check the Explore app's waist-lock setting. Zero motion. *Proves:* which of 500/501 is
even applicable, which is the premise both candidates 1 and 4 rest on.

#### Candidate 5 — SDK/firmware generation skew: `Start()` used to be 200

**Fit: weak for (b), but it is cheap and it has two independent human witnesses.** Unitree
changed `Start()` from `SetFsmId(200)` to `SetFsmId(500)` — C++ on **2025-06-09** (`40c02be`),
Python not until **2026-04-20** (`82d7dde`, author `weijiabin <rd_wjb@unitree.com>`). For ~10
months the two official SDKs disagreed with each other. If our firmware is from the 200 era,
500 could plausibly be recognised-but-dead.

Grade **A** for the commits themselves. The claim that **200 still works** is grade **E**:
GetSoloTech's `FSM_AND_SAFETY.md` publishes "200 Start / High-level locomotion active" with no
source, no firmware version and no date, and reads as synthesized; issue #104
(thomasschichl, 2025-09-08) independently complains that "ID 200 … is not mentioned in the
manual at all" after calling it, and got no maintainer reply.

Sources: <https://github.com/unitreerobotics/unitree_sdk2_python/commit/82d7ddee38fd1cba494a3cf5ff94d897d04b81c0>,
<https://github.com/unitreerobotics/unitree_sdk2_python/issues/104>

**Experiment:** after candidate 1's step 2 has calibrated the error channel, send
`SetFsmId(200)` on the gantry. *Expected:* `7302` if 200 is dead here. *Proves:* which SDK
generation this firmware belongs to — and, combined with a `7302` on 500, would say something
much more alarming than either result alone.

#### Candidate 6 — motion authority has been taken (debug mode, or `ai_sport` released)

**Fit: weak against our evidence, but the documentary support is the strongest of any
candidate, so it earns a read-only probe.** Official docs are unambiguous: *"After entering
the debugging mode, the built-in operation control is completely exited and the high-level
motion service becomes invalid."* Grade **B**.

But this **cuts against observation (a)**: in debug mode the high-level service is gone, so
`SetFsmId(4)` could not have physically stood our robot. Our robot executed 1 and 4. It is
very likely **not** in classic debug mode.

The parallel version — `ai_sport` released via `motion_switcher` rather than debug mode — is
grade **D** for G1 applicability. `MotionSwitcherClient::SelectMode("ai")` is reported to
re-activate it and `ReleaseMode()` to drop it, but **Unitree's own Motion Switcher page is
written for Go2 Edu and never mentions G1** (service name `mcf`; `ai`/`normal`/`advanced` are
the *legacy* mode strings for versions < 1.1.6). People apply it to G1 anyway and report it
working; that is not documentation.

Sources: <https://support.unitree.com/home/en/G1_developer/quick_start>,
<https://support.unitree.com/home/en/developer/Motion%20Switcher%20Service%20Interface>

**Experiments:**

1. **Read-only.** `motion_switcher` **1001 `CheckMode`** → `{form, name}`. Pure query.
   *Expected:* a mode name, telling us whether `ai_sport` is held, released, or owned by
   something else. If the service is absent on G1, that kills the hypothesis for free.
   **Do this before any `SelectMode`/`ReleaseMode` call.**
2. **One button press.** On the remote, press **L2+UP** — the official documented exit from
   debug mode back to ready — then retry the FSM targets. Note issue #43 claims *only a
   reboot* clears debug mode; sources directly conflict (§2), so if L2+UP does nothing, a
   power cycle is the next rung rather than a dead end.

#### Candidate 7 — wrong service surface

**Fit: poor for (b) — it does not explain `code=0` — but it costs nothing to check while we
are enumerating.** Issue #42 shows someone getting `3203 API not implemented on server` for
api_id 7106 on **`/api/loco/request`**, while we get `7404` on **`/api/sport/request`**.
Different topic, different failure. The G1 loco service was renamed from `"loco"` to
`"sport"` in June 2025 (`331352d`, 2025-06-13), so both names have been live at different
times. Grade **A** for the rename, **C** for the issue.

**Experiment:** enumerate the services actually present, and check whether this firmware
exposes `/api/loco/*` alongside `/api/sport/*`. *Proves:* whether the same api_id can route
to two implementations here.

### Explicitly single-sourced, in one place

Do not let any of these harden into an assumption. Each is one source, and in several cases
that source hedges its own language:

| Claim                                                          | Sole source                       | Grade |
| -------------------------------------------------------------- | --------------------------------- | ----- |
| `fsm_mode == 2` means "feet unloaded"                          | Sentdex `hanger_boot_sequence.py` | E     |
| 500 jitters; use 501 or 801 instead                            | CMU Robotics Knowledgebase        | D     |
| Firmware v1.4.9 removed 501 from 1-DoF G1s                     | CMU Robotics Knowledgebase        | D     |
| `SelectMode("ai")` re-enables `ai_sport` on a **G1**           | CMU Robotics Knowledgebase        | D     |
| Only a reboot exits debug mode                                 | issue #43                         | C     |
| Adding `self.Start()` inside `Move()` fixed a dead `SetVelocity` | robonomics.network blog (2024-12-27) | E |
| `200` is a live Start id                                       | GetSoloTech doc + issue #104      | E     |
| Gesture index **36** ("forward push")                          | legion1581/unitree_ui (decompiled APK) | E |
| "Arm gestures require a locomotion-active state"               | legion1581/unitree_ui             | E     |
| 802 exists at all                                              | one remark on one official page   | B     |

---

## 2. Where the sources contradict each other

**This is the most valuable section in the file.** Every row is a number we must not trust
until the robot arbitrates.

### 2.1 FSM ids

| Id      | Reading A                                                   | Reading B                                                        | Verdict                                                       |
| ------- | ----------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------- |
| **200** | `Start()` — "High-level locomotion active" (GetSoloTech `FSM_AND_SAFETY.md`, unsourced, no model, no firmware; issue #104) | Not present in any current vendor SDK or official table | Was correct pre-2025-06 (C++) / pre-2026-04 (Python). Unknown whether firmware still accepts it |
| **4**   | "Lock Standing", **No Balance Control** (official table)    | `StandUp()` (C++ header); **absent entirely** from the Python client | Both vendor-authored. Our own `g1_protocol.py` calls it `PREPARATION` — a third name |
| **500** | "Walk Motion", 1-DoF-waist policy (official table)          | "Various Walking/Movement States, 500+" (GetSoloTech, vague)      | Official table is far more specific; GetSoloTech is unsourced   |
| **501** | "Walk Motion-3Dof-waist" (official table)                   | Not present in any vendor **header**                              | Documented but not exposed by any SDK helper                    |
| **601** | `Start()` **on H2** (`h2_loco_client.hpp`)                   | Meaningless on G1                                                 | See the collision warning below                                 |
| **702** | "Lie Down, Stand Up" (official table); `Lie2StandUp()` (Python) | Absent from the C++ header                                     | C++/Python SDKs disagree                                        |
| **706** | "Balance Squat, Squat Stand" (official table)               | Python sends 706 for **both** `Squat2StandUp` **and** `StandUp2Squat` | Toggle, or a vendor copy-paste bug. Read 7001 before and after |
| **801** | "Run" (official table)                                       | "Running Mode", better balance than Regular (CMU)                 | Consistent                                                      |
| **802** | 29-DoF Run after ai_sport 8.6.x.x (one official remark)      | Documented nowhere else, by anyone                                | The only explanation of 802 that exists                         |

**The dangerous one — H2 is indistinguishable from G1 on the wire.** H2 uses the same service
name (`sport`), the same api_ids (7001–7006 / 7101–7107) and the **same error codes**
(7301/7302/7303, identical strings) — but `H2 Start() = SetFsmId(601)` where
`G1 Start() = SetFsmId(500)`. A blog post, snippet or model answer sourced from H2 material
will look perfectly plausible for a G1 and put the wrong id on the wire. This is the single
strongest argument for never accepting a bare "7101 with `data=N`" recipe without knowing
which robot it came from.
<https://github.com/unitreerobotics/unitree_sdk2/blob/main/include/unitree/robot/h2/loco/h2_loco_client.hpp>

### 2.2 `Start()` across SDK generations

| Source                            | `Start()` value | Date       |
| --------------------------------- | --------------- | ---------- |
| `unitree_sdk2` C++ (before)       | `SetFsmId(200)` | —          |
| `unitree_sdk2` C++ (`40c02be`)    | `SetFsmId(500)` | 2025-06-09 |
| `unitree_sdk2_python` (before)    | `SetFsmId(200)` | —          |
| `unitree_sdk2_python` (`82d7dde`) | `SetFsmId(500)` | 2026-04-20 |

Anyone who pinned the Python SDK before 2026-04-20 was sending 200. Both eras of blog post
are on the internet and neither says which era it is from.

### 2.3 C++ vs Python SDK — currently, both official, both shipping

| Helper           | C++ `g1_loco_client.hpp`    | Python `g1_loco_client.py`             |
| ---------------- | --------------------------- | -------------------------------------- |
| `StandUp()`      | `SetFsmId(4)`               | **does not exist**                     |
| `Squat()`        | `SetFsmId(2)`               | **does not exist**                     |
| `Squat2StandUp()`| **does not exist**          | `SetFsmId(706)`                        |
| `Lie2StandUp()`  | **does not exist**          | `SetFsmId(702)`                        |
| `BalanceStand()` | no argument, `SetBalanceMode(0)` | **requires** a `balance_mode` argument |
| `ContinuousGait()`| `SetBalanceMode(flag?1:0)` | **does not exist**                     |
| 7110/7111        | present (added 2026-02-26)  | added 2026-07-13                       |

The `unitree_ros2` copy vendored on our robot stops at **7107** — no 7110/7111, no
`InternalFsmMode` enum. Our robot's ROS2-side surface is a strict subset of the current SDK
header.

### 2.4 Remote-control bring-up sequences — four published, all different

| Source                                          | Sequence                                                                    | Stated version         |
| ----------------------------------------------- | --------------------------------------------------------------------------- | ---------------------- |
| Official `quick_start` (current)                | L2+B (damp) → L2+UP (prep) → lower rope → **R2+A**                            | none stated            |
| quadruped.de operation guide                    | L1+A (damp) → L1+UP (lock stand) → **R1+X** (Main Operation Control)          | firmware **V1.0.2**    |
| `xr_teleoperate` wiki                           | L2+B → L2+UP → **R1+X** ("1 Dof waist regular mode control program")          | none stated            |
| issue #43 (2025-02)                             | **L1+A**, **L1+UP**                                                           | G1 EDU, fw ≥ v1.3.0    |

Older firmware uses `L1+` combos, current official docs use `L2+`, and the vendor's own
teleop wiki mixes `L2+` combos with `R1+X`. **The operator's remote failure may simply be a
generation mismatch**, not a robot fault.

Sources: <https://support.unitree.com/home/en/G1_developer/quick_start>,
<https://docs.quadruped.de/projects/g1/html/operation_1.2.html>,
<https://github.com/unitreerobotics/xr_teleoperate/wiki/Motion>,
<https://github.com/unitreerobotics/unitree_sdk2_python/issues/43>

### 2.5 Which FSM the remote's "Main Operation Control" targets

| Source                | Claim                                                              |
| --------------------- | ------------------------------------------------------------------ |
| CMU Robotics KB       | Regular Mode sets **501**; Running Mode sets **801**                |
| `xr_teleoperate` wiki | `R1+X` enters "the **1 Dof waist** regular mode control program" (= 500) |

Both cannot be true for the same robot and firmware — almost certainly variant-dependent.
Whichever combo actually moves this robot's `fsm_id` off 4 tells us the exact id to send over
RPC.

### 2.6 Debug mode exit

| Source                          | Exit method                                                     |
| ------------------------------- | ---------------------------------------------------------------- |
| Official `quick_start`          | **L2+UP** to re-enter ready mode                                  |
| issue #43 (jliphard, 2025-02-13)| "the only good way to leave debug mode is to **reboot** the G1"; re-enabling `ai_sport` in the app "remains greyed out while in debug mode" |

Likely a firmware-generation difference. Test L2+UP first; keep reboot as the fallback.

### 2.7 The 7404 polarity — three sources, three different rules

This one matters because **our bridge currently enforces one of the three**.

| Source                                        | Rule                                                                                                      | Grade |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ----- |
| Official arm-action error table               | `7404` = "Current FsmID cannot trigger this action." Remark: *"Some actions cannot be triggered under walking/running motion control."* — i.e. gating **blocks during** locomotion | B |
| Vendor header comment (`g1_arm_action_error.hpp`) | *"The actions are only supported in fsm id {500, 501, 801}"*, and within 801 only fsm_mode {0, 3} — i.e. gating **requires** locomotion | A |
| legion1581/unitree_ui                         | Gestures require a locomotion-active state, **plus** four gestures (hug, heart, both-hands-up, single-hand-up) are hidden specifically in Run | E |

Two Unitree-authored sources state **opposite polarities**, and that genuinely matters for
which FSM we should be driving toward. Our `g1_protocol.py` `is_locomotion_state()` encodes
the unitree_ui version — the E-grade one.

⚠️ **Correction to this document (verified 2026-08-12).** An earlier draft said that gate
might be what is blocking us. It is not, and the distinction matters: `is_locomotion_state()`
is **defined but never called** — `grep` finds it only at its own definition, with no caller
in `_g1_request.py` or `mcp_server.py`. The `wave` that failed went straight to the wire, and
the **firmware** answered `7404 FSM_UNAVAILABLE` after a 0.71 s round trip. There is no
client-side gate to bypass, so no experiment can "prove it is our own code".

What remains true and worth resolving is the polarity question itself, since it decides
whether a gesture-capable state is one we are trying to *enter* or one we are trying to
*avoid*. That still wants the zero-motion probe in §5.

Sources: <https://support.unitree.com/home/en/G1_developer/arm_action_interface>,
<https://github.com/unitreerobotics/unitree_sdk2/blob/main/include/unitree/robot/g1/arm/g1_arm_action_error.hpp>

### 2.8 Gesture indices

| Index  | Official docs (15-entry table)     | C++ `action_map`                  | Python `action_map` | unitree_ui |
| ------ | ---------------------------------- | --------------------------------- | ------------------- | ---------- |
| **11** | Double Hand Flying Kiss            | "two-hand kiss"                   | "two-hand kiss"     | absent     |
| **12** | Single Hand Flying Kiss            | "left kiss" **and** "right kiss"  | "left kiss"         | present    |
| **13** | **absent**                         | **absent**                        | **"right kiss"**    | absent     |
| **36** | **absent**                         | **absent**                        | **absent**          | "forwardPush" |

The C++ map contains an outright bug: `{"left kiss",12}` and `{"right kiss",12}` are both
inserted into a `std::map`, so the second is silently discarded and `ExecuteAction("right
kiss")` resolves to 12. Three Unitree-authored sources, three different answers on 13. **Do
not pick one — probe the robot** (§5).

Our `Gesture.FORWARD_PUSH = 36`, used by `point_at`, has **no backing in any Unitree source
at all** — its only provenance is a decompiled Android app. `point_at` is on materially
weaker ground than `wave`, `hug` or `clap`.

### 2.9 Audio service payloads

| Call                | C++ SDK sends                       | Python SDK sends |
| ------------------- | ----------------------------------- | ---------------- |
| `SetVolume` (1006)  | `{"name":"volume","value":N}`       | `{"volume":N}`   |
| `PlayStop` (1004)   | header builds `{"app_name": …}`     | Unitree's own example passes the **stream_id** into that slot |

Also: the Python `AudioClient` has a real bug — `self.tts_index += self.tts_index` with
`tts_index = 0` never increments, so every Python TTS request goes out with `index=0`. The
C++ does `json.index = tts_index++`. If the firmware dedupes on index, repeated Python TTS
calls would silently stop after the first. The 0–100 volume range is asserted only by an
AI-generated wiki, never by a header.

### 2.10 Dex3-1 hand

| Point                          | Reading A                                                    | Reading B                                                          |
| ------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------------ |
| Right-hand IDL joint order     | Official docs: index 3/4 = `middle_0`/`middle_1` for **both** hands | `xr_teleoperate`: index 3/4 = `Index0`/`Index1` on the **right** hand |
| Thumb joint 1 limit            | SDK arrays: 0.724 rad (L) / 0.742 rad (R) ≈ 41.5° / 42.5°     | Product page: **35°** (0.611 rad)                                   |
| Tactile message shape          | Official IDL: `uint16 data[12]` + `uint8 temp`               | `unitree_ros2` `.msg`: `float32[12] pressure` + `float32[12] temperature` |
| Tactile array count            | Official doc text: "3x4 array at each fingertip (**6** locations)" | Product page: "33 sensors" across "**9** arrays" (matches `SENSOR_MAX = 9`) |
| Tactile scaling                | Official doc: "Valid data ≥ **100000**"                       | The field is `unsigned short` — max 65535. **Unrepresentable.**     |
| State topic                    | Official docs + `xr_teleoperate` + sim: `rt/dex3/*/state`     | C++ example subscribes `rt/lf/dex3/*/state`                         |

Getting the joint order backwards means commanding the wrong finger. Do not hard-code a
tactile parser from any single source.

### 2.11 Sensors

| Point                       | Reading A                                                        | Reading B                                                       |
| --------------------------- | ---------------------------------------------------------------- | --------------------------------------------------------------- |
| Mid-360 IP on a G1          | Weston Robot integrator guide: **192.168.123.20**                 | deepglint (and our robot): **192.168.123.120**                   |
| Livox host port offset      | Shipped `MID360_config.json`: host ports **+1** (56101/56301/…)   | issue #176: had to set host ports **equal** to LiDAR ports to get any data |
| Livox port map              | Livox wiki + shipped config: 56100 cmd / 56200 push / 56300 points / 56400 IMU / 56500 log | A circulating search summary shifts everything by one slot — **wrong**, flagged because it is out there |
| RealSense exclusivity       | Vendor `rs400_support.md`: exclusivity is **per endpoint**; Depth/Color/Motion are independent | Widespread forum wisdom: "only one process can use a RealSense at a time" |
| teleimager head camera      | Shipped YAML: `type: uvc`, `[480,1280]`, `binocular: true`        | Maintainer replies in #218/#145: a RealSense **D435i**            |

The Mid-360 address is almost certainly **unit-specific**: the manual states the sensor ships
as `192.168.1.1XX` where XX is the last two digits of the serial. Any hardcoded `.120` is a
per-unit assumption, not a G1 constant.

---

## 3. Where the web disagrees with our own robot

**In every row below, the robot wins.** These are recorded so that nobody re-derives the web
claim later and "corrects" a verified observation.

| Web claim                                                                                          | Our record                                                                                       | Ruling |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------ |
| Arm actions "only supported in fsm id {500, 501, 801}" (vendor header comment, grade A)             | Inventory §3 [live]: a 7106 wave **succeeded**, `code=0`, arm visibly moved, at `fsm_id=802`      | **Robot.** Either 802 belongs in that set, or our gesture went via the `arm` service's `EXECUTE_ACTION`, which that `loco`-side comment does not govern. Worth disambiguating, but the observation stands |
| "200 = Start / High-level locomotion active" (GetSoloTech, issue #104)                              | Inventory §3 [src]: the vendor header **on our robot** is `unitree_ros2/…/g1_loco_client.hpp`     | **Robot.** Our own copy is the authority on what our SDK sends |
| Mid-360 at `192.168.123.20` (Weston Robot)                                                          | Inventory §1 [live]: **`192.168.123.120`**                                                       | **Robot** |
| arXiv G1 teardown: `eth0 192.168.123.161` on the host that also owns `/dev/video0-5` and `master_service` | Inventory §1 [live]: `.161` is the **control board, no SSH**; the Jetson is `.164`             | **Robot.** Most likely batch variation — which is itself the lesson: G1 internals are not uniform across units |
| `video_hub` "(disabled)" in the arXiv service table; issue #299's robot runs **both** head and chest nodes | Inventory §4 [live]: `videohub_pc4_chest` **is** running (against a `/dev/video10` that no longer exists); head `videohub_pc4` is **not** running | **Robot.** Three robots, three configurations. `videohub_pc4_chest /dev/video10` is the stock arrangement, so ours is a device fault, not a misconfiguration |
| No source relates `mode_machine` to `fsm_id`                                                        | Inventory §3 + §6 [live]: `mode_machine=5` at `fsm_id=802` **and** at `fsm_id=0`                 | **Robot.** They are independent fields. Never label one with the other |
| Search summaries describe raw audio "flowing through `rt/audio_msg`"                                 | Inventory §5 [live]: `gemm-ai` subscribes `rt/audio_msg`, the **ASR text** topic; vendor source shows raw PCM goes over UDP multicast | **Robot + vendor source agree; the summary is wrong.** Do not size buffers or QoS as if `rt/audio_msg` carried audio |

### Our own code, where the web says we are wrong

These are **not** robot observations — they are claims about `apps/bridge` that the web
disputes. Flagged here because they are cheap to check and one of them may be part of the
blocker. They belong in a code review, not in the inventory.

| Our code                                                                  | What the web says                                                                                     |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `is_locomotion_state()` names `{500, 501, 801, 802}` as gesture-capable      | The official 7404 remark says the polarity is the **opposite** (§2.7). Note it gates nothing — it is dead code, never called; the 7404 we saw came from firmware |
| `Gesture.FORWARD_PUSH = 36` (used by `point_at`)                          | Absent from official docs, the C++ map and the Python map. Only source is a decompiled APK               |
| `Mode.PREPARATION = 4`                                                     | Official table: "Lock Standing". C++ header: `StandUp()`. Three names for one id                        |
| `dex_left_cmd = "rt/api/dex3/left/request"`                               | Four independent sources say `rt/dex3/left/cmd` with a raw `HandCmd_` — **no api_id, no JSON envelope**. The hands are not an RPC service |
| `Mode.RUN = 801  # 802 also observed`                                      | Consistent with the one official remark, but 802 is otherwise undocumented. Inventory §3 already flags the label as suspect |
| No `7401` recovery handling anywhere                                       | Official docs: after a **sustained** pose (15, 20, 21…), the next action returns `7401` until you send 99 or repeat the same id. Without this, a second gesture after a hold looks like an unexplained failure |

---

## 4. Reference material by area

### 4.1 The loco / `sport` service

Service name is **`sport`**, API version `1.0.0.0`, topics `rt/api/sport/request` /
`…/response`. It was renamed from `"loco"` to `"sport"` on **2025-06-13** (`331352d`, which
also added 7107); H1 still uses `"loco"` today. Any pre-mid-2025 material naming the G1
service `loco` is stale.

| api_id | Call                    | Request body                                    | Confidence |
| ------ | ----------------------- | ----------------------------------------------- | ---------- |
| 7001   | `GET_FSM_ID`            | `""` (C++) / `"{}"` (Python) — both accepted     | A          |
| 7002   | `GET_FSM_MODE`          | as above                                        | A          |
| 7003   | `GET_BALANCE_MODE`      | as above                                        | A          |
| 7004   | `GET_SWING_HEIGHT`      | as above                                        | A          |
| 7005   | `GET_STAND_HEIGHT`      | as above                                        | A          |
| 7006   | `GET_PHASE`             | as above — marked `// deprecated`, returns `{"data":[floats]}` | A |
| 7101   | `SET_FSM_ID`            | `{"data": <int>}`                               | A          |
| 7102   | `SET_BALANCE_MODE`      | `{"data": <int>}`                               | A          |
| 7103   | `SET_SWING_HEIGHT`      | `{"data": <float>}`                             | A          |
| 7104   | `SET_STAND_HEIGHT`      | `{"data": <float>}`                             | A          |
| 7105   | `SET_VELOCITY`          | `{"velocity":[vx,vy,omega],"duration":<float>}` | A          |
| 7106   | `SET_ARM_TASK`          | `{"data": <int>}` — ids **0–3 only** on this service | A     |
| 7107   | `SET_SPEED_MODE`        | `{"data": <int>}` — range 0–3, roughly 1.0–3.0 m/s (B) | A/B  |
| 7110   | `SWITCH_TO_USER_CTRL`   | added 2026-02-26; **absent** from the `unitree_ros2` copy on our robot | A |
| 7111   | `SWITCH_TO_INTERNAL_CTRL` | `InternalFsmMode` LAST / PASSIVE / WALKRUN — **no numeric values published** | A |

There is **no 7008, 7108 or 7109 for G1** — those are H2 (`7007 GET_ARM_SDK_STATUS`,
`7008 GET_AVAILABLE_FSM_IDS`, `7108 SET_PUNCH_API`, `7109 SET_ARM_SDK_STATUS`). Since G1 and
H2 share the service name and api space, probing 7007/7008 on our G1 is worthwhile (§5).

**No client in any vendor repo parses a response body for 7105 or any setter.** Only the
int32 return code is used. The setter response payload is undocumented.

#### Error codes

| Code | Meaning                              | Scope                                       |
| ---- | ------------------------------------ | ------------------------------------------- |
| 0    | Success                              | generic                                     |
| 3102 | Send request error                   | generic, **client-side**                    |
| 3103 | Api is not registed                  | generic, **client-side** — fires if you `Call()` an api_id you never registered |
| 3104 | Call api timeout                     | generic, **client-side only** — says nothing about robot state (this is what produced our false gesture failures) |
| 3203 | Api not implement error              | generic, **server-side** — the firmware does not implement that api_id |
| 3204 | Api parameter error                  | generic, server-side                        |
| 7301 | LocoState not available              | `loco`/`sport`                              |
| 7302 | Invalid fsm id                       | `loco`/`sport`                              |
| 7303 | Invalid task id                      | `loco`/`sport`                              |
| 7304 | FSM ID return denied                 | **R1 only** — not declared for G1, so a 7304 here would be undocumented, not impossible |
| 7400 | Topic `rt/armsdk` is occupied        | `arm`                                       |
| 7401 | Arm is holding — send 99 or repeat the same id | `arm`                             |
| 7402 | Invalid action id                    | `arm`                                       |
| 7404 | Invalid fsm id                       | `arm` (**not** a loco code)                 |

7301/7302/7303 are declared with **identical numbers and identical strings** for G1, H2 and
R1. There is no 7403.

#### `SetVelocity` semantics

`SetVelocity(vx, vy, omega, duration = 1.0f)`. `Move(vx,vy,vyaw,continuous)` maps to
`duration = continuous ? 864000.f : 1.f` — **864000 s is exactly 10 days**, used as
"continuous". `Move()`'s default `continous_move_` flag is **false**, and `SwitchMoveMode()`
is a purely client-side latch that changes nothing on the robot. `StopMove()` is just
`SetVelocity(0,0,0)` with the same 1 s duration — not a special stop opcode.

That 1 s default is a **firmware-level deadman** and is stronger than any watchdog we write
in Python — which is exactly the reasoning our colleagues' `cmd_vel_to_loco` already follows
(inventory §5).

**Two things are documented nowhere:**

- **No vendor source states the vx/vy/omega sign or axis convention.** The parameters are
  named and never explained. ROS REP-103 (x forward, y left, yaw CCW) is the near-universal
  default and almost certainly what Unitree used — but that is inference. Measure it.
- **No velocity limit exists in any vendor source.** No clamp, no constant, no range comment
  anywhere under `include/unitree/robot/g1/`. Two numbers get quoted as if they were the
  limit and are not: the ~2 m/s marketing figure, and `unitree_rl_lab`'s training ranges
  (`limit_ranges` vx −0.5…1.0, vy −0.3…0.3, ωz −0.2…0.2), which apply to Unitree's own **RL
  policies over `rt/lowcmd`** — a different control path entirely. Use the RL ranges as a
  conservative **ceiling to stay under**, never as a target, and never reconcile them with
  the 2 m/s figure.

`HighStand()`/`LowStand()` pass `UINT32_MAX`/`0` as sentinels, so 7104 clearly saturates and
the firmware clamps into its own band. The valid range and unit of `stand_height` and
`swing_height` are undocumented.

#### Cross-model numbering

| Model | Service   | Numbering                                                                                      |
| ----- | --------- | ------------------------------------------------------------------------------------------------ |
| G1    | `sport`   | 7001–7006, 7101–7107, 7110, 7111                                                                |
| H2    | `sport`   | same as G1 **plus** 7007, 7008, 7108, 7109; **no** 7110/7111. `Start() = 601`                    |
| R1    | `sport`   | subset — 7001, 7002, 7101, 7105, 7107; adds error 7304                                          |
| H1    | `loco`    | 8xxx, API version `2.0.0.0`. **The +1000 offset breaks at x106**: G1 7106 = SET_ARM_TASK but H1 8106 = SET_PHASE and arm task is 8107. H1 also has odometry apis G1 lacks (8201–8204) |
| Go2   | `sport`   | entirely different — one api_id per behaviour, no fsm_id parameter (1001 DAMP, 1008 MOVE, 2043 BACKFLIP…). **Nothing transfers from Go2 to G1** |
| G1-D  | `agv`     | wheeled variant: 1001 AGV_MOVE, 1002 AGV_HEIGHT_ADJUST. Velocity is `{"vx":…,"vy":…,"vyaw":…}` — named scalars, **no duration field**, so no 1 s deadman on that path |

#### Three routes from high-level to low-level

| Route            | Topic                                         | Prerequisite                                                             |
| ---------------- | --------------------------------------------- | -------------------------------------------------------------------------- |
| Motion switcher  | `rt/lowcmd` after `ReleaseMode()`             | Service `motion_switcher`, 1001 CheckMode / 1002 SelectMode / 1003 ReleaseMode / 1004 SetSilent / 1005 GetSilent. Kills the high-level controller |
| `arm_sdk`        | `rt/arm_sdk`                                  | Arms only; legs stay under the high-level controller. Blend weight at motor index 29 |
| User ctrl (new)  | `rt/user_lowcmd` after 7110                   | Vendor example **exits if `fsm_id != 1`** ("Current fsm is not PASSIVE"), publishes one zeroed `kp=0 kd=1.5` command **before** switching, runs at 50 Hz, clamps joint velocity to 0.5 rad/s |

Confusing them is the most common error in the third-party material. `7110` is **not** a way
to make `Start()` work — official docs warn that the first and last actions of your motion
must be a standard standing posture *"otherwise the robot may lose control"*, and no JSON
shape or numeric `InternalFsmMode` value is published anywhere.

#### Ready-made abort conditions

`include/unitree/robot/g1/common/terminations.hpp` ships vendor-chosen safety predicates —
*"When the function returns true, it is recommended to set the motor to passive mode"*:

| Predicate                 | Threshold                                |
| ------------------------- | ---------------------------------------- |
| `bad_orientation`         | > **1.0 rad** from gravity               |
| `joint_vel_out_of_limit`  | > **10.0 rad/s**                         |
| `ang_vel_out_of_limit`    | gyro > **6.0 rad/s**                     |
| `motor_winding_overheat`  | `temperature()[1]` > **120 °C**          |
| `motor_casing_overheat`   | `temperature()[0]` > **85 °C**           |
| `low_battery`             | `BmsState` soc < **20 %**                |
| `lost_connection`         | `LowState_` stale > **1000 ms**          |

The `lost_connection` docstring explains why it matters: *"a loose network cable may cause the
connection to be interrupted. If the program continues to run at this time, it will send a
step signal to the motors, causing violent movement."* Adopting this set wholesale is better
than inventing our own. Note `motor.temperature()` is a 2-element array — `[0]` casing,
`[1]` winding.

Also useful: *"Damping mode, as the ultimate fallback, can always be activated."* And a
precedent for silent refusal — *"During continuous stepping in climbing mode, switching to
any mode other than damping mode is not allowed"* — though the docs never say **which return
code** such a refusal produces. That gap is the heart of §1.

Official startup preconditions are all mechanical, not software: suspend on a rack before
power-on, ~1 min boot, initialisation complete when the ankle reaches its limit position
(audible), then wait a further ~30 s. **No source anywhere documents a battery-percentage
lockout, a zeroing/calibration prerequisite, or a soft-estop latch** — their absence online
is not proof they do not exist.

### 4.2 Arms, gestures, hands

**Two services, two `7106`s, disjoint id spaces.**

| Service | Name    | API version | 7106                | 7107               |
| ------- | ------- | ----------- | ------------------- | ------------------ |
| loco    | `sport` | `1.0.0.0`   | `SET_ARM_TASK`, ids **0–3** (`WaveHand`→0/1, `ShakeHand`→2/3) | `SET_SPEED_MODE`   |
| arm     | `arm`   | `1.0.0.14`  | `EXECUTE_ACTION`, ids **11–27, 99** | `GET_ACTION_LIST`  |

Also on the arm service: `7108 EXECUTE_CUSTOM_ACTION`, `7113 STOP_CUSTOM_ACTION`. Sending
7107 to the wrong service is a silent category error. Our bridge's `Gesture` enum matches the
**arm** service map, so our gestures go via `rt/api/arm/request` — worth confirming in the
log, since the `{500,501,801}` restriction quoted in §2.7 is a comment on the **arm** error
header.

Official 15-entry action table (grade **B**, page undated, no firmware stamp), with the SDK's
own names where they differ:

| Id  | Official name                | SDK name       |
| --- | ---------------------------- | -------------- |
| 99  | Recover Initial Arm Pose     | release arm    |
| 11  | Double Hand Flying Kiss      | two-hand kiss  |
| 12  | Single Hand Flying Kiss      | left kiss      |
| 15  | Arms Horizontal              | hands up       |
| 17  | Applause                     | clap           |
| 18  | High Five                    | high five      |
| 19  | Hug                          | hug            |
| 20  | Double Hand Heart            | heart          |
| 21  | Single Hand Heart            | right heart    |
| 22  | Double Hand Cross            | **reject**     |
| 23  | Right Hand Horizontal        | right hand up  |
| 24  | Dynamic Light Wave           | **x-ray**      |
| 25  | Wave Hand in Front Chest     | face wave      |
| 26  | Wave Hand High               | high wave      |
| 27  | Handshake                    | shake hand     |

Our enum is **missing 11** entirely and adds an unsourced 36 (§2.8).

**`SportModeState` carries gesture progress we are not using**: `task_id` ("Upper limb
interaction action ID; for details see Arm Action Service Interface" — i.e. in the **arm**
service's id space) and `task_time` ("Unit: seconds, increments from 0 until the action is
completed"). That is a proper completion signal, and it would let us stop depending on the
arm service's slow completion-ack — the thing that produced the false `3104` failures in
inventory §3. Also `fsm_mode`: **0 = Static** (switching allowed), **1 = Dynamic** (most
switches disallowed). Under that definition our `fsm_id=802, fsm_mode=0` reading means
"Run controller engaged, currently static", not "the robot is running".

**`rt/arm_sdk` does not bypass the FSM.** It blends into the running controller:
`executed = motion_cmd*(1-w) + arm_sdk_cmd*w`, with `w` at `motor_cmd[29].q`
(`kNotUsedJoint`), range 0.0–1.0. The legs stay under the high-level controller, which is
still running and still owns the blend. Official docs restrict it to *"Locked Stance,
Movement Control 1 and Movement Control 2"* — which, cross-referencing the same page's mode
table, maps to **4 / 500 / 501**. That last point is the genuinely useful one: **low-level arm
control is documented to work while merely standing, without ever entering a gait**, and it
gives arbitrary poses rather than a fixed catalogue.

Caveats on that path:

- **Ramp the weight, never step it.** *"If there is a gap between the positions of the motion
  control commands and the user commands … suddenly changing the weight value may cause the
  robotic arm to move at high speed."* `xr_teleoperate` ramps down over
  `np.linspace(1, 0, num=101)` at 0.02 s steps (≈2 s). Vendor example gains: `kp=60.0`,
  `kd=1.5`, `control_dt=0.02` (50 Hz), `weight_rate=0.2`.
- `JointIndex`: legs 0–11, waist `kWaistYaw` 12 / `kWaistRoll` 13 / `kWaistPitch` 14, left arm
  15–21, right arm 22–28, `kNotUsedJoint` 29, spares 30–35. **35 slots** — matching the
  `motor_count=35` our bridge already reads live.
- The `mode_machine` of a low-level command must match the value in `rt/lowstate`, and the
  per-motor `mode` must be set to **1**, or commands are silently ignored. Two independent
  reports in `unitree_rl_lab` #44 converge on this; the reporter there saw a clean FSM
  transition log with a completely inert robot.
- Official docs say independent arm development requires disabling a built-in service named
  **`g1_arm_example`** via `RobotStateClient::ServiceSwitch(name, swit, status)` — but the
  only published service-name table is the **quadruped** one and does not contain
  `g1_arm_example`. `ServiceList()` on our robot is the only reliable way to get the name.
- Field report, unanswered: `unitree_sdk2_python` #146 (2026-04-17) — driving `arm_sdk`
  **while walking** made a G1 bend forward at the waist and lose balance, with all three waist
  strategies tried. Argues for testing `arm_sdk` **standing**, not walking.

**Debug mode kills both arm paths.** *"The Arm Action Service Interface relies on the built-in
motion control. After entering debug mode, the built-in motion control exits completely, and
the Arm Action Service becomes invalid."*

**Dex3-1 hands.** 7 DoF per hand (thumb 3, index 2, middle 2), RS485, 24 V. Command topic
`rt/dex3/{left,right}/cmd` carrying `unitree_hg::msg::dds_::HandCmd_` — **a raw motor-command
message, not an RPC service: no api_id, no JSON envelope**. State on
`rt/dex3/{left,right}/state` (our robot shows the `/lf/` variant). Per-motor mode byte
`RIS_Mode_t` packs `id:4 | status:3 | timeout:1` as
`(id & 0x0F) | ((status & 0x07) << 4) | ((timeout & 0x01) << 7)`, status 0 = Lock, 1 = FOC;
**bit 7 is a firmware-level 1 s timeout protection**, the same free-safety pattern as the
`SetVelocity` deadman. `MOTOR_MAX = 7`, `SENSOR_MAX = 9`.

Position limits from the vendor C++ example (misleadingly named `maxTorqueLimits_` but used
to clamp `q`, i.e. radians), mirrored between hands:

```
left  max { 1.05,  1.05,  1.75,  0,     0,     0,     0    }
left  min {-1.05, -0.724, 0,    -1.57, -1.75, -1.57, -1.75 }
right max { 1.05,  0.742, 0,     1.57,  1.75,  1.57,  1.75 }
right min {-1.05, -1.05, -1.75,  0,     0,     0,     0    }
```

Left joints 3–6 are negative-only; right joints 3–6 positive-only. Gains: vendor C++ uses
rotate `kp=0.5 kd=0.1`, grip `kp=1.5 kd=0.1`, stop all-zero; `xr_teleoperate` uses
`kp=1.5 kd=0.2`.

**Nothing anywhere states whether the hands are FSM-gated.** The architecture suggests not —
raw message, no RPC, hence no place for a precondition check or a 74xx error — but that is
inference. The real precondition is probably "is the Dex3 bridge process running", not "what
FSM state are we in". No public repo exists for that bridge (`dex1_1_service`,
`dfx_inspire_service` and `brainco_hand_service` exist; no dex3 equivalent), so on our robot
it is almost certainly a closed vendor binary. Our `[live]` sighting of
`/api/dex3_msg_controller` is the best clue anyone has.

### 4.3 Sensors

**Livox Mid-360** (manual v1.0, 2023-01 — bare sensor, not G1-specific): 360° × −7°…52° FOV,
40 m @10 % / 70 m @80 % reflectivity, 0.1 m blind zone, 200 000 pts/s first return, 10 Hz,
≤2 cm range error @10 m, ≤0.15° angular error, 905 nm Class 1, 6.5 W (peak **14 W** when
self-heating below 0 °C), 9–27 V, 265 g, IP67. **It stops operating automatically above ~80 °C
shell temperature** — a thermal dropout would look exactly like a network fault. Built-in
ICM40609 IMU pushes at 200 Hz by default, at offset x=11.0 mm, y=23.29 mm, z=−44.12 mm inside
the point-cloud frame.

Port map (LiDAR side): 56100 cmd / 56200 push / 56300 points / 56400 IMU / 56500 log, plus
56000 for broadcast discovery. **The shipped config gives host-side ports as +1**
(56101/56201/56301/…), so a `tcpdump` filter on `udp port 56300` may see nothing while 56301
sees everything.

**The single most important risk here:** starting any Livox-SDK2-based driver in its **default
`master_sdk: true` mode writes a new destination IP into the sensor**
(`BuildUpdateMid360LidarCfgRequest` sets `kKeyStateInfoHostIpCfg`,
`kKeyLidarPointDataHostIpCfg`, `kKeyLidarImuHostIpCfg` from your config's host IP). The LiDAR
stores exactly **one** Target Address, and the setting **persists**. So
`ros2 launch livox_ros_driver2 msg_MID360_launch.py` is not a read-only act — if the control
board or the gemm stack currently owns the stream, it goes dark and stays dark after we exit.
Grade **A**, read from source; note nobody *documents* this, the READMEs describe the config
as if it only configured the host.

Escape hatches, documented in Livox-SDK2's README but not in the ROS driver's:
`"master_sdk": false` makes the SDK listen-only (skips the command socket and both
`UpdateLidarCfg` overloads); `"multicast_ip"` fans the stream out to a group. The ROS driver
contains **zero** references to multicast but passes the config path straight into SDK2, whose
parser accepts both README schemas — so this is a two-line JSON edit, not a driver patch. A
slave only receives if someone already configured a multicast group as master.

Driver traps confirmed at source level:

- **Config extrinsics rotate the point cloud but not the IMU.** With `roll: 180` (which
  deepglint reports the G1 needs, since the sensor is mounted upside down), cloud and IMU end
  up in different frames.
- **IMU `frame_id` is hardcoded** to `"livox_frame"`, ignoring the launch parameter the cloud
  honours.
- **IMU linear acceleration is published in `g`, not m/s²** — no 9.8 factor anywhere. Issue
  #157 has been open since 2024-12 with no maintainer response; a commenter confirms
  empirically that a level Mid-360 reads ≈1 on Z.
- `xfer_format=0` PointCloud2 uses a **26-byte packed, unaligned** point layout, and its
  `timestamp` field is a per-point **offset**, not an absolute time. FAST-LIO wants CustomMsg;
  Nav2/RViz want PointCloud2; one node instance cannot serve both.
- On ARM + ROS 2 Humble + CycloneDDS, CustomMsg reportedly sticks at **~5 Hz instead of 10 Hz**
  (reproduced on Jetson Orin NX and RK3588); the reporter's fix was switching RMW to Zenoh.
  Our Jetson is **Foxy**, so this may not transfer — and the Zenoh workaround is not available
  to us there.
- `bind failed` / `Failed to init livox lidar sdk` on Jetson ARM64 is a recurring,
  never-officially-resolved failure. Every community answer converges on "the `host_ip` in the
  JSON is not an address that exists on this machine" — accumulated folklore, not
  documentation.

**Intel RealSense D435i.** The important correction: **exclusivity is per *endpoint*, not per
device**. Vendor doc: *"Multiple applications can use librealsense2 simultaneously, as long as
no two users try to stream from the same camera endpoint"*, with Depth, Color and Motion as
independent endpoints. So if gemm takes depth+color and we only want colour, we are blocked;
if it leaves an endpoint free, we are not. Collision looks like
`xioctl(VIDIOC_S_FMT) failed … Device or resource busy`. Caveat: that doc is old (RS400-era,
mentions T265 endpoints) and does not say whether D435i depth and IR share an endpoint — treat
as a hypothesis to test.

The IMU is a Bosch BMI055, **not factory-calibrated** (non-zero angular velocity at idle,
gravity ≠ 9.80665), with a depth-to-IMU extrinsic that is precalculated and **cannot be
modified**. On Linux it surfaces through HID/IIO, **not** a `/dev/video` node, so an IMU-only
consumer does not contend for V4L2 at all.

`librealsense` has moved org — `IntelRealSense/librealsense` → `realsenseai/librealsense` —
and dropped the "Intel" prefix from camera visible names. Any code matching on
`"Intel RealSense D435I"` may break across an upgrade. Jetson: choose between the RSUSB
backend (no kernel patching, but *"performance and functional limitations e.g. multi-cam"*)
and the V4L native backend (~30 min kernel patch, needed for depth formats and per-frame
metadata). The verified boards are AGX-class; our Orin **NX** is not on the list, and an OTA
that bumps the L4T kernel silently unloads a hand-patched module. **The gemm container has
already resolved this one way or the other — find out which before changing anything.**

**The G1's own camera pipeline.** Well-evidenced negative: `unitree_sdk2` (HEAD 2026-07-09) and
`unitree_ros2` (HEAD 2026-07-02) contain **no G1 video client and no image IDL of any kind**.
`include/unitree/robot/g1/` holds exactly `agv, arm, audio, common, loco`. The `videohub`
service with `1001 GetImageSample` is **Go2**. There is no published DDS image topic for the
G1 to go looking for, and no RTSP endpoint anywhere.

What does exist, and it matches our fault character-for-character: issue #299 on
`xr_teleoperate` lists

```
master_service.service - LSB: master service init script   (/etc/init.d/master_service)
 ├─ 1201 /unitree/module/master_service/master_service
 ├─ 2288 /unitree/module/video_hub_pc4/videohub_pc4_chest /dev/video10
 └─11162 /unitree/module/video_hub_pc4/videohub_pc4       /dev/video4
```

controlled by an undocumented `sudo /unitree/sbin/mscli {list,stop,remove}service`, with
service definitions in `/unitree/etc/master_service/service/`. So **`videohub_pc4_chest
/dev/video10` is the stock configuration** — our chest node is doing what Unitree ships it to
do, and what changed is that `/dev/video10` stopped existing. Device numbers are enumeration
-order dependent; do not treat them as fixed.

A Unitree maintainer confirms the conflict is by design (*"please find and disable the related
video_hub service process"*) and adds a fact that matters for a vanished device node:
**all G1 cameras hang off one USB-C hub in the neck**, so a hub-level event takes out several
`/dev/video` nodes at once.

The highest-value non-destructive recovery, from `teleimager` #8 — needs no reboot and nobody
at the robot:

```
udevadm info --name=/dev/videoX --query=path       # → PORT_PATH, e.g. 1-2.3
echo 0 | sudo tee /sys/bus/usb/devices/<PORT>/authorized && sleep 1 && \
echo 1 | sudo tee /sys/bus/usb/devices/<PORT>/authorized
```

That issue's own cause was `teleimager` deliberately making `UVCCamera.release()` a no-op and
then SIGKILLing itself, *"leaving the USB device in an unrecoverable state"*. Whether
`videohub_pc4` leaks the same way is unknown — but the sysfs recovery works regardless of
cause.

The vendor's current exposure route is **`teleimager`**: ZeroMQ PUB/SUB (ports 55555–55557)
plus WebRTC H.264/VP8 over HTTPS (60001–60003), config REQ/REP on 60000, run manually on the
Jetson. Its shipped `cam_config_server.yaml` defaults the head camera to a **binocular UVC**
camera (`[480, 1280]`), while maintainer replies assume a **RealSense D435i** — both are real
G1 configurations, and Weston Robot adds a third axis: *"Earlier G1 batches connected [the
D435i] to locomotion computer via internal USB; later batches connect to development
computer."*

### 4.4 Audio and voice

**On-robot TTS is one RPC call**, and this is the strongest single lead in the whole document
for un-stubbing `say`. Service **`voice`**, API version `1.0.0.0`, topics
`rt/api/voice/request` / `…/response`, client constructed with lease/authorisation
**disabled**.

| api_id | Call            | Body                                          |
| ------ | --------------- | --------------------------------------------- |
| 1001   | `TTS`           | `{"index":N,"text":"…","speaker_id":K}`       |
| 1002   | `ASR`           | **registered by both SDKs, wrapped by neither.** Parameter shape unknown |
| 1003   | `START_PLAY`    | `{"app_name":…,"stream_id":…}` **plus a binary PCM body** |
| 1004   | `STOP_PLAY`     | `{"app_name":…}` (but the vendor example passes `stream_id`) |
| 1005   | `GET_VOLUME`    | —                                             |
| 1006   | `SET_VOLUME`    | see §2.9 conflict                             |
| 1010   | `SET_RGB_LED`   | `{"R":r,"G":g,"B":b}`                         |

`speaker_id` 0 = Chinese/auto, 1 = English. English TTS and mic acquisition were added
2025-04-11 with an explicit floor: *"Depend on vui_service >= 2.0.3.5 vui_module >= 2.0.0.3"*.
`TtsMaker` **returns before speech finishes** — the vendor example sleeps 5 s / 8 s after each
call — so size its timeout like `sport`, not like `arm`.

**The microphone is not on the Jetson.** The 4-mic array and 5 W speaker belong to the control
board at `192.168.123.161`, which streams raw **16 kHz mono 16-bit PCM as UDP multicast to
239.168.123.161:5555** — not DDS, not ALSA, not USB. Note that is a **different group** from
the `239.255.0.1` DDS firehose in inventory §1; both can be present at once. Playback goes
back only through `PlayStream`. A third party (Saxion Mechatronics) independently confirms the
same topology on a robot addressed identically to ours (`.161` control board, `.164` Jetson),
and warns CycloneDDS must bind `eth0` not `wlan0` — the same finding as inventory §2.

Four mics physically, one channel on the wire: beamforming/AEC happens on the control board
before we see it, and we get no per-element access.

`rt/audio_msg` is a plain `std_msgs::msg::dds_::String_` published **by** the robot, carrying
ASR text. **Its internal format is documented nowhere** — bare text, JSON with confidence, or
wake-word events, unknown. Sniff it.

**A concrete blocker in our own tree:** our pinned `unitree_sdk2py` commit is
`a7dff75` (2026-05-07). Upstream `d801b12` (2026-05-11, *"add init py"*) is what **added**
`unitree_sdk2py/g1/__init__.py`, `g1/audio/__init__.py`, `g1/loco/__init__.py`,
`g1/arm/__init__.py` and the `b2` files — so before that date those subpackages were skipped
by package discovery and never installed. Our pin is four days too old, which is also the
likely reason `postsync.sh` has to patch `unitree_sdk2py/__init__.py` for the missing `b2`
import. Two ways out: bump the pin past `d801b12` (the `b2` patch may become unnecessary), or
register the `voice` api_ids in `g1_rpc.py` the way we already register `sport` — the latter
works on the current pin, because `rpc/client.py` already has
`_CallRequestWithParamAndBin`.

The G1's only documented light output is that same `1010` RGB LED. There is no expression,
face or eye API, and no brightness control (Go2's `vui` service has brightness; that is a
different service with colliding numbers). **No source anywhere states where on the robot the
LED physically is.** Finding it needs eyes, not code.

`/api/audiohub` and `/api/vui` are Go2 constructs; no evidence either exists on a G1's DDS
domain. But a `vui_service` **process** does exist on G1 (arXiv teardown puts it at
`/unitree/module/vui_service/` with a `chat_go` backend on port 6080, ~1.15 GB RSS,
continuous mic capture) — so a process exists even though the SDK client for it is Go2-only.
That same paper documents persistent telemetry sending audio to external servers without
explicit consent, which is worth knowing before any always-on mic experiment.

**No documented built-in wake word for the G1** could be found anywhere. Absence of a
documented wake word is not evidence there is none — the `vui_service` footprint suggests
otherwise, and `rt/audio_msg` is where its effect would show up.

### 4.5 Community practice

**A complete gantry bring-up script exists** and explicitly handles the hanging case — the
closest thing to a recipe for our exact situation, and the source of candidate 3:

```
Damp() → SetFsmId(4) → ramp SetStandHeight (step 0.02, max 0.5) until fsm_mode==0 and h>0.2
       → BalanceStand(0) → SetStandHeight(h) → Start()
```

with an operator-in-the-loop retry if `fsm_mode` stays at 2 ("feet unloaded"). Its early-out
for "already ready" is `cur_id == 200 and cur_mode != 2`, which dates it to the pre-500 era.
**Grade E** — self-declared LLM-generated, no firmware stated — but it contains
hardware-specific detail (the mode 2 → 0 transition) that is unlikely to be hallucinated
wholesale, and the author reports it working.
<https://github.com/Sentdex/unitree_g1_vibes/blob/main/hanger_boot_sequence.py>

**Closest public symptom matches**, all unresolved and all without a maintainer answer:

| Issue                            | Symptom                                                                      | Value to us |
| -------------------------------- | ------------------------------------------------------------------------------ | ----------- |
| `unitree_rl_lab` #44 (2025-09-01)| FSM logs a clean `FixStand → Velocity` transition; robot completely inert       | The *shape* of our failure. But those are `unitree_rl_lab`'s own FSM names, not the vendor sport FSM — overlap may be superficial. Community answers converge on `mode_machine` mismatch and per-motor `mode != 1` |
| `unitree_sdk2_python` #42 (2025-02-12) | api_id 7106 → `3203` on `/api/loco/request`; remote works, SDK does not  | Different topic and different code from ours. Isolates our success to the `arm` service |
| `unitree_sdk2_python` #104 (2025-09-08) | SDK-initialised stand is **lower** than app-initialised; `HighStand()`/`LowStand()` then do nothing | Strengthens the hypothesis that the app/remote path reaches a **different and more capable** state than `Damp → StandUp → Start` does |
| `unitree_sdk2_python` #146 (2026-04-17) | `arm_sdk` while walking → bends forward at the waist, loses balance      | Test `arm_sdk` standing, not walking |
| `unitree_sdk2_python` #33 / #78  | Whole-RPC-transport failure (`send request error`, `GetVolume: (3102, None)`)   | #78 reports it appearing **after updating to firmware 1.4.0** |

One report claims a team got walking only after inserting `self.Start()` into `Move()`, and
that *"scripts do not work in the robot's debug mode, although according to the docs they
should"*. Published **2024-12-27**, which pre-dates the 8.6.x `ai_sport` era entirely — so it
is consistent with 500 being valid on older builds and superseded on ours. Grade **E**, and
the researcher could not verify the `self.Start()` line in situ.

Independent teams working on G1 audio built their **own** wake word and ASR (openWakeWord +
faster-whisper) and their own TTS (Piper, resampled to 16 kHz, pushed through `PlayStream`) —
bypassing Unitree's on both ends. Their README never says why, so "because the built-in one is
inadequate" is inference. They may simply predate the `vui_service ≥ 2.0.3.5` English support.
Not a reason to skip testing `TtsMaker(speaker_id=1)`.

---

## 5. Experiments to run

One checklist, ordered by risk. **Work top to bottom.** Every tier-0 item is a pure read and
costs nothing but time; the blocker may well be resolved before reaching tier 2.

### Tier 0 — read-only, no state change, no motion

| # | Do                                                                          | Expected                                                | Proves                                                            |
| - | ----------------------------------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------- |
| 1 | Read firmware, `ai_sport`, `vui_service`/`vui_module` versions off the robot (`RobotStateClient::GetPkgVersion`) and record them in `ROBOT-INVENTORY.md` as `[live]` | version strings | Places our robot on the v1.3.0 / v1.4.9 / 8.6.x.x / 2.0.3.5 timelines. **Resolves several §2 conflicts without touching the legs.** Do this first |
| 2 | `RobotStateClient::ServiceList()` — Name / Status / Protect per service       | a service table                                          | Whether `g1_arm_example` exists and is on (the `arm_sdk` precondition); what bridges the Dex3 RS485 bus; whether `video_hub` entries match issue #299 |
| 3 | Sweep **all** sport getters in the current posture: 7001, 7002, 7003, 7004, 7005, 7006 | a full snapshot                              | Which getters are gated on "LocoState" (7003 gave 7301 once) and which are always available. 7004/7005 reveal the **units** of swing/stand height before we ever set one |
| 4 | Probe sport **7008** `GET_AVAILABLE_FSM_IDS`, then **7007** `GET_ARM_SDK_STATUS` | a list, or `3203`                                       | If it answers: the authoritative FSM table for this firmware, retiring §2.1 entirely. If `3203`: learned cleanly at zero risk |
| 5 | Send `7101` with `{"data": 99999}`                                            | `7302 Invalid fsm id`                                    | The firmware **rejects** unknown ids rather than ignoring them — which makes our `code=0` on 500 mean 500 is *known*. Calibrates the whole error channel. Then repeat with `{"data": 601}` (H2's Start) to confirm G1 rejects H2 numbers |
| 6 | Poll **7002 alone** while hanging, then with the feet taking weight            | 0 or 1 both times, **or** a 2                            | Kills or promotes candidate 3 for free. No vendor source documents an `fsm_mode` of 2 |
| 7 | `motion_switcher` **1001 `CheckMode`**                                        | `{form, name}`, or service absent                        | Whether `ai_sport` is held, released, or owned by something else. **Never** call `SelectMode`/`ReleaseMode` before this |
| 8 | Arm service **7107 `GET_ACTION_LIST`** on `rt/api/arm/request`, empty parameter | `[[presets],[recorded]]`                                | Settles §2.8 in one shot: whether 11, 13 and 36 exist, and whether there are undocumented actions. Size the timeout generously — the arm service is the slow one |
| 9 | Log which topic our 7106 requests actually go to                              | `rt/api/arm/request`                                     | Whether the `{500,501,801}` restriction in §2.7 even governs our gesture path |
| 10 | Subscribe `rt/lf/sportmodestate` and log `fsm_id`, `fsm_mode`, `task_id`, `task_time` together | populated fields                     | Real gesture progress (retiring the false-3104 problem), and reframes 802 via `fsm_mode` 0=Static |
| 11 | Subscribe `rt/audio_msg` while someone speaks English and Chinese nearby      | raw payloads                                             | Whether built-in ASR runs, what the string contains, which language, whether wake-word events appear. Additive — gemm-ai already subscribes |
| 12 | Join UDP multicast `239.168.123.161:5555` on eth0, capture 5 s to WAV          | 16 kHz mono int16 audio                                  | The mic path exists on **this** robot, pre-mixed to one channel. Nothing arriving points at the `vui_service` version floor |
| 13 | Voice **1005 `GET_VOLUME`**                                                   | `code=0` plus a body                                     | The `voice` service answers here at all — and the body's shape settles the §2.9 C++/Python conflict |
| 14 | `tcpdump -ni eth0 'udp and src host 192.168.123.120'`, read destinations       | dst `.164`, `.161`, or a `224.x` group                   | **Do this before any Livox driver starts.** Determines whether bringing one up is safe or steals the stream |
| 15 | Subscribe `rt/utlidar/cloud` read-only via the 0.10.2 Python bindings           | data, or nothing                                          | Whether the G1 already republishes the cloud on DDS — if so, no Livox driver, no config write, no risk at all |
| 16 | Enumerate DDS services: does `/api/loco/*` exist alongside `/api/sport/*`? Does a `motion_switcher` service exist? | a topic list                     | Whether the same api_id routes to two implementations here (candidate 7) |
| 17 | `sudo /unitree/sbin/mscli listservice`; `systemctl status master_service`; `ls /unitree/etc/master_service/service/` | a service tree                    | Whether the head `videohub_pc4` is absent because it was removed, crashed, or lost its device |
| 18 | `ls -l /dev/video*`; `v4l2-ctl --list-devices`; `lsusb -t`; `fuser -v` each node; `dmesg -T \| grep -iE 'usb\|uvc\|xhci'` | device + holder map              | Whether `/dev/video10` vanished at the hub level (all cameras share one neck hub) |
| 19 | Identify the hardware variant: 23-DoF vs 29-DoF, waist 3-DoF vs locked; check the Explore app's waist-lock setting | a variant answer                | Which of 500/501 is even applicable — the premise candidates 1 and 4 both rest on |

### Tier 1 — low risk: state changes, but no locomotion

| #  | Do                                                                                                             | Expected                                       | Proves                                                     |
| -- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------ |
| 20 | Send arm action **99** ("Recover Initial Arm Pose") in whatever state the robot is in. No gate to bypass — `is_locomotion_state()` is never called (§2.7) | `0`, or `7404`, or `7400`, or `7402` | Settles the **7404 polarity** (§2.7): a `0` from a non-locomotion state means gestures are blocked *during* locomotion, not gated behind it — which would redirect the whole FSM effort. 99 only returns the arms toward neutral, the safest action in the catalogue |
| 21 | Send a deliberately bogus arm action id (e.g. 250)                                                              | `7402 Action ID does not exist`                 | Calibrates the arm error channel before probing the disputed ids |
| 22 | Send sport-service 7106 with `{"data": 250}` on `rt/api/sport/request`; then try `/api/loco/request`             | `3203` on one or both                            | Whether the legacy sport-side `SET_ARM_TASK` is still implemented here, reproducing issue #42 without commanding a real gesture |
| 23 | **`arm_sdk` zero-authority test.** Read current joint positions from lowstate; publish `LowCmd_` on `rt/arm_sdk` at 50 Hz with `motor_cmd[15..28].q` = those **same measured values**, `kp=60`, `kd=1.5`, `motor_cmd[29].q = 0.0` | **nothing moves** — weight 0 gives our command zero authority by construction | The topic exists, nothing else holds it, and the robot's behaviour is unchanged. Log `fsm_id` throughout |
| 24 | **Dex3 no-op write.** `HandCmd_` on `rt/dex3/left/cmd`, 7 entries, mode byte `id=i, status=0 (Lock), timeout=1`, all of `q/dq/tau/kp/kd` zero | accepted, `error[]` clear, no finger motion | The command topic name (correcting our `rt/api/dex3/left/request`) and the message shape, without commanding a finger. **Left hand alone first** |
| 25 | Voice: `SetVolume` low, then **1001** `{"index":0,"text":"Hello, this is a test","speaker_id":1}`; repeat with `speaker_id:0` and Chinese | audible speech                       | `say` can stop being a stub. Needs a human present — the robot makes noise |
| 26 | Send three TTS calls with `index` stuck at 0, then three with `index` incrementing                              | both speak three times                          | Whether the firmware dedupes on `index` — i.e. whether the Python SDK's `tts_index` bug would make TTS "randomly stop working" |
| 27 | `LedControl` `{0,255,0}` then `{0,0,255}` with someone watching the **whole** robot, then reset to `{0,0,0}`     | a visible LED, somewhere                        | Where the LED physically is. No source anywhere documents it |
| 28 | Repeat voice `GET_VOLUME` + a short TTS in a parked posture and again in a standing posture                     | identical behaviour                             | Audio is FSM-ungated — which makes speech a safe acknowledgement channel even when motion is refused |
| 29 | Probe voice **1002 (ASR)** with `{}`, after experiment 11                                                        | a code, maybe a body                             | The most likely candidate for an ASR enable/disable or language switch. Watch whether `rt/audio_msg` behaviour changes |
| 30 | USB port de-authorize/re-authorize cycle for the missing chest camera node (§4.3)                               | `/dev/video10` returns                           | Recovers a leaked USB device with no reboot and nobody at the robot. Someone should watch it, but it touches no motion path |
| 31 | With gemm's `realsense2_camera_node` up, run `rs-enumerate-devices`, then a pyrealsense2 pipeline requesting **colour only** | success, or `Device or resource busy`  | Whether per-endpoint sharing holds for this camera. **Coordinate with the gemm owner first** |

### Tier 2 — gantry, weight supported, operator present, `Damp()` ready

| #  | Do                                                                                                                       | Expected                                   | Proves                                                                 |
| -- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------ |
| 32 | Wire the `terminations.hpp` thresholds (§4.1) as **read-only monitors** and confirm they never false-trigger while the robot merely stands | silence                     | An abort set we did not have to invent, validated before it is needed    |
| 33 | With the operator driving from the hand controller through a full bring-up and back, log `fsm_id` + `fsm_mode` continuously | a state trace                              | The **real** FSM map for this firmware, the 802 label, and the exact id the remote's own "Main Operation Control" targets — which we can then send over RPC. Our software commands nothing |
| 34 | Establish the remote generation: try `L2+B → L2+UP → R2+A`, and separately `L1+A → L1+UP → R1+X`                          | one of them moves `fsm_id` off 4            | Whether the operator's remote failure was a §2.4 generation mismatch     |
| 35 | Press **L2+UP** (documented debug-mode exit), then retry the FSM targets                                                 | a transition, or nothing                    | The "silently in debug mode" latch. If nothing, a power cycle is the next rung — issue #43 claims only a reboot clears it |
| 36 | From standing at 4: `SetFsmId(802)`, poll 7001 (1–2 s settle). Separate runs for `801`, `501`, and `200`                   | one takes                                   | **Candidate 1 / 4 / 5.** Return to a known state between attempts rather than stacking failed transitions |
| 37 | Run the **vendor's own** sequence instead of ours: `Damp()` → sleep 0.5 s → `SetFsmId(706)` → poll 7001                    | the robot stands, `fsm_id` ≠ 4              | **Candidate 2.** 706 is a toggle — read 7001 before **and** after         |
| 38 | At fsm 4, drive the arms over `rt/arm_sdk` (weight ramped 0→0.05 over 5 s, hold, ramp back; setpoints pinned to measured position) | arms hold still, then respond   | **The hypothesis-splitter.** Arms move → `ai_sport` is alive and this is purely FSM-target selection. Arms dead → authority is genuinely gone. Ramp, never step |
| 39 | Once some state takes: `SetVelocity(0,0,0,1.0)` first, **then** a very small `vx` with the firmware's default 1 s duration | legs cycle briefly                          | Which FSM id accepts velocity — the open question in inventory §6         |
| 40 | Send exactly **one** 7105 with `duration=1.0` and time how long the legs keep cycling                                     | stop after ~1 s                             | The firmware deadman empirically. **Do this before ever sending `duration=864000`**, which should be treated as prohibited until the 1 s behaviour is proven |
| 41 | Four separate single-shot 1 s commands: `[+0.1,0,0]`, `[0,+0.1,0]`, `[0,0,+0.1]`, and one negative                        | measurable directions                       | The sign convention. **Nothing in any vendor document tells us this** — REP-103 is only an educated guess |
| 42 | Ramp `vx` over 0.1 / 0.2 / 0.3 m/s, measuring achieved vs commanded                                                       | a scale factor                              | Real velocity scaling. Our sim gains are fitted to a policy running at 10–15 % of commanded and will not transfer. Stay under the RL `limit_ranges` as a **ceiling**, not a target |
| 43 | Send arm action **15** (a sustained pose), then immediately **26**; then send **99** and retry 26                          | `7401` on the second, success after 99       | The undocumented recovery rule we do not implement. If confirmed, the skills layer must auto-send 99 after any sustained gesture |
| 44 | Dex3 single-finger identification: one index at a time (start at index 3), small delta from measured `q`, `kp=0.5 kd=0.1`, timeout bit set, clamped to the per-hand arrays | a specific finger moves | Resolves the §2.10 left/right joint-order contradiction empirically. Remember the arrays are mirrored |

### Tier 3 — last resort, do not use as a workaround

| #  | Do                                                            | Why it is last                                                                                                |
| -- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| 45 | Livox driver with `"master_sdk": false` (listen-only)          | Safe by construction, but only informative once experiment 14 says the LiDAR is **not** already pointed at us. Watch the log for `set master/slave sdk to slave sdk` |
| 46 | Livox driver in **stock (master) mode**                        | **Destructive.** Persistently rewrites the sensor's destination. Record the current destination first so it can be restored, and get the gemm owner's agreement. The safe alternative in the same window is `"multicast_ip": "224.1.1.5"` so both stacks can join |
| 47 | `7110 SwitchToUserCtrl`                                        | Hands the legs to our own controller over `rt/user_lowcmd`. Official warning: first and last actions must be a standard standing posture *"otherwise the robot may lose control"*. No published JSON shape, no numeric enum. Only attempt with a working `rt/user_lowcmd` controller already written and `7111 SwitchToInternalCtrl(PASSIVE)` wired to an abort. **Not a fix for this bug** |
| 48 | `motion_switcher` `ReleaseMode()`                              | Drops the high-level controller entirely. Whatever takes over must publish at 50 Hz or the robot drops. Never unattended |

---

## 6. Dead ends

Recorded so nobody repeats the search. "Not documented online" is itself information.

**Sources that could not be read**

- **`support.unitree.com` is a JavaScript SPA** and returns only a nav shell to normal
  fetches. The only content recovered came through a text-render proxy, and even that was
  flaky — retrying the same URL sometimes flipped a nav-only result into full content, so
  **retry before concluding a page is empty**. Slugs that yielded content:
  `/home/en/G1_developer/{sport_services_interface, arm_action_interface, dexterous_hand,
  arm_control_routine, quick_start}` and `/home/en/developer/RobotStateClient`. Slugs that did
  not: `RobotStateClient_Service`, `device_state_service_interface`,
  `arm_action_service_interface`, `G1_Dex3-1_Dexterous_Hand`, `upper_limb_control`, the
  `VuiClient_Service` page under the G1 tree, and every `zh/` variant.
- One researcher got **HTTP 567 "Access Restricted"** from a real headless browser for the
  same G1 pages — geo- or bot-blocking. **This matters**: Unitree's own header
  (`g1_arm_action_error.hpp`) links to that page's `#Expert interface` anchor as the normative
  reference for FSM-id restrictions. The document Unitree treats as authoritative on the FSM
  table is the one we could least reliably read.
- **Wayback has a snapshot of it that is useless** — the SPA archived as an empty shell,
  `document.body.innerText.length == 0`, 3 KB of DOM.
- **Intel's D400-series datasheet is 403 Forbidden** from both `intel.com` and the Mouser
  mirror, as is the D435i SKU spec page. So the depth resolution/frame-rate matrix, MinZ, and
  the "Simultaneous Image Streams" bandwidth table are unavailable. Treat 1280×720@30 depth,
  848×480@90 and 86°×57° depth FOV as **unconfirmed reseller repetition**.
  `rs-enumerate-devices` on our own camera is a better source and costs nothing.
- The Mid-360 FAQ renders its answers behind click-to-expand; question 6 (whether two
  Mid-360s in proximity degrade each other) is unanswered here.

**Things that genuinely are not documented anywhere**

| Missing                                                                 | Notes                                                                         |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| **The vx/vy/omega sign and axis convention**                              | Parameters are named and never explained, in any vendor repo                    |
| **Any velocity limit for the loco service**                                | No clamp, no constant, no range comment. The 2 m/s figure is marketing; the `unitree_rl_lab` ranges are a different control path |
| **`SetSpeedMode` (7107) values**                                          | No enum, no example argument, no comment. The vendor CLI just forwards your integer |
| **`swing_height` / `stand_height` units and ranges**                       | Only hint is the `UINT32_MAX`/`0` saturation sentinels                          |
| **Any setter's response shape**, including 7105                            | Every vendor client discards the body; only the int32 code is used              |
| **What `fsm_id` 802 is**                                                  | One remark on one official page. No vendor header, no example, no third-party table. Inventory §3's "run" label remains unsupported |
| **What "Movement Control 1" / "Movement Control 2" are numerically**       | The arm docs use the names and never map them to ids                            |
| **Any relationship between `mode_machine` and `fsm_id`**                    | Corroborates our `[live]` finding that they are independent                     |
| **Whether the Dex3 hands are FSM-gated**                                   | Not in the official page, the SDK examples, `xr_teleoperate`, or any issue. One search summary claimed hands work "including during damping or zero-torque modes" — untraceable, reads as generated, **not counted as evidence** |
| **Numeric joint limits on the official Dex3-1 page**                       | It references the limit arrays but does not print values                        |
| **The G1's `ServiceSwitch` service-name table**                            | The only published list is the **quadruped** one and does not contain `g1_arm_example` |
| **Where the G1's RGB LED physically is**                                   | Not in any vendor, manual or third-party source                                 |
| **Any built-in G1 wake word**                                              | A `vui_service` process exists with continuous mic capture; no trigger phrase, no language, nowhere |
| **Gesture index 36**                                                       | Searched directly and by neighbouring-id patterns. Only the decompiled APK      |
| **Any source for "arm gestures require a locomotion-active state"**         | Originates in `legion1581/unitree_ui` alone; the official 7404 remark points the **other** way |
| **A G1 "Plus" / "PC4" API surface**                                        | Only marketing tiering (23-DoF base vs 29-DoF EDU/EDU-Plus vs EDU Ultimate). The only substantiated SDK variant split is legged-G1 vs wheeled G1-D/AGV |
| **Any G1 image topic, video client, or image IDL**                         | Grepped every IDL and include directory in `unitree_sdk2` (HEAD 2026-07-09) and `unitree_ros2` (HEAD 2026-07-02). A **well-evidenced absence**, not a failed search. `videohub`/`GetImageSample` is Go2 |
| **Any RTSP endpoint for the G1**                                           | Vendor paths are ZeroMQ and WebRTC via `teleimager`                             |
| **What the G1's chest camera physically is**                               | No model, no resolution, no USB VID:PID. The only two references in the whole `unitreerobotics` org are issue #299's process line and an unrelated sim issue. `videohub_pc4_chest` is essentially undocumented |
| **Any report of a G1 camera pipeline breaking after an OTA**                | Searched directly. The closest documented mechanisms are the videohub↔RealSense contention and the USB leak on server shutdown. Our fault may well be one of those rather than OTA damage |
| **Accel/gyro output rates for the D435i**                                   | Absent from `doc/d435i.md`. The quoted 63/250 Hz and 200/400 Hz figures are not in the vendor doc |
| **Any official Unitree Python example for the Dex3**                        | A forum thread asking for exactly this (2025-07-24) was still unanswered on 2025-11-06. Vendor Python exists inside `xr_teleoperate` and `unitree_sim_isaaclab`, but is not presented as a reference and the two disagree with the docs on right-hand joint order |
| **A quotable vendor statement of the Mid-360 single-host constraint**       | It is an inference from the singular "Target Address" field plus the SDK2 master/slave design. Source code corroborates; a one-line vendor statement does not exist |
| **Any official acknowledgement of the Livox IMU units bug**                 | Issue #157 open since 2024-12 with zero maintainer response. The extrinsics-not-applied-to-IMU asymmetry is documented nowhere at all — established only by reading `pub_handler.cpp` |
| **A changelog tying any of this to a specific G1 firmware version**         | The only anchors anywhere are the ones in §0. None of the SDK headers or official pages carry a version stamp, which is exactly why the tables in §2 disagree |

**Threads with no vendor answer** — `unitree_sdk2_python` #42, #43, #104, #146, #33, #78;
`unitree_rl_lab` #44; `livox_ros_driver2` #35, #74, #157, #176, #187. All are community
reports. None should be treated as documentation, and #44 in particular — our closest public
symptom match — was closed without any maintainer explanation.
