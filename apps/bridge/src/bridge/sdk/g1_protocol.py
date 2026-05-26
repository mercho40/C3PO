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
)

# Real G1 over WebRTC (firmware ≥ 1.5.1) — also matches DDS-direct topology.
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
    dex_left_cmd="rt/api/dex3/left/request",
    dex_right_cmd="rt/api/dex3/right/request",
    run_command=None,
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
    802: "run",  # alternative Run state observed in firmware
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
    "squat":         SkillRequest("sport_request", API_ID_G1_STATE, Mode.SQUAT),
    # Arm gestures (api_id=7106) — require a locomotion state
    "wave":          SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.HIGH_WAVE),
    "point_at":      SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.FORWARD_PUSH),
    "shake_hand":    SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.SHAKE_HANDS),
    "hug":           SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.HUG),
    "clap":          SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.CLAP),
    "release_arm":   SkillRequest("arm_request", API_ID_G1_UPPER_LIMBS, Gesture.RELEASE_ARM),
}
