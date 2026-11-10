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
   * the stale-perception policy, for AUTONOMOUS sources only (Nav2 and
     go_to_object's walk);
   * the blind rule, for Nav2's cmd_vel only: zero unless a fresh world
     summary says the lidar is online. Nav2 steers on the lidar costmap;
     go_to_object steers on the camera, so a lidar fault must not stop it;
   * the geofence, while the Nav2 gate is armed: zero further than
     SAFETY_GEOFENCE_M from where arm_navigation found the robot, or with no
     fresh odometry pose to measure. Manual driving is never fenced;
   * an acceleration cap. Braking toward zero is never rate-limited.

   The firmware's 1 s `duration` deadman stays underneath all of it
   (`_locomotion.VELOCITY_DURATION_S`): it is the part that still works when
   this process is dead.

2. **Risk tiers** — every MCP tool carries a tier derived from its metadata
   (`skill_meta.derive_tier`). `tool_refusal()` runs inside the MCP server's
   own `call_tool`, so a T2 tool, or a T1 tool of danger "high"
   (arm_navigation, start_walking_waist, zero_torque), cannot be invoked over MCP — by
   apps/back, by Claude Code, by anything — without a one-shot operator
   confirmation; T2 never with a person inside arm's reach. apps/back's
   admin-only /invoke grants before it dispatches a high-danger T1, so the
   admin's click is the confirmation for the console and headset bring-up.
   Below LOW_BATTERY_PCT the long and arm tools are refused outright.

WHY THE STALE POLICY IS AUTONOMOUS-ONLY. The console walk pad and the headset
drive the robot today with no perception container running at all, with a
person watching who can see what the detector cannot. Treating "no detector"
as "person nearby" for them would cripple the only driving that has worked on
this robot, for no gain. An autonomous source has no such person in the loop
by definition, so for it unknown is treated as near (SAFETY_STALE_POLICY).
Fresh detections apply to every source, manual included.
"""

from __future__ import annotations

import hashlib
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

# -- the geofence --------------------------------------------------------------

# Nav2 may not take the robot further than this from where it was armed
# (vendor odometry, the frame walk_to uses). 0 or less turns it off; an
# unparseable or non-finite value falls back to the default, never to off.
GEOFENCE_DEFAULT_M = 3.0
_geofence_env = _env_float("SAFETY_GEOFENCE_M", GEOFENCE_DEFAULT_M)
GEOFENCE_M = _geofence_env if math.isfinite(_geofence_env) else GEOFENCE_DEFAULT_M
# Odometry arrives at ~50 Hz or more, so 1 s is dozens of missed messages.
POSE_STALE_S = 1.0

# -- risk tiers ---------------------------------------------------------------

# Covers T2 and high-danger T1 alike (needs_confirmation); off opens both.
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
# Where arm_navigation found the robot, (x, y) in vendor odometry; None = unknown.
_fence_anchor: tuple[float, float] | None = None
# (tool, sha256 of its canonical arguments) -> expiry: a grant is for one argument set.
_grants: dict[tuple[str, str], float] = {}


def reset() -> None:
    """Forget governor state and grants. For tests, and nothing else."""
    global _last, _last_at, _last_reason, _fence_anchor
    with _lock:
        _last, _last_at, _last_reason = (0.0, 0.0, 0.0), None, None
        _fence_anchor = None
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
    stale_person = False
    for obj in report.get("objects") or []:
        if isinstance(obj, dict) and obj.get("label") == "person":
            try:
                ranges.append(float(obj["range_m"]))
                # A fresh report can re-emit an old detection; no age_s = older images, as before.
                if float(obj.get("age_s") or 0.0) > PERCEPTION_STALE_S:
                    stale_person = True
            except (KeyError, TypeError, ValueError):
                # A person whose range or age we cannot read could be anywhere.
                return None, False
    if not ranges and report.get("objects_omitted"):
        # Truncated on the wire: the person may be one of the dropped rows.
        return None, False
    # A stale person is not "clear": keep the range, mark the reading not fresh,
    # so callers still stop/refuse for someone known to be close (review 2026-11-04).
    return (min(ranges) if ranges else None), not stale_person


def speed_factor(*, autonomous: bool) -> tuple[float, str | None]:
    """Multiplier for base speed from the person rule, and why (None = 1.0)."""
    if not PERSON_RULE:
        return 1.0, None
    nearest, fresh = person_proximity()
    if nearest is not None and nearest < PERSON_STOP_M:
        # Fresh or not: a person known to be this close stops the base.
        return 0.0, "person_within_stop_distance"
    if fresh:
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


def cap(
    vx: float, vy: float, vyaw: float, max_linear: float, max_yaw: float
) -> tuple[float, float, float]:
    """The static caps: non-finite axes to 0, linear speed capped as a vector, yaw clamped."""
    # Non-finite is a bug upstream, never a request: _clamp would turn NaN into the full cap.
    vx, vy, vyaw = (v if math.isfinite(v) else 0.0 for v in (vx, vy, vyaw))
    tx, ty = _clamp(vx, max_linear), _clamp(vy, max_linear)
    # The vector, not each axis: a diagonal must not reach sqrt(2) x the cap.
    norm = math.hypot(tx, ty)
    if norm > max_linear:
        tx, ty = tx * max_linear / norm, ty * max_linear / norm
    return tx, ty, _clamp(vyaw, max_yaw)


def obstacles_fresh() -> bool:
    """Whether Nav2's obstacle sensor is live: a fresh world summary with the lidar online.

    Independent of PERSON_RULE and STALE_POLICY: those are about people, this is
    about whether the costmap Nav2 drives on is still being fed at all.
    shortcut: lidar only; nav2-depth (ACTION-PLAN E5-E8) must add its depth cloud here.
    """
    from bridge.sdk.perception_link import existing_link

    link = existing_link()
    if link is None:
        return False
    report, age = link.latest_report()
    return bool(
        report and age is not None and age <= PERCEPTION_STALE_S and report.get("lidar_online")
    )


def _pose_now() -> tuple[float, float] | None:
    """The robot's (x, y) in vendor odometry, or None when there is none fresher than POSE_STALE_S.

    Never creates the sampler, like battery_pct.
    """
    from bridge.sdk.state import existing_sampler

    sampler = existing_sampler()
    if sampler is None:
        return None
    try:
        pose = sampler.latest_pose()
    except Exception as exc:  # noqa: BLE001 - a broken read is unknown, never a crash in the gate
        log.warning("safety.pose_unknown", reason="read_failed", error=str(exc))
        return None
    if pose is None or pose[2] > POSE_STALE_S:
        return None
    return pose[0], pose[1]


def anchor_geofence(*, renewing: bool) -> None:
    """Centre the fence on the robot now. arm_navigation calls this.

    A renewal while the gate is still open keeps the centre it has, so re-arming
    leg by leg cannot walk the fence across the room; it re-centres only from a
    closed gate, or when the last attempt found no pose.
    """
    global _fence_anchor
    pose = _pose_now()
    with _lock:
        if not renewing or _fence_anchor is None:
            _fence_anchor = pose
    log.warning("safety.geofence_anchored", anchor=pose, radius_m=GEOFENCE_M, renewing=renewing)


def geofence() -> dict[str, Any]:
    """The fence as arm_navigation and /safety/status report it."""
    pose = _pose_now()
    with _lock:
        anchor = _fence_anchor
    distance = None if pose is None or anchor is None else round(math.dist(pose, anchor), 2)
    return {
        "radius_m": GEOFENCE_M if GEOFENCE_M > 0 else None,
        "anchor": None if anchor is None else [round(v, 2) for v in anchor],
        "distance_m": distance,
        # None: no fence, or no pose/anchor to measure (Nav2 is then held at zero).
        "inside": None if GEOFENCE_M <= 0 or distance is None else distance <= GEOFENCE_M,
    }


def _nav2_armed() -> bool:
    from bridge.sdk.perception_link import existing_link

    link = existing_link()
    return link is not None and link.is_enabled()


def _geofence_block() -> str | None:
    """Why the fence stops autonomous motion now, or None when it allows it.

    Only while the Nav2 gate is open: arming is what sets the centre, and an
    unarmed autonomous walk (go_to_object's walk_to) is bounded by its own target.
    """
    if GEOFENCE_M <= 0 or not _nav2_armed():
        return None
    pose = _pose_now()
    with _lock:
        anchor = _fence_anchor
    if pose is None or anchor is None:
        # No pose to measure against: Nav2 could be anywhere, so it gets zero.
        return "geofence_pose_unknown"
    if math.dist(pose, anchor) > GEOFENCE_M:
        return "outside_geofence"
    return None


def govern(
    vx: float, vy: float, vyaw: float, *, autonomous: bool = False, nav2: bool = False
) -> tuple[float, float, float]:
    """The velocity that may actually reach the legs for this request.

    `nav2` marks Nav2's cmd_vel, the one source that steers on the lidar costmap;
    it is autonomous too.
    """
    global _last, _last_at, _last_reason
    autonomous = autonomous or nav2
    factor, reason = speed_factor(autonomous=autonomous)
    if nav2 and not obstacles_fresh():
        # Nav2 keeps planning on its last costmap when the sensor goes quiet, and
        # Humble's collision monitor ignores a stale source: blind, so it fails open.
        factor, reason = 0.0, "obstacle_source_stale"
    if autonomous and (fence := _geofence_block()):
        # Zero, not "inward only": Nav2's goal is what took it out, and recovery
        # is the operator's (teleop is never fenced) or a fresh arm here.
        factor, reason = 0.0, fence
    tx, ty, tw = cap(vx, vy, vyaw, MAX_LINEAR_MPS, MAX_YAW_RPS)
    tx, ty, tw = tx * factor, ty * factor, tw * factor

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


def needs_confirmation(meta: dict[str, Any] | None) -> bool:
    """Whether a call of this tool needs an operator's grant first: T2, and high-danger T1."""
    c3po = (meta or {}).get("c3po") or {}
    tier = c3po.get("tier")
    return tier == "T2" or (tier == "T1" and c3po.get("danger_level") == "high")


# -- battery -------------------------------------------------------------------

# Seconds after which the last BmsState_ is "unknown": the snapshot keeps its
# soc forever, so a dead subscription would otherwise freeze the gate.
BATTERY_STALE_S = 60.0


def drains_battery(meta: dict[str, Any] | None) -> bool:
    """Arm motion and long motion: what a low pack can leave stopped mid-task."""
    c3po = (meta or {}).get("c3po") or {}
    if c3po.get("tier") in (None, "T0"):
        return False
    return (
        c3po.get("tier") == "T2"
        or float(c3po.get("expected_duration_s") or 0.0) >= 15.0
        or (c3po.get("classification") == "locomotion" and c3po.get("danger_level") == "high")
    )


def battery_pct() -> int | None:
    """The pack's state of charge, or None when unknown (no sampler, no BMS message, stale).

    Never creates the sampler, like person_proximity and the perception link.
    """
    from bridge.sdk.state import existing_sampler

    sampler = existing_sampler()
    if sampler is None:
        return None
    try:
        pct, age = sampler.battery()
    except Exception as exc:  # noqa: BLE001 - a broken read is unknown, never a crash in the gate
        log.warning("safety.battery_unknown", reason="read_failed", error=str(exc))
        return None
    if pct is None or age is None or age > BATTERY_STALE_S:
        if os.environ.get("SIM_MODE") == "real":
            log.warning("safety.battery_unknown", reason="stale" if pct is not None else "no_bms")
        return None
    return pct


def _grant_key(tool: str, args_key: str) -> tuple[str, str]:
    return tool, hashlib.sha256(args_key.encode()).hexdigest()


def grant_confirmation(tool: str, args_key: str, *, via: str) -> float:
    """Allow ONE call of `tool` with exactly these arguments within CONFIRM_TTL_S.

    `args_key` is the canonical JSON of the arguments (mcp_server._canonical_args),
    so another caller cannot spend the operator's grant on different ones.
    Returns the expiry.

    Called only from outside the MCP tool surface: the console route
    (`POST /safety/confirm`, via="console"). The spoken path — a local STT
    match on "sí"/"confirmo", checked by code and not by the model — is meant
    to call this with via="spoken"; it is not wired yet (PLAN 0.2).
    """
    key = _grant_key(tool, args_key)
    expires = time.monotonic() + CONFIRM_TTL_S
    with _lock:
        _grants[key] = expires
    log.warning("safety.t2_confirmed", tool=tool, args=key[1][:12], via=via, ttl_s=CONFIRM_TTL_S)
    return expires


def clear_confirmations(reason: str) -> None:
    """Drop every pending T2 grant: a confirmation given before PARAR must not
    survive into what comes after it."""
    with _lock:
        n = len(_grants)
        _grants.clear()
    if n:
        log.warning("safety.confirmations_cleared", count=n, reason=reason)


def _consume_confirmation(tool: str, args_key: str | None) -> bool:
    if args_key is None:
        return False
    with _lock:
        expires = _grants.pop(_grant_key(tool, args_key), None)
    return expires is not None and time.monotonic() < expires


def confirm_secret_ok(presented: str | None) -> bool:
    """Constant-time check of the console's secret. Unset secret = always no."""
    return bool(CONFIRM_SECRET) and hmac.compare_digest(
        (presented or "").encode(), CONFIRM_SECRET.encode()
    )


# Allowed while a PARAR stands: damp is the state a stop itself falls back to,
# and the first step of every bring-up, so refusing it only blocks recovery.
STOP_STANDING_ALLOWED = frozenset({"damp"})


def tool_refusal(name: str, meta: dict[str, Any] | None, args_key: str | None = None) -> str | None:
    """Why this MCP call must not run, or None. Runs before every tool call.

    `args_key` is the call's canonical arguments; None never matches a grant.
    """
    refusal = _refusal(name, meta, args_key)
    if refusal is not None and needs_confirmation(meta):
        # A pre-grant for a call refused anyway (PARAR, battery, person) must not sit armed.
        _consume_confirmation(name, args_key)
    return refusal


def _refusal(name: str, meta: dict[str, Any] | None, args_key: str | None) -> str | None:
    from bridge import estop

    tier = ((meta or {}).get("c3po") or {}).get("tier")
    if tier != "T0" and name not in STOP_STANDING_ALLOWED and estop.stop_is_standing():
        # The latch every client hits, the agent's next step included: before
        # this, PARAR stopped the robot and the model's next tool call moved it.
        log.warning("safety.refused_stop_standing", tool=name, tier=tier)
        return (
            f"refused: PARAR (stop_everything) is standing, so {name} does not run. "
            "Only an operator can resume: «Reanudar» on the console or in the "
            "headset menu. Tell the operator the robot is stopped and wait."
        )
    if drains_battery(meta):
        from bridge.sdk.state import LOW_BATTERY_PCT

        # Unknown is allowed: a decision, so stub and Isaac (no BMS) keep working.
        # shortcut: checked at call time only; a task already running is not
        # stopped when the pack crosses 20%, add a check in the skill loops if that matters.
        pct = battery_pct()
        if pct is not None and pct < LOW_BATTERY_PCT:
            log.warning("safety.refused_low_battery", tool=name, battery_pct=pct)
            return (
                f"refused: battery is {pct}% (below {LOW_BATTERY_PCT}%), so {name} does not "
                "run: arm motion and long walks can stop mid-task. walk_velocity, turn, "
                "open_hands, release_arm_control and stop still work. Tell the operator "
                "to charge the robot."
            )
    if tier == "T2" and PERSON_RULE and name not in HANDOVER_TOOLS:
        nearest, fresh = person_proximity()
        # Fresh or not: a person known to be inside reach refuses the arm.
        if nearest is not None and nearest < PERSON_REACH_M:
            # shortcut: checked at call time only; a person stepping in while
            # move_arm holds a pose is not seen. Needs a per-tick check in
            # arm_sdk's loop once the arms run autonomously.
            log.warning("safety.t2_refused", tool=name, reason="person_in_reach", range_m=nearest)
            return (
                f"refused: a person is {nearest:.2f} m away, inside arm's reach "
                f"({PERSON_REACH_M} m). {name} is a T2 tool and does not run with "
                "someone that close. Ask them to step back."
            )
    if not needs_confirmation(meta):
        return None
    if not T2_CONFIRM:
        log.warning("safety.t2_unconfirmed_allowed", tool=name, reason="SAFETY_T2_CONFIRM=off")
        return None
    if _consume_confirmation(name, args_key):
        return None
    log.warning("safety.t2_refused", tool=name, reason="no_confirmation")
    kind = "a T2 tool" if tier == "T2" else "a high-danger tool"
    return (
        f"refused: {name} is {kind} and needs an operator's confirmation first. "
        "You cannot give it: a human operator presses «Confirmar» for these exact "
        "arguments in the console chat, then you retry once with exactly the same "
        f"arguments within {int(CONFIRM_TTL_S)} s. Tell the operator what you want to do and wait."
    )


def status() -> dict[str, Any]:
    """Everything the test-day checklist needs to see, for GET /safety/status."""
    nearest, fresh = person_proximity()
    with _lock:
        last, last_at, reason = _last, _last_at, _last_reason
        pending = sorted({t for (t, _), exp in _grants.items() if time.monotonic() < exp})
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
        "obstacles_fresh": obstacles_fresh(),
        "geofence": geofence(),
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
