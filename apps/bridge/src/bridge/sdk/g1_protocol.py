"""G1 protocol catalogue — modes, gestures, topics, FSM rules.

Reverse-engineered from the legion1581/unitree_ui project (MIT) against
G1 firmware ≥ 1.5.1. Pure data + a few helpers; no SDK or network code.

This module is the single source of truth for:
- Topic names on a real G1 (note the `rt/lf/` prefix difference vs the
  Isaac Sim `unitree_sim_isaaclab` scene, which uses bare `rt/`).
- High-level request shapes: full-body modes via `rt/api/sport/request`
  with `api_id=7101`; upper-limb gestures via `rt/api/arm/request` with
  `api_id=7106`. Both carry parameter `{"data": <index>}`.
- The G1 FSM transition table — the on-robot firmware rejects illegal
  transitions. We keep the rule set here as reference data only — nothing
  enforces it client-side, deliberately; see the note above the frozensets.

Sim vs real topic profile:
    Isaac Sim (today)              Real G1 (≥ 1.5.1)
    -------------                  -----------------
    rt/lowstate                    rt/lf/lowstate
    rt/sim_state                   rt/lf/sportmodestate
    (none)                         rt/lf/bmsstate
    (none)                         rt/lf/secondary_imu
    rt/dex1/{l,r}/state            rt/lf/dex3/{l,r}/state
    rt/lowcmd                      rt/lowcmd  (same)
    rt/run_command/cmd             (none — sim convenience only)
    (none)                         rt/api/sport/request
    (none)                         rt/api/arm/request

The bridge picks a topic profile via `SIM_MODE` (see `Transport` in
`docs/ARCHITECTURE.md` §5, planned).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final, Literal, NamedTuple


# ---------------------------------------------------------------------------
# Topic names
# ---------------------------------------------------------------------------


class Topics(NamedTuple):
    """Topic name profile for one connection target (sim or real)."""

    lowstate: str
    sportmodestate: str
    bmsstate: str | None
    secondary_imu: str | None
    dex_left_state: str | None
    dex_right_state: str | None
    lowcmd: str
    sport_request: str | None
    arm_request: str | None
    dex_left_cmd: str | None
    dex_right_cmd: str | None
    # Sim-only convenience velocity channel; None on real G1.
    run_command: str | None
    # World-frame pose source. Sim gets pose from `sportmodestate` (which is
    # `rt/sim_state`, a JSON blob), so this is None there. On real it's the
    # vendor's odometry estimate — a separate topic with a separate type.
    odom: str | None


# Isaac Sim with unitree_sim_isaaclab — the profile we use today.
SIM_TOPICS: Final[Topics] = Topics(
    lowstate="rt/lowstate",
    sportmodestate="rt/sim_state",
    bmsstate=None,
    secondary_imu=None,
    dex_left_state="rt/dex1/left/state",
    dex_right_state="rt/dex1/right/state",
    lowcmd="rt/lowcmd",
    sport_request=None,
    arm_request=None,
    dex_left_cmd="rt/dex1/left/cmd",
    dex_right_cmd="rt/dex1/right/cmd",
    run_command="rt/run_command/cmd",
    odom=None,  # sim pose comes from rt/sim_state via `sportmodestate`
)

# Real G1 over direct DDS — what `SIM_MODE=real` uses, with the bridge running
# onboard the Jetson. (These names were originally derived from the WebRTC
# topic profile; they match because that interface was always a shim over these
# same DDS topics.)
REAL_TOPICS: Final[Topics] = Topics(
    lowstate="rt/lf/lowstate",
    sportmodestate="rt/lf/sportmodestate",
    bmsstate="rt/lf/bmsstate",
    secondary_imu="rt/lf/secondary_imu",
    dex_left_state="rt/lf/dex3/left/state",
    dex_right_state="rt/lf/dex3/right/state",
    lowcmd="rt/lowcmd",
    sport_request="rt/api/sport/request",
    arm_request="rt/api/arm/request",
    # ⚠️ UNVERIFIED, and probably wrong in shape as well as name. The hands are
    # not an RPC service: every source found on the robot drives them by
    # publishing a raw `HandCmd_` to a plain command topic, with no api_id and
    # no JSON envelope — so an `/api/.../request` name implies a protocol that
    # does not exist here. Worse, the only hand that actually answered was a
    # BrainCo Revo2 on `rt/brainco/right/{cmd,state}`, and no Dex3 driver
    # exists anywhere on the Jetson. Which hands are physically fitted is
    # unresolved (`docs/ROBOT-HARDWARE.md` §6). Do not build on these two
    # names until someone has looked at the wrists.
    dex_left_cmd="rt/api/dex3/left/request",
    dex_right_cmd="rt/api/dex3/right/request",
    run_command=None,
    # Published by the vendor's `ai_odom_node` as unitree_go SportModeState_ —
    # a type this SDK *does* ship, unlike the humanoid sportmodestate. The
    # `rt/state_estimator/*` topics carry the same information as
    # nav_msgs Odometry_, but consuming those would mean hand-writing the ROS
    # IDL. Verified live 2026-08-11: position + imu_state.rpy populated.
    odom="rt/odommodestate",
)


def topics_for(sim_mode: str) -> Topics:
    """Pick the topic profile for the given SIM_MODE env value."""
    if sim_mode == "isaac" or sim_mode == "mujoco_local" or sim_mode == "stub":
        return SIM_TOPICS
    return REAL_TOPICS


# ---------------------------------------------------------------------------
# api_id constants — what goes in the request envelope's identity.api_id
# ---------------------------------------------------------------------------

API_ID_G1_STATE: Final[int] = 7101  # Full-body posture / gait mode
API_ID_G1_UPPER_LIMBS: Final[int] = 7106  # Upper-limb gesture trigger
API_ID_LOCO_SET_VELOCITY: Final[int] = 7105  # Body-frame velocity setpoint
API_ID_LOCO_GET_FSM_ID: Final[int] = 7001  # Current FSM state index
API_ID_LOCO_GET_FSM_MODE: Final[int] = 7002  # Current FSM sub-mode
API_ID_LOCO_SET_BALANCE_MODE: Final[int] = 7102  # Balance controller mode

# Present in the C++ SDK (unitree_sdk2) but in NEITHER the Python SDK nor the
# `unitree_ros2` tree vendored on our robot, which stops at 7107. That is why
# an earlier doc recorded 7110 as [web]-only and "not in this client" —
# we had read the wrong header. Unverified against our firmware: a `3203` reply
# means this build does not implement it, which is a clean, motion-free answer.
API_ID_LOCO_SET_PUNCH: Final[int] = 7108
API_ID_LOCO_SET_ARM_SDK_STATUS: Final[int] = 7109  # Enables the rt/arm_sdk path
API_ID_LOCO_SWITCH_TO_USER_CTRL: Final[int] = 7110
API_ID_LOCO_SWITCH_TO_INTERNAL_CTRL: Final[int] = 7111

# Arm service. Note 7107 here is NOT SET_SPEED_MODE — that is 7107 on the sport
# service. Same number, different service, different call: the scoping trap this
# module keeps warning about, in its sharpest form.
API_ID_ARM_GET_ACTION_LIST: Final[int] = 7107
API_ID_ARM_EXECUTE_CUSTOM_ACTION: Final[int] = 7108
API_ID_ARM_STOP_CUSTOM_ACTION: Final[int] = 7113

# --- motion_switcher service ------------------------------------------------
#
# A DIFFERENT service ("motion_switcher"), so these ids live in their own
# namespace and collide with the sport service's numbers — motion_switcher's
# error codes 7001-7009 are the same integers as sport's api_ids 7001-7006.
# Never compare a bare number across services.
#
# This service decides which motion controller owns the robot, which makes
# CHECK_MODE the single most useful diagnostic we have: an empty `name` means
# no controller is loaded, and in that state the sport service still answers
# `code 0` to everything while doing nothing. "Wrong FSM id" and "nothing
# loaded to act on any id" are otherwise indistinguishable.
# --- voice / audio service ---------------------------------------------------
#
# Service name is "voice", not "audio" — the client class is called AudioClient
# but the DDS service it resolves is `voice`. Getting that wrong yields 3102
# ("no server on that request topic"), which reads like a broken robot rather
# than a typo.
#
# Speech is worth having for a reason beyond politeness: it appears to be
# ungated by the locomotion FSM, so it is a channel the robot still has when
# motion is being refused — which is the situation we keep ending up in.
VOICE_SERVICE: Final[str] = "voice"
VOICE_API_VERSION: Final[str] = "1.0.0.0"
API_ID_VOICE_TTS: Final[int] = 1001
API_ID_VOICE_ASR: Final[int] = 1002
API_ID_VOICE_START_PLAY: Final[int] = 1003
API_ID_VOICE_STOP_PLAY: Final[int] = 1004
API_ID_VOICE_GET_VOLUME: Final[int] = 1005
API_ID_VOICE_SET_VOLUME: Final[int] = 1006
API_ID_VOICE_SET_RGB_LED: Final[int] = 1010


class Speaker:
    """`speaker_id` for TTS. Mixed Chinese/English in one call is unsupported.

    From Unitree's VuiClient page: "speaker_id 0 for Chinese roles and 1 for
    English roles. Mixed Chinese and English modes are not supported."
    """

    CHINESE: Final[int] = 0
    ENGLISH: Final[int] = 1


MOTION_SWITCHER_SERVICE: Final[str] = "motion_switcher"
MOTION_SWITCHER_API_VERSION: Final[str] = "1.0.0.1"
API_ID_MS_CHECK_MODE: Final[int] = 1001  # Getter — safe
API_ID_MS_SELECT_MODE: Final[int] = 1002  # Loads a controller — NOT registered
API_ID_MS_RELEASE_MODE: Final[int] = 1003  # Unloads a controller — NOT registered


class BalanceMode:
    """`SetBalanceMode` values, from the vendor's `g1_loco_client.hpp`.

    `BalanceStand() = SetBalanceMode(0)` and
    `ContinuousGait(flag) = SetBalanceMode(flag ? 1 : 0)` — so 0 is the
    stand-and-balance controller and 1 is continuous gait.
    """

    BALANCE_STAND: Final[int] = 0
    CONTINUOUS_GAIT: Final[int] = 1

# api_ids are scoped *per service*, not globally — 7107 means SET_SPEED_MODE on
# `sport` but something unrelated on `arm`. The full loco surface (from the
# vendor's `g1_loco_client.hpp`; see docs/ROBOT-API.md §2) is:
#
#   7001..7006  GET fsm_id / fsm_mode / balance_mode / swing+stand height / phase
#   7101  SET_FSM_ID        7102  SET_BALANCE_MODE
#   7103  SET_SWING_HEIGHT  7104  SET_STAND_HEIGHT
#   7105  SET_VELOCITY      7106  SET_ARM_TASK      7107  SET_SPEED_MODE
#
# Only the three named above are wired up so far. The getters would give us real
# preconditions (is the FSM actually in a gait mode before we send velocity?)
# and are the obvious next addition.


# ---------------------------------------------------------------------------
# Full-body FSM modes (param={"data": index}, topic=sport_request)
# ---------------------------------------------------------------------------


class Mode(IntEnum):
    """G1 FSM mode indices.

    These are the values published on `rt/lf/sportmodestate.mode` and the
    values you send in `{"data": N}` to `rt/api/sport/request` with
    api_id=7101 to request a transition.
    """

    ZERO_TORQUE = 0
    DAMP = 1
    SQUAT = 2
    SEATING = 3
    # Our names for 4 and 500 are the odd ones out, and it cost us. The vendor
    # header calls them `StandUp()` and `Start()`; the official docs call 4
    # "Lock Standing" and 500 "Walk Motion". Three sources, three names, for
    # ids we send constantly — when reading anyone else's code or docs, match
    # on the NUMBER, never the label.
    #
    # 500 in particular is not a generic "begin": it is one of two walk
    # policies, with 501 the 3-DoF-waist variant. Sent from 4 on this robot it
    # returns code 0 and does nothing (2026-08-13, repeatable, both
    # harness-supported and weight-bearing) — solved: 501 IS this build's walk
    # program, and the robot walked under it on 2026-08-15. See
    # `docs/ROBOT-API.md` §12.
    PREPARATION = 4
    WALK = 500
    WALK_WAIST = 501  # Walk with waist control
    DANCE = 503
    LIE_UP = 702
    SQUAT_UP = 706  # Same index as SQUAT; different semantic role
    RUN = 801  # 802 also observed as Run
    CLIMB = 812


# Human-readable label per mode index (covers the variants too).
MODE_LABEL: Final[dict[int, str]] = {
    Mode.ZERO_TORQUE: "zero_torque",
    Mode.DAMP: "damp",
    Mode.SQUAT: "squat",
    Mode.SEATING: "seating",
    Mode.PREPARATION: "preparation",
    Mode.WALK: "walk",
    Mode.WALK_WAIST: "walk_waist",
    Mode.DANCE: "dance",
    Mode.LIE_UP: "lie_up",
    Mode.SQUAT_UP: "squat_up",
    Mode.RUN: "run",
    # 802: label is SUSPECT. Read live from api_id 7001 on 2026-08-11 while the
    # robot was standing perfectly still — so "run" is very likely wrong, and
    # 802 is probably a general "controller active / main operation" state
    # rather than a gait. Left as-is because guessing a replacement would be no
    # better; resolve it by watching fsm_id transition during a supervised
    # motion window. Don't build preconditions on this label meaning "running".
    802: "run",
    Mode.CLIMB: "climb",
}


def mode_label(mode: int) -> str:
    """Return a human-readable name for a mode_machine / sportmodestate.mode int."""
    return MODE_LABEL.get(mode, f"unknown({mode})")


# ---------------------------------------------------------------------------
# FSM transition rules
# ---------------------------------------------------------------------------
#
# The G1 firmware enforces these rules on `rt/api/sport/request` and rejects
# illegal transitions. Mirroring them client-side avoids round-trips and
# gives skills a deterministic precondition check.
#
# Rules (in evaluation order, matching legion1581/unitree_ui/action-bar.ts):
#   - Not in Damp → can't transition to ZeroTorque / Preparation / SquatUp / LieUp.
#   - In ZeroTorque → only Damp accepted.
#   - In Squat       → only Damp accepted.
#   - In Damp        → only ZeroTorque / Preparation / SquatUp / LieUp.
#   - In Preparation → only Damp / Walk / WalkWaist / Run.
#   - Arm gestures (separate request topic) require a locomotion-active
#     state (Walk / WalkWaist / Run).

_DAMP_TARGETS: Final[frozenset[int]] = frozenset(
    {Mode.ZERO_TORQUE, Mode.PREPARATION, Mode.SQUAT_UP, Mode.LIE_UP}
)
_PREPARATION_TARGETS: Final[frozenset[int]] = frozenset(
    {Mode.DAMP, Mode.WALK, Mode.WALK_WAIST, Mode.RUN}
)

# These two sets are REFERENCE DATA, not a guard. A `can_transition()` helper
# built on them was removed unused: nothing ever called it, while this module's
# docstring and earlier docs claimed "skills check it before firing a
# mode". Documenting a safety check that does not run is worse than having
# neither, particularly on the FSM path that had us blocked until 2026-08-15.
#
# It was not wired up instead, deliberately. The rules below are assembled from
# partly-unverified sources, and a client-side gate encoding a wrong rule would
# refuse a transition the firmware would have accepted — turning a robot problem
# into a bridge problem, precisely when we are trying to tell those apart. The
# firmware rejects illegal transitions itself and says so (7302 Invalid fsm id),
# which is the answer we actually want.
#
# `_PREPARATION_TARGETS` is cited by docs/ROBOT-API.md §12 as evidence in the
# (solved) Start()/fsm_id=500 case, which is why the data stays.


# ---------------------------------------------------------------------------
# Upper-limb gestures (param={"data": index}, topic=arm_request)
# ---------------------------------------------------------------------------


class Gesture(IntEnum):
    """G1 arm-gesture indices for `rt/api/arm/request` with api_id=7106."""

    # THE ROBOT'S OWN TABLE. Read live on 2026-08-15 by calling GetActionList
    # (arm service, api_id 7107), which returns 23 preset actions with their ids,
    # names and gating. This outranks both of our previous sources.
    #
    # It corrects a correction. These names originally came from a decompiled
    # Android app; on 2026-08-14 I replaced them with Unitree's published table,
    # which uses different names and omits several ids entirely. The robot says
    # the APK-derived names were RIGHT — `refuse`, `ultraman_ray`,
    # `right_hand_up`, `both_hands_up` are the firmware's own strings — and that
    # the published table is incomplete for this build. Trust the robot.
    #
    # Gating is per-action and comes in two kinds, neither of which we had:
    #   fsm=[500, 501]        — needs a walk program (only `turn_back_wave`)
    #   mode_machine=[5, 6]   — needs a 29-DoF or 27-DoF body
    # This robot reports mode_machine 5, so every mode_machine-gated action
    # below is available to it. Only ONE action in the whole table is
    # FSM-gated, which reframes error 7404: gesture availability is mostly
    # about which BODY you have, not which state it is in.
    TURN_BACK_WAVE = 1  # fsm=[500, 501]
    BLOW_KISS_BOTH_HANDS = 11
    BLOW_KISS_LEFT_HAND = 12
    BLOW_KISS_RIGHT_HAND = 13
    BOTH_HANDS_UP = 15
    CLAMP = 17
    HIGH_FIVE = 18
    HUG = 19
    HEART_BOTH_HANDS = 20  # mode_machine=[5, 6]
    HEART_RIGHT_HAND = 21  # mode_machine=[5, 6]
    REFUSE = 22
    RIGHT_HAND_UP = 23
    ULTRAMAN_RAY = 24
    WAVE_UNDER_HEAD = 25
    WAVE_ABOVE_HEAD = 26
    SHAKE_HAND = 27
    BOX_LEFT_HAND_WIN = 28  # mode_machine=[5, 6]
    BOX_RIGHT_HAND_WIN = 29  # mode_machine=[5, 6]
    BOX_BOTH_HAND_WIN = 30  # mode_machine=[5, 6]
    RIGHT_HAND_ON_HEART = 33
    BOTH_HANDS_UP_DEVIATE_RIGHT = 34
    FORWARD_PUSH = 36  # mode_machine=[5, 6]
    # 99 both performs "recover initial arm pose" and is the documented escape
    # from error 7401 — after a sustained action the arm latches, and the next
    # action is refused until you send 99 or repeat the same id.
    RELEASE_ARM = 99


# Actions this robot's firmware gates, from GetActionList (2026-08-15).
# `mode_machine` is the body variant (5 = 29-DoF, which is us); `fsm` is a
# required walk program. Everything not listed here is ungated.
ACTION_REQUIRES_FSM: Final[dict[int, tuple[int, ...]]] = {1: (500, 501)}
ACTION_REQUIRES_MODE_MACHINE: Final[dict[int, tuple[int, ...]]] = {
    20: (5, 6),
    21: (5, 6),
    28: (5, 6),
    29: (5, 6),
    30: (5, 6),
    36: (5, 6),
}

# Taught (user-recorded) actions this robot holds, executed BY NAME rather than
# by id through the arm service's string overload. Durations are the firmware's
# own, from GetActionList. Not wired to a skill yet.
TAUGHT_ACTIONS: Final[dict[str, float]] = {
    "Waist_Drum_Dance": 9.5,
    "Scratch_head": 8.1,
    "Spin_discs": 6.9,
    "Throw_money": 8.1,
}


# ---------------------------------------------------------------------------
# Convenience: skill → (api_id, mode/gesture) mapping
# ---------------------------------------------------------------------------
# Used by Phase 1 skills (damp / prepare / wave / point_at / etc.) so each
# skill is a one-liner that resolves to a request.

SkillName = Literal[
    "damp",
    "zero_torque",
    "prepare",
    "start_walking",
    "start_walking_waist",
    "balance_stand",
    "sit_g1",
    "lie_up",
    "squat",
    "wave",
    "point_at",
    "shake_hand",
    "hug",
    "clap",
    "release_arm",
]


class SkillRequest(NamedTuple):
    """The wire shape for a one-shot G1 high-level skill."""

    topic_kind: Literal["sport_request", "arm_request"]
    api_id: int
    data: int

    def param_json(self) -> str:
        return f'{{"data":{self.data}}}'


SKILL_REQUESTS: Final[dict[SkillName, SkillRequest]] = {
    # Full-body postures (api_id=7101)
    "damp":          SkillRequest("sport_request", API_ID_G1_STATE, Mode.DAMP),
    "zero_torque":   SkillRequest("sport_request", API_ID_G1_STATE, Mode.ZERO_TORQUE),
    "prepare":       SkillRequest("sport_request", API_ID_G1_STATE, Mode.PREPARATION),
    "start_walking": SkillRequest("sport_request", API_ID_G1_STATE, Mode.WALK),
    # 500 and 501 are two different walk PROGRAMS selected by waist DoF, not a
    # generic start and a variant of it. Unitree's `basic_services_interface`
    # documents mode_machine as `4:23-Dof; 5:29-Dof; 6:27-Dof`, and this robot
    # reports **5** — so it is a 29-Dof/3-Dof-waist machine and 501 is the
    # program it actually implements. That is why `start_walking` (500)
    # returns rpc code 0 and never transitions out of StandUp — solved: the
    # robot walked under 501 on 2026-08-15 (docs/ROBOT-API.md §12).
    #
    # The official Python LocoClient has no method for 501 at all, which is
    # part of why we keep our own catalogue rather than delegating to it.
    "start_walking_waist": SkillRequest("sport_request", API_ID_G1_STATE, Mode.WALK_WAIST),
    "sit_g1":        SkillRequest("sport_request", API_ID_G1_STATE, Mode.SEATING),
    "lie_up":        SkillRequest("sport_request", API_ID_G1_STATE, Mode.LIE_UP),
    # Mode.SQUAT (2) is never actually sent by the reference implementation
    # (legion1581/unitree_ui) for G1 — both its "Squat" and "Squat-Up" buttons
    # send SQUAT_UP (706). Follow the verified value, not the unverified enum.
    "squat":         SkillRequest("sport_request", API_ID_G1_STATE, Mode.SQUAT_UP),
    # Balance controller (api_id=7102) — NOT a posture, so not 7101. The
    # vendor's `BalanceStand()`. Sent while standing, it engages the
    # stand-and-balance controller; `Start()` (SetFsmId 500) was observed
    # returning code 0 without transitioning out of StandUp on 2026-08-12,
    # and this is the one documented call in that client we had never sent.
    "balance_stand": SkillRequest("sport_request", API_ID_LOCO_SET_BALANCE_MODE,
                                  BalanceMode.BALANCE_STAND),
    # Arm gestures (api_id=7106) — require a locomotion state
    "wave":          SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.WAVE_ABOVE_HEAD),
    # Back to 36. Yesterday I moved this to 23 because Unitree's published
    # table has no 36 — but the ROBOT's own GetActionList does: id 36,
    # `forward_push`, gated on mode_machine [5, 6], and this robot is 5. The
    # published table is incomplete for this build. Still never executed here.
    "point_at":      SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.FORWARD_PUSH),
    "shake_hand":    SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.SHAKE_HAND),
    "hug":           SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.HUG),
    "clap":          SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.CLAMP),
    "release_arm":   SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.RELEASE_ARM),
}
