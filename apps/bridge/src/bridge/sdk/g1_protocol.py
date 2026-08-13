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
  transitions, so skills must check `can_transition` before firing a mode.
- Fault sources / per-bit code labels (see `faults.py` for the decoder).

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
`docs/SPEC.md` §16, planned).
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
    # unresolved (`docs/ROBOT-PERIPHERALS.md` §4). Do not build on these two
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
# vendor's `g1_loco_client.hpp`; see docs/ROBOT-INVENTORY.md §3) is:
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
    # harness-supported and weight-bearing) — unresolved, see
    # `docs/ROBOT-API.md` §11. 501 is the leading candidate for the right
    # target on this build and has never been tried.
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
_LOCOMOTION_MODES: Final[frozenset[int]] = frozenset({Mode.WALK, Mode.WALK_WAIST, Mode.RUN, 802})


def can_transition(current_mode: int, target_mode: int) -> bool:
    """True if the FSM will accept `current → target` directly."""
    if current_mode != Mode.DAMP and target_mode in _DAMP_TARGETS:
        return False
    if current_mode == Mode.ZERO_TORQUE and target_mode != Mode.DAMP:
        return False
    if current_mode == Mode.SQUAT and target_mode != Mode.DAMP:
        return False
    if current_mode == Mode.DAMP and target_mode not in _DAMP_TARGETS:
        return False
    if current_mode == Mode.PREPARATION and target_mode not in _PREPARATION_TARGETS:
        return False
    return True


def is_locomotion_state(mode: int) -> bool:
    """True if the FSM mode permits arm gestures (Walk / Walk Waist / Run)."""
    return mode in _LOCOMOTION_MODES


# ---------------------------------------------------------------------------
# Upper-limb gestures (param={"data": index}, topic=arm_request)
# ---------------------------------------------------------------------------


class Gesture(IntEnum):
    """G1 arm-gesture indices for `rt/api/arm/request` with api_id=7106."""

    SHAKE_HANDS = 27
    HIGH_FIVE = 18
    HUG = 19
    HIGH_WAVE = 26
    LOW_WAVE = 25  # Face-level wave
    CLAP = 17
    BLOW_KISS = 12  # Left hand
    HEART_BOTH_HANDS = 20
    HEART_SINGLE_HAND = 21
    HANDS_UP = 15
    SINGLE_HAND_UP = 23  # Right hand
    REFUSE = 22
    # ⚠️ UNVERIFIED. 36 has no backing in any vendor artifact on the robot —
    # not the official action table, not the C++ gesture map, not the Python
    # one. Its only source is a decompiled Android app. It has never been sent
    # to this robot successfully, so `point_at` may simply be wrong. Expect
    # 7402 "Action ID does not exist" if it is (survey 2026-08-14).
    FORWARD_PUSH = 36  # Closest equivalent to point_at
    ULTRAMAN_RAY = 24  # X-Ray pose
    RELEASE_ARM = 99


GESTURE_LABEL: Final[dict[int, str]] = {g.value: g.name.lower() for g in Gesture}


def gesture_label(gesture: int) -> str:
    return GESTURE_LABEL.get(gesture, f"unknown({gesture})")


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
    "wave":          SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.HIGH_WAVE),
    "point_at":      SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.FORWARD_PUSH),
    "shake_hand":    SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.SHAKE_HANDS),
    "hug":           SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.HUG),
    "clap":          SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.CLAP),
    "release_arm":   SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.RELEASE_ARM),
}
