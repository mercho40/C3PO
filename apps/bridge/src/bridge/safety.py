"""The safety governor: the rules every motion command passes, below the LLM.

docs/autonomy/PLAN.md Phase 0.2, research/05 §C.2 layers 2, 4 and 6. Nothing an
agent says can change what this module decides — it reads no tool arguments it
did not get from the dispatch path, and the only way to widen it is an
environment variable on the robot.

Two gates live here, and both sit on a path the LLM cannot route around:

1. **Base velocity** — `govern()` is called from `_locomotion.send_velocity`,
   the one function every real `SET_VELOCITY` (7105) goes out through: walk_to,
   turn, walk_velocity, the headset stream, the console walk pad and Nav2's
   cmd_vel all reach the legs there. It applies, in order:

   * hard caps on vx/vy and yaw rate;
   * the person-distance rule: a fresh `person` detection inside
     PERSON_SLOW_M scales speed by PERSON_SLOW_FACTOR, inside PERSON_STOP_M
     stops the base;
   * the stale-perception policy, for AUTONOMOUS sources only (Nav2 today);
   * an acceleration cap. Braking toward zero is never rate-limited.

   The firmware's 1 s `duration` deadman stays underneath all of it
   (`_locomotion.VELOCITY_DURATION_S`): it is the part that still works when
   this process is dead.

2. **Risk tiers** — every MCP tool carries a tier derived from its metadata
   (`skill_meta.derive_tier`). `tool_refusal()` runs inside the MCP server's
   own `call_tool`, so a T2 tool cannot be invoked over MCP — by apps/back, by
   Claude Code, by anything — without a one-shot operator confirmation, and
   never with a person inside arm's reach.

WHY THE STALE POLICY IS AUTONOMOUS-ONLY. The console walk pad and the headset
drive the robot today with no perception container running at all, with a
person watching who can see what the detector cannot. Treating "no detector"
as "person nearby" for them would cripple the only driving that has worked on
this robot, for no gain. An autonomous source has no such person in the loop
by definition, so for it unknown is treated as near (SAFETY_STALE_POLICY).
Fresh detections apply to every source, manual included.
"""

from __future__ import annotations

import hmac
import math
import os
import threading
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        log.warning("safety.env_unparseable", name=name, default=default)
        return default


def _env_on(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("off", "0", "false", "no")


# -- base velocity caps -------------------------------------------------------

# Ceilings no env value can exceed. 0.5 m/s is PLAN 0.2's limit; 0.8 rad/s is
# perception_link's Nav2 clamp, the widest yaw anything here was designed for.
HARD_MAX_LINEAR_MPS = 0.5
HARD_MAX_YAW_RPS = 0.8

# The operating caps. 0.3 is the only speed vetted on this robot
# (walk_velocity's cap, from xr_teleoperate #135) and exactly what
# `_locomotion.send_velocity` clamped to before this module existed — raise
# them only after measuring, and never past the ceilings above.
MAX_LINEAR_MPS = min(_env_float("SAFETY_MAX_LINEAR_MPS", 0.3), HARD_MAX_LINEAR_MPS)
MAX_YAW_RPS = min(_env_float("SAFETY_MAX_YAW_RPS", 0.3), HARD_MAX_YAW_RPS)

# Acceleration caps. Per command, speed may grow by at most MAX_ACCEL × dt,
# where dt is the time since the previous command capped at ACCEL_DT_CAP_S. So a
# 20-50 Hz stream (Nav2, teleop, walk_to) ramps, while a command from rest may
# step straight to the cap — which is what every manual path does today.
MAX_ACCEL_MPS2 = _env_float("SAFETY_MAX_ACCEL_MPS2", 1.0)
MAX_YAW_ACCEL_RPS2 = _env_float("SAFETY_MAX_YAW_ACCEL_RPS2", 1.0)
ACCEL_DT_CAP_S = 0.3
# After this long the firmware has dropped the last setpoint by itself (its
# 1 s `duration`), so the robot is at rest and that is the ramp's start point.
SETPOINT_EXPIRES_S = 1.0

# -- the person-distance rule --------------------------------------------------

PERSON_RULE = _env_on("SAFETY_PERSON_RULE")
PERSON_SLOW_M = 1.5
PERSON_STOP_M = 0.5
PERSON_SLOW_FACTOR = 0.4
# Arm's reach for the T2 block. The G1 arm is ~0.6 m from the shoulder; 0.8
# leaves room for detector range error, which is unmeasured on this robot.
PERSON_REACH_M = 0.8
# Detections older than this are "unknown", not "clear". The world summary
# arrives at 4 Hz, so 1 s is four missed reports.
PERCEPTION_STALE_S = 1.0
# What an autonomous source does when perception is unknown: "slow" (treat as
# a person inside PERSON_SLOW_M), "stop", or "ignore". Slow, not stop, so that
# Nav2 can still be brought up on the gantry before the vision container is.
STALE_POLICY = os.environ.get("SAFETY_STALE_POLICY", "slow").strip().lower()

# -- risk tiers ---------------------------------------------------------------

T2_CONFIRM = _env_on("SAFETY_T2_CONFIRM")
# The console must present this to /safety/confirm. Unset = no confirmation
# can be issued, so T2 is refused outright: fail closed, never open.
CONFIRM_SECRET = os.environ.get("SAFETY_CONFIRM_SECRET", "")
CONFIRM_TTL_S = 30.0
# T2 skills that exist to be near a person (skills/manipulation.py).
HANDOVER_TOOLS: frozenset[str] = frozenset({"handover"})


_lock = threading.Lock()
_last: tuple[float, float, float] = (0.0, 0.0, 0.0)
_last_at: float | None = None
_last_reason: str | None = None
_grants: dict[str, float] = {}


def reset() -> None:
    """Forget governor state and grants. For tests, and nothing else."""
    global _last, _last_at, _last_reason
    with _lock:
        _last, _last_at, _last_reason = (0.0, 0.0, 0.0), None, None
        _grants.clear()


# -- perception ---------------------------------------------------------------


def person_proximity() -> tuple[float | None, bool]:
    """`(nearest person in metres or None, perception_is_fresh)`.

    Never creates the perception link: a process that never started one (the
    teleop server, the test suite) simply has no data, which reads as unknown.
    """
    from bridge.sdk.perception_link import existing_link

    link = existing_link()
    if link is None:
        return None, False
    report, age = link.latest_report()
    if not report or age is None or age > PERCEPTION_STALE_S or not report.get("detector_online"):
        return None, False
    ranges = []
    for obj in report.get("objects") or []:
        if isinstance(obj, dict) and obj.get("label") == "person":
            try:
                ranges.append(float(obj["range_m"]))
            except (KeyError, TypeError, ValueError):
                # A person whose range we cannot read could be anywhere.
                return None, False
    if not ranges and report.get("objects_omitted"):
        # Truncated on the wire: the person may be one of the dropped rows.
        return None, False
    return (min(ranges) if ranges else None), True


def speed_factor(*, autonomous: bool) -> tuple[float, str | None]:
    """Multiplier for base speed from the person rule, and why (None = 1.0)."""
    if not PERSON_RULE:
        return 1.0, None
    nearest, fresh = person_proximity()
    if fresh:
        if nearest is not None and nearest < PERSON_STOP_M:
            return 0.0, "person_within_stop_distance"
        if nearest is not None and nearest < PERSON_SLOW_M:
            return PERSON_SLOW_FACTOR, "person_near"
        return 1.0, None
    if not autonomous or STALE_POLICY == "ignore":
        return 1.0, None
    if STALE_POLICY == "stop":
        return 0.0, "perception_unknown_stop"
    return PERSON_SLOW_FACTOR, "perception_unknown_slow"


# -- the velocity governor ----------------------------------------------------


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _ramp(prev: float, req: float, max_step: float) -> float:
    """Limit speed-up to `max_step`; let braking toward zero through untouched."""
    if prev * req <= 0.0:
        # Zero or a reversal: getting to zero is braking, so start from there.
        prev = 0.0
    if abs(req) <= abs(prev):
        return req
    return prev + math.copysign(min(abs(req) - abs(prev), max_step), req)


def govern(
    vx: float, vy: float, vyaw: float, *, autonomous: bool = False
) -> tuple[float, float, float]:
    """The velocity that may actually reach the legs for this request."""
    global _last, _last_at, _last_reason
    factor, reason = speed_factor(autonomous=autonomous)
    tx = _clamp(vx, MAX_LINEAR_MPS) * factor
    ty = _clamp(vy, MAX_LINEAR_MPS) * factor
    tw = _clamp(vyaw, MAX_YAW_RPS) * factor

    now = time.monotonic()
    with _lock:
        if _last_at is None or now - _last_at > SETPOINT_EXPIRES_S:
            prev, dt = (0.0, 0.0, 0.0), ACCEL_DT_CAP_S
        else:
            prev, dt = _last, min(now - _last_at, ACCEL_DT_CAP_S)
        out = (
            _ramp(prev[0], tx, MAX_ACCEL_MPS2 * dt),
            _ramp(prev[1], ty, MAX_ACCEL_MPS2 * dt),
            _ramp(prev[2], tw, MAX_YAW_ACCEL_RPS2 * dt),
        )
        _last, _last_at = out, now
        changed, _last_reason = reason != _last_reason, reason
    if changed:
        # Transitions only: this runs at up to 50 Hz.
        log.warning("safety.speed_factor", factor=factor, reason=reason, autonomous=autonomous)
    return out


def base_moving() -> bool:
    """True while the last governed velocity is non-zero and unexpired: the legs are walking.

    Arm skills refuse then: driving rt/arm_sdk while walking bent a G1 at the waist
    (unitree_sdk2_python #146).
    """
    with _lock:
        last, at = _last, _last_at
    return (
        at is not None
        and time.monotonic() - at <= SETPOINT_EXPIRES_S
        and any(abs(v) > 1e-3 for v in last)
    )


# -- risk tiers and confirmation ----------------------------------------------


def grant_confirmation(tool: str, *, via: str) -> float:
    """Allow ONE call of `tool` within CONFIRM_TTL_S. Returns the expiry.

    Called only from outside the MCP tool surface: the console route
    (`POST /safety/confirm`, via="console"). The spoken path — a local STT
    match on "sí"/"confirmo", checked by code and not by the model — is meant
    to call this with via="spoken"; it is not wired yet (PLAN 0.2).
    """
    expires = time.monotonic() + CONFIRM_TTL_S
    with _lock:
        _grants[tool] = expires
    log.warning("safety.t2_confirmed", tool=tool, via=via, ttl_s=CONFIRM_TTL_S)
    return expires


def _consume_confirmation(tool: str) -> bool:
    with _lock:
        expires = _grants.pop(tool, None)
    return expires is not None and time.monotonic() < expires


def confirm_secret_ok(presented: str | None) -> bool:
    """Constant-time check of the console's secret. Unset secret = always no."""
    return bool(CONFIRM_SECRET) and hmac.compare_digest(
        (presented or "").encode(), CONFIRM_SECRET.encode()
    )


# Allowed while a PARAR stands: damp is the state a stop itself falls back to,
# and the first step of every bring-up, so refusing it only blocks recovery.
STOP_STANDING_ALLOWED = frozenset({"damp"})


def tool_refusal(name: str, meta: dict[str, Any] | None) -> str | None:
    """Why this MCP call must not run, or None. Runs before every tool call."""
    from bridge import estop

    tier = ((meta or {}).get("c3po") or {}).get("tier")
    if tier != "T0" and name not in STOP_STANDING_ALLOWED and estop.stop_is_standing():
        # The latch every client hits, the agent's next step included: before
        # this, PARAR stopped the robot and the model's next tool call moved it.
        log.warning("safety.refused_stop_standing", tool=name, tier=tier)
        return (
            f"refused: PARAR (stop_everything) is standing, so {name} does not run. "
            "Only an operator can resume: «Reanudar» on the console, or "
            "re-engaging the headset. Tell the operator the robot is stopped and wait."
        )
    if tier != "T2":
        return None
    if PERSON_RULE and name not in HANDOVER_TOOLS:
        nearest, fresh = person_proximity()
        if fresh and nearest is not None and nearest < PERSON_REACH_M:
            # shortcut: checked at call time only; a person stepping in while
            # move_arm holds a pose is not seen. Needs a per-tick check in
            # arm_sdk's loop once the arms run autonomously.
            log.warning("safety.t2_refused", tool=name, reason="person_in_reach", range_m=nearest)
            return (
                f"refused: a person is {nearest:.2f} m away, inside arm's reach "
                f"({PERSON_REACH_M} m). {name} is a T2 tool and does not run with "
                "someone that close. Ask them to step back."
            )
    if not T2_CONFIRM:
        log.warning("safety.t2_unconfirmed_allowed", tool=name, reason="SAFETY_T2_CONFIRM=off")
        return None
    if _consume_confirmation(name):
        return None
    log.warning("safety.t2_refused", tool=name, reason="no_confirmation")
    return (
        f"refused: {name} is a T2 tool and needs an operator's confirmation first. "
        "You cannot give it: the operator confirms on the console "
        f"(POST /skills/{name}/confirm), then the call may be retried once within "
        f"{int(CONFIRM_TTL_S)} s. Tell the operator what you want to do and wait."
    )


def status() -> dict[str, Any]:
    """Everything the test-day checklist needs to see, for GET /safety/status."""
    nearest, fresh = person_proximity()
    with _lock:
        last, last_at, reason = _last, _last_at, _last_reason
        pending = sorted(t for t, exp in _grants.items() if time.monotonic() < exp)
    return {
        "caps": {
            "linear_mps": MAX_LINEAR_MPS,
            "yaw_rps": MAX_YAW_RPS,
            "accel_mps2": MAX_ACCEL_MPS2,
            "yaw_accel_rps2": MAX_YAW_ACCEL_RPS2,
        },
        "person_rule": PERSON_RULE,
        "stale_policy": STALE_POLICY,
        "t2_confirm": T2_CONFIRM,
        "confirm_configured": bool(CONFIRM_SECRET),
        "pending_confirmations": pending,
        "perception_fresh": fresh,
        "nearest_person_m": nearest,
        "last_command": list(last),
        "last_command_age_s": None if last_at is None else round(time.monotonic() - last_at, 2),
        "last_reason": reason,
        **stop_latch(),
    }


def stop_latch() -> dict[str, Any]:
    """The PARAR latch as the console shows it: standing or not, and since when."""
    from bridge import estop

    stopped_at, acked_at = estop.last_stop_at(), estop.last_ack_at()
    return {
        "stop_standing": stopped_at > 0.0 and stopped_at > acked_at,
        "stopped_at": stopped_at or None,
        "acknowledged_at": acked_at or None,
    }
