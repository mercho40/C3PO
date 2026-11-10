"""Shared velocity helpers for locomotion skills, dispatched by `SIM_MODE`.

The two targets take velocity through completely different channels, so this
module is the single seam that hides the difference — `walk_to` and `turn`
call `send_velocity` and never learn which one they're driving.

- **sim** (`isaac`, `mujoco_local`): publishes a Python-list-as-string
  `"[x_vel, y_vel, yaw_vel, height]"` to `rt/run_command/cmd`. This is an
  Isaac Sim convenience channel; real G1 firmware does **not** subscribe to
  it, which is why publishing there on real hardware is a silent no-op.
- **real**: a `SET_VELOCITY` RPC (api_id 7105) on `rt/api/sport/request`
  carrying `{"velocity": [vx, vy, omega], "duration": d}`. `height` has no
  place in that call — stand height is a separate api_id (7104) — so it is
  accepted and ignored here.

Velocity caps and gains come from `unitree_sim_isaaclab/send_commands_keyboard.py`
and are fitted to the *sim* walk policy, which runs at ~10-15% of commanded
velocity. **They will not transfer to real hardware** and need measuring on
the robot before `walk_to` can close a distance accurately there. On real,
every setpoint then passes `bridge.safety.govern` — tighter caps, the
person-distance rule and an acceleration limit — which is the conservative
direction to be wrong in.

`_` prefix on the module: skills inside `bridge.skills` import freely; this
isn't part of the bridge's public API.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
import time
from typing import Any

import structlog

from bridge.skills.task_runtime import Task
from bridge import estop

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

CMD_TOPIC = "rt/run_command/cmd"

# How long the firmware honours one SET_VELOCITY setpoint before stopping by
# itself. We re-issue every LOOP_PERIOD_S (20 ms), so 1.0 s is ~50x headroom
# against a slow loop while still braking the robot within a second if this
# process dies mid-stride.
#
# This matters more than it looks: it is a deadman *below* our software. The
# alternative the vendor client offers is `Move()`'s continuous mode with
# duration=864000 (ten days), where a crashed bridge leaves the robot walking
# with its last setpoint. Do not raise this to "reduce chatter".
VELOCITY_DURATION_S = 1.0

# Loop / progress
LOOP_HZ = 50
LOOP_PERIOD_S = 1.0 / LOOP_HZ
PROGRESS_NOTIFY_DELTA = 0.05  # only emit MCP progress when delta >= 5%

# Velocity caps (m/s, m/s, m/s, rad/s) — match the keyboard teleop example.
#
# These are Isaac-Sim-tuned, and 3-5x higher than the only numbers anyone has
# vetted against this physical robot (walk_velocity.py's 0.3 / 0.3, which
# come from xr_teleoperate's own controller-button safety cap). They stay as
# the *sim* caps because they are tuned and exercised there — but they are no
# longer what reaches real hardware: `send_velocity` re-clamps through the
# safety governor (bridge/safety.py) whenever SIM_MODE == "real".
#
# Previously these were the only clamp on either target, which meant wiring
# real-mode odom silently gave walk_to/turn permission to command 1.0 m/s and
# ~90 deg/s at a humanoid, on a path where nothing had ever been measured.
MAX_FWD_VEL = 1.0
MAX_BACK_VEL = -0.6
MAX_LAT_VEL = 0.5
MAX_YAW_VEL = 1.57

# Proportional control gains tuned against unitree_sim_isaaclab on 2026-05-09.
KP_LIN = 0.8
KP_YAW = 1.2

DEFAULT_HEIGHT = 0.78

_publisher: Any = None


def _get_publisher() -> Any:
    """Lazy ChannelPublisher for `rt/run_command/cmd`."""
    global _publisher
    if _publisher is None:
        from unitree_sdk2py.core.channel import ChannelPublisher
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

        _publisher = ChannelPublisher(CMD_TOPIC, String_)
        _publisher.Init()
        log.info("locomotion.publisher.ready", topic=CMD_TOPIC)
    return _publisher


def send_velocity(
    vx: float,
    vy: float,
    vyaw: float,
    height: float,
    *,
    autonomous: bool = False,
    nav2: bool = False,
) -> int | None:
    """Send one body-frame velocity setpoint, by whatever channel this target uses.

    `height` applies to the sim channel only; see the module docstring.
    `autonomous` marks a source with no person in its loop (Nav2, go_to_object):
    it gets the safety governor's stale-perception policy on top of the shared
    rules. `nav2` marks Nav2's cmd_vel alone, which also gets the blind rule.

    Returns the robot's RPC code on real hardware (0 = acknowledged), and None
    in sim, where the setpoint is a fire-and-forget publish with no ack.
    `stop_everything` reads it to tell a stop the robot took from one it never
    heard; the control loops ignore it.
    """
    if SIM_MODE == "real":
        from bridge import safety
        from bridge.sdk import g1_rpc

        # THE ONE GOVERNOR, at the dispatch point rather than in each caller:
        # walk_to/turn size their setpoints against the sim caps above
        # (MAX_FWD_VEL=1.0, MAX_YAW_VEL=1.57) and saturate them on any target
        # more than a metre away, Nav2 against its own YAML, the headset
        # against its stick. Governing here covers every current and future
        # caller by construction — speed and yaw caps, the person-distance
        # rule, acceleration — see bridge/safety.py. The controllers still
        # converge, just more slowly.
        if (vx or vy or vyaw) and estop.stop_is_standing():
            # The last line under the MCP gate: an internal call that skips
            # GovernedMCP (look_around -> turn, a skill calling a skill, the
            # headset, Nav2) still cannot move the robot while PARAR stands.
            log.warning("locomotion.refused_stop_standing", vx=vx, vy=vy, vyaw=vyaw)
            vx = vy = vyaw = 0.0
        vx, vy, vyaw = safety.govern(vx, vy, vyaw, autonomous=autonomous, nav2=nav2)

        code, _ = g1_rpc.call_set_velocity(vx, vy, vyaw, VELOCITY_DURATION_S)
        if code != 0:
            # Deliberately not raising: this is called at 50 Hz inside a control
            # loop, and one dropped setpoint is self-correcting — the next
            # iteration re-sends, and if they *all* fail the firmware's duration
            # timeout stops the robot. Raising here would instead abort the
            # skill mid-stride, skipping its own stop sequence.
            log.warning("locomotion.set_velocity.rpc_error", rpc_code=code)
        return code

    from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

    payload = str([float(vx), float(vy), float(vyaw), float(height)])
    _get_publisher().Write(String_(data=payload))
    return None


async def send_velocity_async(
    vx: float, vy: float, vyaw: float, height: float, *, autonomous: bool = False
) -> int | None:
    """`send_velocity`, but without blocking the event loop on real hardware.

    On real, `send_velocity` makes a synchronous DDS RPC that waits for an ack
    with SPORT_TIMEOUT_S (10 s) of headroom. Called bare from inside walk_to /
    turn's 50 Hz `async` control loop, a slow ack stalls the entire loop with
    no yield point — which means the `cancel_task` that would stop the walk
    cannot be delivered, precisely while the robot keeps moving. `g1_rpc`'s own
    docstring flags this hazard.

    Awaiting it off-thread keeps the ack semantics exactly as they are (this is
    NOT the unverified `_CallNoReply` change that docstring also floats) while
    letting the loop stay responsive: a slow robot now slows the control rate
    instead of freezing the process that has to stop it.

    Sim publishes are non-blocking, so they stay inline — no thread churn for a
    path that never needed it.
    """
    if SIM_MODE == "real":
        return await asyncio.to_thread(
            functools.partial(send_velocity, vx, vy, vyaw, height, autonomous=autonomous)
        )
    return send_velocity(vx, vy, vyaw, height, autonomous=autonomous)


async def stop_motion(height: float = DEFAULT_HEIGHT, duration_s: float = 0.4) -> None:
    """Send zero-velocity commands for `duration_s` so the robot rests cleanly.

    Monotonic clock: a wall clock stepping FORWARD mid-burst (which is what an
    NTP correction does on a machine that booted without an RTC — the robot
    onboard, every time) ends the burst early, and this burst is what actually
    stops the robot. A short one is a robot still carrying velocity.
    """
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        await send_velocity_async(0, 0, 0, height)
        await asyncio.sleep(LOOP_PERIOD_S)


#: How long the stop burst keeps counting acks after its last setpoint. A reply
#: slower than this counts as unheard. A guess until the real SET_VELOCITY ack
#: latency is measured on the robot.
STOP_ACK_GRACE_S = 0.3


def stop_motion_sync(height: float = DEFAULT_HEIGHT, duration_s: float = 0.4) -> tuple[int, int]:
    """Blocking variant of stop_motion for sync skills (e.g. stop_everything).

    Returns `(sent, acked)`: how many zero setpoints went out, and how many the
    robot acknowledged with code 0. In sim nothing is acknowledged, so `acked`
    stays 0 there; only real-hardware callers should read it.

    On real each setpoint waits for its ack on its own thread, so the burst
    keeps its 50 Hz schedule however the robot answers. It used to wait in
    line: SET_VELOCITY has SPORT_TIMEOUT_S (10 s) of headroom, so one hung reply
    held the whole burst, it came back `acked == 0`, and stop_everything damped
    a standing robot. Acks count until STOP_ACK_GRACE_S after the last
    setpoint. Concurrent calls are safe on the vendor client: it matches each
    reply to its request by id, and the walk loops already call it from
    several threads.
    """
    codes: list[int | None] = []
    errors: list[Exception] = []

    def send_one() -> None:
        try:
            codes.append(send_velocity(0, 0, 0, height))
        except Exception as exc:
            errors.append(exc)

    threads: list[threading.Thread] = []
    sent = 0
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        sent += 1
        if SIM_MODE == "real":
            thread = threading.Thread(target=send_one, name="stop-burst", daemon=True)
            thread.start()
            threads.append(thread)
        else:
            send_velocity(0, 0, 0, height)  # a publish: it never waits
        time.sleep(LOOP_PERIOD_S)
    grace_end = time.monotonic() + STOP_ACK_GRACE_S
    for thread in threads:
        thread.join(max(0.0, grace_end - time.monotonic()))
    acked = codes.count(0)
    if errors and not acked:
        # Nothing landed and something raised: stop_everything reports it and damps.
        raise errors[0]
    if errors:
        # The robot heard the rest, so no Damp; but a raise is never silent.
        log.warning(
            "locomotion.stop_burst.setpoint_raised", count=len(errors), error=repr(errors[0])
        )
    return sent, acked


async def maybe_report_progress(
    ctx: Any | None, task: Task, message: str, last_reported: float
) -> float:
    """Emit an MCP progress notification when the delta since last report exceeds the threshold.

    Returns the updated `last_reported` value so callers can keep a running watermark.
    Best-effort: any exception is swallowed (progress is decorative, never load-bearing).
    """
    if ctx is None:
        return last_reported
    if task.progress - last_reported < PROGRESS_NOTIFY_DELTA:
        return last_reported
    try:
        await ctx.report_progress(progress=task.progress, total=1.0, message=message)
    except Exception as exc:
        log.debug("locomotion.progress.failed", task_id=task.task_id, error=str(exc))
    return task.progress


def teleop_conflict() -> str | None:
    """Why this skill must not command velocity right now, or None.

    `rt/run_command/cmd` has no arbitration. Two processes writing velocity at
    20-50 Hz give a robot that obeys whichever message landed last, alternating
    tens of times a second, with neither writer able to tell. Both writers exist
    and are reachable at once: this process runs walk_to / turn / walk_velocity,
    and the teleop stream runs the headset.

    The collision is ordinary, not exotic — the operator walks the robot across
    a room while the agent, asked in the chat panel to "go to the door", starts
    a walk_to. Both requests are legitimate. Only one can be obeyed.

    The person in the headset wins. They are in the room with the robot, they
    can see what it is about to hit, and they did not ask a language model for
    permission to move. An agent's plan can wait.
    """
    from bridge.estop import teleop_is_driving

    if not teleop_is_driving():
        return None
    return (
        "a teleoperation session is driving the robot right now. Two commanders on "
        "rt/run_command/cmd means the robot obeys whichever message landed last, so this "
        "skill will not add itself as a second one. The operator is driving right now; "
        "wait until they let go."
    )


def arm_hold_conflict() -> str | None:
    """Why the base must not walk now because this process holds the arms, or None.

    move_arm(hold=True), pick and reach leave `rt/arm_sdk` engaged after they
    return, and walking under arm_sdk weight is untried on this robot (BACKLOG
    §12). The headset's own arms renew the teleop lease, so teleop_conflict
    already covers those. Checked here, not in ArmSdkDriver.engage, so the
    headset's arms are never refused for the operator looking around.
    """
    from bridge.teleop.arm_sdk import get_driver

    if not get_driver().engaged:
        return None
    return (
        "the arms are held on rt/arm_sdk (move_arm, pick or reach), and walking under "
        "arm_sdk weight is untried on this robot. Call release_arm_control (or "
        "stop_everything) first, then walk."
    )


def fsm_conflict() -> str | None:
    """Why a fresh FSM reading says the legs will not take velocity now, or None.

    In 802 (Run, R2+A on the remote) SetVelocity answers code 0 and nothing moves,
    so a walk stood still until its timeout and reported completed. Gated like
    ArmSdkDriver's FSM check, except that an unknown or stale reading is allowed:
    sim has no FSM poller, and a slow poll must not block a walk. Never creates
    the sampler.
    """
    from bridge.sdk import g1_protocol
    from bridge.sdk.state import existing_sampler
    from bridge.skills._g1_request import WALK_FSM_IDS
    from bridge.teleop.arm_sdk import MAX_FSM_AGE_S

    sampler = existing_sampler()
    if sampler is None:
        return None
    state = sampler.get_arm_state()
    fsm_id, age = state["fsm_id"], state["fsm_age_s"]
    if fsm_id is None or age is None or age > MAX_FSM_AGE_S or fsm_id in WALK_FSM_IDS:
        return None
    return (
        f"the robot is in FSM {fsm_id} ({g1_protocol.mode_label(fsm_id)}), not a walk "
        "program (500/501), so it would acknowledge every velocity and not move. Run damp, "
        "prepare, then start_walking_waist, and check that get_state's fsm_id reads 501."
    )


#: The error a running walk or turn ends with when the headset takes over.
TELEOP_TAKEOVER = (
    "the operator in the headset took over and is driving the robot, so this motion "
    "ended. Do not retry it while they drive."
)

#: The error a walk or turn refuses or ends with when odometry stops arriving.
POSE_STALE = (
    "pose_stale: odometry has not updated for over a second, so the robot would steer "
    "on a frozen position. Check get_state's faults (stale_pose) and the bridge's odom "
    "subscription before retrying."
)


def pose_stale(sampler: Any) -> bool:
    """True when the sampler's newest pose is older than safety.POSE_STALE_S, or it has none.

    `get_state()` keeps returning the last pose after odometry stops, so a loop
    reading only that would keep driving toward a target it never gets closer to.
    """
    from bridge.safety import POSE_STALE_S

    pose = sampler.latest_pose()
    return pose is None or pose[2] > POSE_STALE_S
