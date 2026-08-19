"""The domain-42 side of the bridge: perception in, Nav2 velocity out.

    rt/c3po/world_summary   std_msgs::msg::dds_::String_   (JSON)  ~4 Hz, read
    rt/c3po/cmd_vel         geometry_msgs::msg::dds_::Twist_       ~20 Hz, act

WHY A SECOND DOMAIN AND NOT DOMAIN 0:
  * unitree_sdk2py.ChannelFactory is a process-wide Singleton with class-level
    __domain/__participant and an early-return `if __initialized`. Nothing built
    through it can reach domain 42. Hence: plain `cyclonedds` below, never the
    vendor SDK.
  * A ROS 2 process is pinned to exactly one ROS_DOMAIN_ID, so "summary on 0,
    costmaps on 42" would mean a second node process plus a relay inside the
    container — strictly more machinery than one extra participant here.
  * rt/c3po/cmd_vel on domain 0 would sit beside gemm's cmd_vel_to_loco. Domain
    42 makes the isolation structural rather than the pgrep honour system in
    _common.sh.

THE CONFIG MUST BE PASSED EXPLICITLY. `Domain(42, _DOMAIN42_XML)`, never a bare
`DomainParticipant(42)`. connection.py writes `<Domain id="any">` with
AllowMulticast=false and a lone `<Peer address="192.168.123.161"/>`, and "any"
applies to EVERY domain created without its own config — so a bare participant
on 42 would inherit "unicast to the control board" and discover nothing, with no
error. (dds_create_domain(id, cfg) with a non-NULL cfg overrides CYCLONEDDS_URI,
which is also why domain 0 does not read that file today; see connection.py.)

This module is deliberately SELF-SUFFICIENT about that: it depends on nothing
connection.py does or does not do. It passes its own XML for its own domain, so
it stays correct both before and after the `id="any"` → `id="0"` fix lands
(apps/perception/README.md, decisions list — the connection.py scoping fix; a
supervised change to domain 0's config).

BOTH DDS OBJECTS ARE HELD ON A MODULE SINGLETON FOR PROCESS LIFETIME.
cyclonedds-python's Entity.__del__ calls dds_delete, so a dropped reference to
the Domain silently tears down the whole domain and every reader under it stops
delivering with no error anywhere. This is the most likely way to build this and
have it work in a REPL and deliver nothing as a daemon.

Verified live on this robot: 119 samples on domain 42 concurrent with 31,337
LowState_ samples on domain 0, undisturbed.

NOTHING IN HERE IMPORTS ROS. Not rclpy, not rosidl, not a generated message
package — the whole point of the split (docs/DECISIONS.md D2) is that the bridge
speaks plain CycloneDDS and the ROS graph stops at the container boundary. The
DDS imports are lazy so that importing this module (as the tests do) costs
nothing and needs no CycloneDDS C library.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)

PERCEPTION_DOMAIN_ID = int(os.environ.get("PERCEPTION_DOMAIN_ID", "42"))
WORLD_SUMMARY_TOPIC = "rt/c3po/world_summary"
CMD_VEL_TOPIC = "rt/c3po/cmd_vel"
# Nav2's global costmap, already encoded to a small indexed PNG by the nav
# container (c3po_perception.costmap_publisher). Read-only telemetry: it feeds
# the operator console's map and reaches NOTHING that can actuate.
COSTMAP_TOPIC = "rt/c3po/costmap"

SUPPORTED_REPORT_VERSIONS = frozenset({1})
# The costmap publishes at the global costmap's 1 Hz, so this is far slacker
# than the world summary's. A stale map is a display problem, not a safety one —
# but it must still be REPORTED stale rather than shown as current, or an
# operator plans against a picture of somewhere the robot has left.
COSTMAP_STALE_AFTER_S = 5.0

REPORT_OFFLINE_AFTER_S = 2.0   # perception publishes at 4 Hz; 8 missed ticks

# Our own staleness deadman, and it sits ABOVE the firmware's 1 s SET_VELOCITY
# one (VELOCITY_DURATION_S in skills/_locomotion.py). A wedged Nav2 therefore
# gets zeros at ~0.3-0.4 s (this window plus one issue tick) instead of the
# robot coasting the full second the firmware would allow. The firmware deadman
# stays underneath as the floor: it is the only part of this that still works if
# this process dies. CMD_VEL_DEADMAN_S + BRAKE_AFTER_STALE_S must stay strictly
# below VELOCITY_DURATION_S — test_the_deadman_is_shorter_than_the_firmwares
# asserts exactly that, because the two numbers live in different files.
CMD_VEL_DEADMAN_S = 0.3

# The re-issue rate. Nav2 talks at ~20 Hz and the firmware forgets a setpoint
# after 1 s, so 10 Hz is enough to keep a walk alive while adding at most 100 ms
# to every deadline above.
ISSUE_HZ = 10.0

# After the deadman expires we do not simply fall silent: we brake actively for
# this long first. Silence alone would work (the firmware stops the robot within
# 1 s of the last setpoint) but it wastes most of a second of stride on a robot
# whose planner has already gone quiet. Zeros first, then silence, then the
# firmware deadman as the floor under all of it.
BRAKE_AFTER_STALE_S = 0.5

# How often the reader thread drains the two readers. cyclonedds-python has no
# on-data callback like the vendor SDK's ChannelSubscriber, so we poll. 50 Hz is
# comfortably above cmd_vel's 20 Hz and costs one `take(1)` per reader per tick.
READER_POLL_HZ = 50.0

# How long an `enable()` stays in force without being renewed. The gate is not
# just default-closed, it is default-*re*-closed: an operator who arms navigation
# and then walks away, or an MCP client that dies mid-session, must not leave a
# robot that will move the moment a planner wakes up. Stage 8's `arm_navigation`
# renews this on every goal.
ARM_TTL_S = 60.0

# Ours, not Nav2's — the backstop for a mis-edited YAML. Deliberately below the
# sim-fitted MAX_* in skills/_locomotion.py.
#
# !! UNMEASURED. These three tuples are reasoned defaults, not numbers anyone has
# !! watched this robot walk at. apps/perception/README.md's decisions list
# !! carries them as a decision
# !! a human owes the project before Stage 8 (the first time a planner's opinion
# !! reaches the legs). They are the ENFORCING clamp: Nav2's own
# !! max_vel_x/max_vel_theta in nav2_params.yaml are advisory, because a wrong
# !! YAML is exactly the failure this exists to survive.
CLAMP_VX, CLAMP_VY, CLAMP_WZ = (-0.20, 0.50), (-0.20, 0.20), (-0.80, 0.80)

# The same domain-42 settings as apps/perception/config/cyclonedds-domain42.xml,
# inline because this process is on the host and has no container filesystem to
# read that file from. `lo` has no MULTICAST flag on this Jetson
# (`ip -o link show lo` -> <LOOPBACK,UP,LOWER_UP>, no MULTICAST), so
# AllowMulticast on it yields a participant that starts cleanly and discovers
# nothing — hence AllowMulticast=false plus an explicit 127.0.0.1 peer, on BOTH
# sides. Everything that makes the two sides MATCH is reproduced here verbatim.
#
# <MaxAutoParticipantIndex> IS part of the discovery handshake and must match —
# it is not the local resource limit it looks like. With no explicit port on a
# unicast <Peer>, Cyclone expands that peer into one SPDP locator per
# participant index 0..MaxAutoParticipantIndex-1 (q_addrset.c
# `add_peer_addresses`), and it is also the range this process searches for its
# OWN free index (q_init.c). The default is 9. Both containers run --network
# host, so their ~13 domain-42 processes share this port space: at the default
# the bridge would probe only a third of the peers it needs and could fail to
# claim an index at all once Nav2 is up. 32, same as the container's file.
#
# What is deliberately NOT reproduced: <Tracing> (its OutputFile is /logs, a
# path that exists only inside the containers) and <MaxMessageSize> (a
# fragmentation limit for a WRITER, and this participant only reads). If the
# container's file ever changes one of the discovery lines below, change this
# string in the same commit — a mismatch here does not raise, it just stops
# discovering.
_DOMAIN42_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config"><Domain id="42">
  <General><Interfaces><NetworkInterface name="lo" priority="default" multicast="false"/></Interfaces>
    <AllowMulticast>false</AllowMulticast></General>
  <Discovery><ParticipantIndex>auto</ParticipantIndex>
    <MaxAutoParticipantIndex>32</MaxAutoParticipantIndex>
    <Peers><Peer address="127.0.0.1"/></Peers></Discovery>
</Domain></CycloneDDS>"""


def _domain_xml(domain_id: int) -> str:
    """The XML above, with its `<Domain id>` matching the domain we create.

    `Domain(id, cfg)` ignores any `<Domain>` block whose id does not match, and
    ignores it *silently* — the participant comes up on defaults (multicast on,
    autodetermine interface) and discovers nothing on `lo`. So if someone
    overrides PERCEPTION_DOMAIN_ID, the id in the config has to move with it.
    """
    if domain_id == 42:
        return _DOMAIN42_XML
    return _DOMAIN42_XML.replace('<Domain id="42">', f'<Domain id="{domain_id}">', 1)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return lo if value < lo else hi if value > hi else value


class PerceptionLink:
    """Domain-42 participant: reads the world summary, gates Nav2's velocity.

    Nothing here actuates until `enable()` has been called. That is the whole
    safety story of D2.1 in one boolean: a perception container that restarts,
    or a Nav2 that was left with a goal queued, must not be able to walk the
    robot by existing.
    """

    def __init__(
        self,
        *,
        forward: Callable[[float, float, float], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        domain_id: int = PERCEPTION_DOMAIN_ID,
    ) -> None:
        # Monotonic by default: the Jetson has no RTC battery and NTP steps its
        # wall clock after boot. A step must not read as a stale setpoint.
        self._clock = clock
        self._forward = forward or _default_forward
        self._domain_id = domain_id
        self._lock = threading.Lock()

        # --- DDS objects, held for process lifetime. See the module docstring:
        # dropping any of these calls dds_delete and delivery stops silently.
        self._domain: Any = None
        self._participant: Any = None
        self._summary_topic: Any = None
        self._cmd_vel_topic: Any = None
        self._summary_reader: Any = None
        self._cmd_vel_reader: Any = None

        # --- world summary
        self._report: dict[str, Any] | None = None
        self._report_at: float | None = None
        self._costmap: dict[str, Any] | None = None
        self._costmap_at: float | None = None
        self.costmaps_received = 0
        self.costmaps_rejected = 0
        self.reports_received = 0
        self.reports_rejected = 0

        # --- the gate
        self._enabled = False
        self._armed_until: float | None = None
        self._disabled_reason = "never armed"
        self._brake_until = 0.0

        # --- cmd_vel
        self._setpoint: tuple[float, float, float] | None = None
        self._cmd_at: float | None = None
        self._last_arrival: float | None = None
        self.cmd_vel_received = 0
        self.dropped_while_disabled = 0
        self.clamped = 0
        self.last_sent: tuple[float, float, float] | None = None
        self.last_sent_at: float | None = None

        self._started = False
        self._stop = threading.Event()
        self._costmap_topic = None
        self._costmap_reader = None
        self._reader_thread: threading.Thread | None = None
        self._issue_thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self, forward: Callable[[float, float, float], None] | None = None) -> None:
        """Create the domain, the participant and both readers. Does NOT arm.

        Readers are built with `qos=None`, i.e. the DDS defaults: BEST_EFFORT /
        KEEP_LAST(1) / VOLATILE. Requested-BEST_EFFORT against the containers'
        offered-RELIABLE matches (verified live on this robot), and KEEP_LAST(1)
        is what we want anyway — only the newest setpoint and the newest summary
        have any meaning.
        """
        if forward is not None:
            self._forward = forward
        if self._started:
            return

        # Imported here, not at module scope, so `import bridge.sdk.perception_link`
        # costs nothing and needs no CycloneDDS C library — the tests rely on it.
        from cyclonedds.domain import Domain, DomainParticipant
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

        from bridge.sdk.ros_idl import Twist_

        self._domain = Domain(self._domain_id, _domain_xml(self._domain_id))
        self._participant = DomainParticipant(self._domain_id)

        self._summary_topic = Topic(self._participant, WORLD_SUMMARY_TOPIC, String_)
        self._summary_reader = DataReader(self._participant, self._summary_topic, qos=None)

        self._cmd_vel_topic = Topic(self._participant, CMD_VEL_TOPIC, Twist_)
        self._cmd_vel_reader = DataReader(self._participant, self._cmd_vel_topic, qos=None)

        self._costmap_topic = Topic(self._participant, COSTMAP_TOPIC, String_)
        self._costmap_reader = DataReader(self._participant, self._costmap_topic, qos=None)

        self._started = True
        self._reader_thread = threading.Thread(
            target=self._read_loop, name="perception-readers", daemon=True
        )
        self._reader_thread.start()
        self._issue_thread = threading.Thread(
            target=self._run, name="perception-cmd-vel", daemon=True
        )
        self._issue_thread.start()

        log.info(
            "perception.link.ready",
            domain_id=self._domain_id,
            topics=[WORLD_SUMMARY_TOPIC, CMD_VEL_TOPIC, COSTMAP_TOPIC],
            gate="closed",
        )

    def stop(self) -> None:
        """Signal both threads to exit. The DDS objects stay alive on purpose.

        Tearing the Domain down here would make a restart of the link a restart
        of the whole domain, and a half-torn-down domain is the silent-delivery
        failure this module is written around.
        """
        self.disable("link stopped")
        self._stop.set()

    # -- world summary -------------------------------------------------------

    def _ingest_report(self, data: str) -> bool:
        """Parse one world-summary payload. Returns True if it was accepted."""
        try:
            report = json.loads(data)
            if not isinstance(report, dict):
                raise ValueError(f"report is {type(report).__name__}, not an object")
        except Exception as exc:
            self.reports_rejected += 1
            log.warning("perception.report.unparseable", error=str(exc))
            return False

        version = report.get("report_version")
        if version not in SUPPORTED_REPORT_VERSIONS:
            # Loudly, and we keep the previous report rather than half-reading
            # this one. A newer container may have changed what a field *means*,
            # and best-effort parsing of that produces a confident, wrong world
            # model — the exact failure "absent is not empty" exists to prevent.
            self.reports_rejected += 1
            log.error(
                "perception.report.unsupported_version",
                report_version=version,
                supported=sorted(SUPPORTED_REPORT_VERSIONS),
            )
            return False

        with self._lock:
            self._report = report
            self._report_at = self._clock()
            self.reports_received += 1
        return True

    def latest_report(self) -> tuple[dict[str, Any] | None, float | None]:
        """The newest accepted report and its age, or `(None, None)`.

        A report older than REPORT_OFFLINE_AFTER_S is dropped rather than
        returned with a large age. Handing a stale scene to `from_report` would
        produce a snapshot whose sources all say "ok" about a container that
        died two seconds ago; degrading to "no perception at all" is the honest
        statement, and world_model.build() turns that into explicit offline
        sources rather than an empty scene.
        """
        with self._lock:
            report, at = self._report, self._report_at
        if report is None or at is None:
            return None, None
        age = self._clock() - at
        if age > REPORT_OFFLINE_AFTER_S:
            return None, None
        return report, age

    # -- the gate ------------------------------------------------------------

    def enable(self, *, reason: str = "", ttl_s: float | None = ARM_TTL_S) -> None:
        """Arm actuation. Nothing reaches SET_VELOCITY before this is called.

        `ttl_s` re-closes the gate on its own; call `enable()` again to renew.
        A gate that stays open because nobody remembered to close it is the same
        failure as no gate.
        """
        now = self._clock()
        with self._lock:
            self._enabled = True
            self._armed_until = None if ttl_s is None else now + ttl_s
            self._disabled_reason = ""
        log.warning("perception.gate.enabled", reason=reason or "unspecified", ttl_s=ttl_s)

    def disable(self, reason: str = "") -> None:
        """Close the gate. Unilateral: one boolean, needs nothing from anyone.

        `stop_everything` MUST call this. Zeroing velocity without closing the
        gate would be undone by Nav2's next tick 50 ms later, which makes "stop"
        mean "pause". The brake window below covers the gap between closing the
        gate and the firmware's own deadman.
        """
        now = self._clock()
        with self._lock:
            was_enabled = self._enabled
            self._enabled = False
            self._armed_until = None
            self._disabled_reason = reason or "disabled"
            self._setpoint = None
            self._cmd_at = None
            if was_enabled:
                self._brake_until = now + BRAKE_AFTER_STALE_S
        if was_enabled:
            log.warning("perception.gate.disabled", reason=reason or "unspecified")

    def is_enabled(self) -> bool:
        with self._lock:
            return self._gate_open_locked(self._clock())

    def _gate_open_locked(self, now: float) -> bool:
        if not self._enabled:
            return False
        if self._armed_until is not None and now >= self._armed_until:
            # Expiry is evaluated lazily rather than by a timer so there is one
            # place that decides "open", and it is the same place every reader
            # of the flag goes through.
            self._enabled = False
            self._disabled_reason = "arm TTL expired"
            self._brake_until = now + BRAKE_AFTER_STALE_S
            return False
        return True

    # -- cmd_vel -------------------------------------------------------------

    def _apply_cmd_vel(self, vx: float, vy: float, vyaw: float) -> bool:
        """Accept one setpoint from Nav2. Returns True if it was kept.

        Clamping happens before the gate check so the clamp counter reflects
        what the planner is actually asking for even during Stage 4, when the
        gate is closed on purpose and nothing is meant to move.
        """
        cx, cy, cw = _clamp(vx, CLAMP_VX), _clamp(vy, CLAMP_VY), _clamp(vyaw, CLAMP_WZ)
        now = self._clock()
        with self._lock:
            self.cmd_vel_received += 1
            self._last_arrival = now
            if (cx, cy, cw) != (vx, vy, vyaw):
                self.clamped += 1
                log.warning(
                    "perception.cmd_vel.clamped",
                    requested=[vx, vy, vyaw],
                    applied=[cx, cy, cw],
                )
            if not self._gate_open_locked(now):
                self.dropped_while_disabled += 1
                return False
            self._setpoint = (cx, cy, cw)
            self._cmd_at = now
        return True

    def _on_cmd_vel(self, reader: Any) -> None:
        """Drain the newest Twist. `take(1)`, never `take(10)`.

        Only the newest setpoint has meaning: replaying a queue of stale
        velocities into the legs is strictly worse than dropping them, because
        each one was computed for a pose the robot has already left.
        """
        for sample in reader.take(1):
            try:
                self._apply_cmd_vel(
                    float(sample.linear.x), float(sample.linear.y), float(sample.angular.z)
                )
            except Exception as exc:  # a malformed sample must not kill the reader
                log.warning("perception.cmd_vel.parse_failed", error=str(exc))

    def cmd_vel_expired(self) -> bool:
        """True when the gate is open but nothing fresh is arriving.

        Polled by `watchdog.py`: a silent planner is not a stopped robot, and
        an armed gate with no traffic is the state in which somebody believes
        the robot is under navigation control when it is not.
        """
        now = self._clock()
        with self._lock:
            if not self._gate_open_locked(now):
                return False
            if self._cmd_at is None:
                return True
            return (now - self._cmd_at) > CMD_VEL_DEADMAN_S

    # -- the issue loop ------------------------------------------------------

    def _issue(self, setpoint: tuple[float, float, float], now: float) -> None:
        try:
            self._forward(*setpoint)
        except Exception:
            # Same reasoning as _locomotion.send_velocity's swallowed RPC error:
            # this runs at 10 Hz and the next tick re-sends. Raising here would
            # kill the only thread that can still brake the robot.
            log.exception("perception.cmd_vel.forward_failed")
            return
        with self._lock:
            self.last_sent = setpoint
            self.last_sent_at = now

    def _tick(self) -> None:
        """One pass of the re-issue loop. Pure enough to test without DDS."""
        now = self._clock()
        with self._lock:
            open_ = self._gate_open_locked(now)
            setpoint = self._setpoint
            cmd_at = self._cmd_at
            brake_until = self._brake_until

        if not open_:
            # Brake through the window opened by disable()/TTL expiry, then stop
            # talking entirely — a closed gate must not be a source of traffic.
            if now < brake_until:
                self._issue((0.0, 0.0, 0.0), now)
            return

        if cmd_at is None or setpoint is None:
            # Armed, but nothing has planned yet. Silence, not zeros: we have
            # nothing to say and saying zeros would look like a live commander.
            return

        age = now - cmd_at
        if age <= CMD_VEL_DEADMAN_S:
            self._issue(setpoint, now)
            return
        if age <= CMD_VEL_DEADMAN_S + BRAKE_AFTER_STALE_S:
            self._issue((0.0, 0.0, 0.0), now)
            return
        # Past the brake window: fall silent and let the firmware's 1 s
        # SET_VELOCITY deadman be the floor. Nothing below us needs our help.

    def _run(self) -> None:
        period = 1.0 / ISSUE_HZ
        while not self._stop.wait(period):
            try:
                self._tick()
            except Exception:
                log.exception("perception.issue_loop.failed")

    def _read_loop(self) -> None:
        period = 1.0 / READER_POLL_HZ
        while not self._stop.wait(period):
            try:
                if self._summary_reader is not None:
                    for sample in self._summary_reader.take(1):
                        self._ingest_report(sample.data)
                if self._cmd_vel_reader is not None:
                    self._on_cmd_vel(self._cmd_vel_reader)
                if self._costmap_reader is not None:
                    for sample in self._costmap_reader.take(1):
                        self._ingest_costmap(sample.data)
            except Exception:
                log.exception("perception.read_loop.failed")

    # -- costmap -------------------------------------------------------------

    def _ingest_costmap(self, data: str) -> bool:
        """Parse one costmap payload. Pure telemetry — it can move nothing.

        Held as the parsed dict rather than decoded bytes: the PNG is already
        the transport format the browser wants, so the bridge never decodes it.
        This hop stays a pass-through, which is why a malformed costmap costs a
        counter and a log line rather than anything structural.
        """
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError(f"costmap is {type(payload).__name__}, not an object")
            if int(payload.get("v", 0)) != 1:
                raise ValueError(f"unsupported costmap schema v={payload.get('v')}")
            if not payload.get("png_base64"):
                raise ValueError("costmap carries no png_base64")
        except Exception as exc:
            self.costmaps_rejected += 1
            log.warning("perception.costmap.unparseable", error=str(exc))
            return False

        with self._lock:
            self._costmap = payload
            self._costmap_at = self._clock()
        self.costmaps_received += 1
        return True

    def latest_costmap(self) -> tuple[dict[str, Any] | None, float | None]:
        """The newest costmap and its age in seconds, or (None, None).

        Deliberately does NOT drop a stale costmap the way `latest_report`
        drops a stale report. A two-minute-old map is still the best picture
        available and is worth showing — but the age comes back with it so the
        caller can say so, and `costmap_status()` marks it stale past
        COSTMAP_STALE_AFTER_S. Showing an old map is fine; showing an old map
        as though it were current is not.
        """
        with self._lock:
            payload, at = self._costmap, self._costmap_at
        if payload is None or at is None:
            return None, None
        return payload, max(0.0, self._clock() - at)

    def costmap_status(self) -> dict[str, Any]:
        payload, age = self.latest_costmap()
        return {
            "present": payload is not None,
            "age_s": None if age is None else round(age, 2),
            "stale": bool(age is not None and age > COSTMAP_STALE_AFTER_S),
            "received": self.costmaps_received,
            "rejected": self.costmaps_rejected,
            "width": None if payload is None else payload.get("width"),
            "height": None if payload is None else payload.get("height"),
            "resolution_m": None if payload is None else payload.get("resolution_m"),
        }

    # -- introspection -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Gate state — what Stage 4 reads while Nav2 plans against a shut gate."""
        now = self._clock()
        with self._lock:
            enabled = self._enabled and (
                self._armed_until is None or now < self._armed_until
            )
            return {
                "enabled": enabled,
                "disabled_reason": None if enabled else (self._disabled_reason or "disabled"),
                "arm_expires_in_s": (
                    None
                    if not enabled or self._armed_until is None
                    else round(self._armed_until - now, 2)
                ),
                "cmd_vel_received": self.cmd_vel_received,
                "dropped_while_disabled": self.dropped_while_disabled,
                "clamped": self.clamped,
                "last_sent": list(self.last_sent) if self.last_sent else None,
                "last_sent_age_s": (
                    round(now - self.last_sent_at, 3) if self.last_sent_at else None
                ),
                "cmd_vel_age_s": (
                    round(now - self._last_arrival, 3) if self._last_arrival else None
                ),
                "clamps": {
                    "vx": list(CLAMP_VX),
                    "vy": list(CLAMP_VY),
                    "vyaw": list(CLAMP_WZ),
                },
            }

    def diagnostics(self) -> dict[str, Any]:
        """Everything Stage 0's 30-second crossing test needs to print.

        `reports_received: 0` with `started: true` and no exception is the
        CORRECT result before any container exists — it says the participant
        came up on domain 42 and nothing is publishing yet.
        """
        report, age = self.latest_report()
        with self._lock:
            raw_age = (
                round(self._clock() - self._report_at, 3) if self._report_at else None
            )
        return {
            "domain_id": self._domain_id,
            "started": self._started,
            "topics": {
                "world_summary": WORLD_SUMMARY_TOPIC,
                "cmd_vel": CMD_VEL_TOPIC,
            },
            "reports_received": self.reports_received,
            "reports_rejected": self.reports_rejected,
            # `report_age_s` is None once the report has aged past
            # REPORT_OFFLINE_AFTER_S; `last_report_age_s` keeps counting, so a
            # dead publisher is distinguishable from one that never existed.
            "report_age_s": round(age, 3) if age is not None else None,
            "last_report_age_s": raw_age,
            "report_present": report is not None,
            "gate": self.status(),
        }


def _default_forward(vx: float, vy: float, vyaw: float) -> None:
    """The one place Nav2's opinion becomes leg motion.

    Imported lazily so this module can be imported (and tested) without DDS,
    and routed through `_locomotion.send_velocity` so the SIM_MODE dispatch,
    the SET_VELOCITY api_id and the 1 s firmware duration all stay in exactly
    one place. `height` is not ours to choose — Nav2 has no opinion about
    stand height and a Twist carries none.
    """
    from bridge.skills._locomotion import DEFAULT_HEIGHT, send_velocity

    send_velocity(vx, vy, vyaw, DEFAULT_HEIGHT)


_link_singleton: PerceptionLink | None = None
_link_lock = threading.Lock()


def get_link() -> PerceptionLink:
    """Module-level singleton — this is what holds the Domain alive.

    Not merely convenience: `Entity.__del__` calls `dds_delete`, so the last
    reference to the Domain going out of scope tears down domain 42 and every
    reader under it, with no error anywhere. Never construct a second
    PerceptionLink for the same domain, and never let this one be collected.
    """
    global _link_singleton
    with _link_lock:
        if _link_singleton is None:
            _link_singleton = PerceptionLink()
        return _link_singleton
