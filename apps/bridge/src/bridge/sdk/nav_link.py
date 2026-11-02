"""Nav2 goals out, Nav2 progress in — the bridge half of D4 on domain 42.

    rt/c3po/nav_goal     std_msgs::msg::dds_::String_ (JSON)   write, on demand
    rt/c3po/nav_status   std_msgs::msg::dds_::String_ (JSON)   read, ~2 Hz heartbeat

The container half is apps/perception/nav/.../goal_relay.py, which turns each
goal into a NavigateToPose action call; both shapes are defined (and tested
against this module) in c3po_perception/nav_goal.py. Plain CycloneDDS, no ROS,
for the reasons perception_link.py's docstring gives.

A GOAL IS NOT PERMISSION TO MOVE. Nav2's velocity still arrives on
rt/c3po/cmd_vel and still dies at PerceptionLink's default-closed gate unless
an operator ran `arm_navigation`. This module never touches that gate; the MCP
tools refuse to send a goal while it is shut, because a goal that plans against
a closed gate just runs Nav2's progress checker out and reads as "aborted".

It rides on PerceptionLink's participant rather than creating its own: that
participant was made from the one explicit domain-42 config, and a second
`Domain(42)` would be a second copy of it to keep matching. So this starts
after `get_link().start()`, and refuses to start without it.

The writer is created at start(), not on the first goal. Domain 42 is
VOLATILE: a writer made at the moment of need publishes before discovery has
matched the relay, and the first goal of every session would vanish.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)

NAV_GOAL_TOPIC = "rt/c3po/nav_goal"
NAV_STATUS_TOPIC = "rt/c3po/nav_status"
NAV_SCHEMA_VERSION = 1

# The relay heartbeats at 2 Hz; four missed beats is a relay that is gone.
STATUS_OFFLINE_AFTER_S = 2.0
READER_POLL_HZ = 20.0

TERMINAL_STATES = frozenset({"succeeded", "canceled", "aborted", "rejected"})


def new_goal_id() -> str:
    return f"nav_{uuid.uuid4().hex[:10]}"


def goal_message(goal_id: str, x: float, y: float, yaw: float, frame: str = "") -> str:
    """One goal. `frame=""` means the relay's own global frame (map or odom)."""
    return json.dumps(
        {"v": NAV_SCHEMA_VERSION, "id": goal_id, "x": x, "y": y, "yaw": yaw, "frame": frame},
        separators=(",", ":"),
    )


def cancel_message(goal_id: str | None = None) -> str:
    """Cancel one goal, or — with no id — whatever goal is active."""
    msg: dict[str, Any] = {"v": NAV_SCHEMA_VERSION, "cancel": True}
    if goal_id is not None:
        msg["id"] = goal_id
    return json.dumps(msg, separators=(",", ":"))


class NavLink:
    def __init__(
        self,
        *,
        publish: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._publish_fn = publish
        self._clock = clock
        self._lock = threading.Lock()
        # Held for process lifetime — see perception_link.py: a dropped DDS
        # entity is deleted, and delivery stops with no error.
        self._writer: Any = None
        self._reader: Any = None
        self._topics: list[Any] = []
        self._status: dict[str, Any] | None = None
        self._status_at: float | None = None
        self.statuses_received = 0
        self.statuses_rejected = 0
        self.goals_sent = 0
        self.cancels_sent = 0
        self._started = False
        self._stop = threading.Event()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        from cyclonedds.pub import DataWriter
        from cyclonedds.qos import Policy, Qos
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic
        from cyclonedds.util import duration
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

        from bridge.sdk.perception_link import get_link

        participant = get_link().participant
        if participant is None:
            raise RuntimeError("perception link not started: no domain-42 participant")

        goal_topic = Topic(participant, NAV_GOAL_TOPIC, String_)
        status_topic = Topic(participant, NAV_STATUS_TOPIC, String_)
        self._topics = [goal_topic, status_topic]
        # RELIABLE with a short history, so a cancel written right behind its
        # goal does not overwrite the goal in a depth-1 writer cache.
        self._writer = DataWriter(
            participant,
            goal_topic,
            qos=Qos(
                Policy.Reliability.Reliable(duration(milliseconds=200)), Policy.History.KeepLast(10)
            ),
        )
        self._reader = DataReader(participant, status_topic, qos=None)
        self._string = String_
        self._started = True
        threading.Thread(target=self._read_loop, name="nav-status", daemon=True).start()
        log.info("nav.link.ready", topics=[NAV_GOAL_TOPIC, NAV_STATUS_TOPIC])

    def _read_loop(self) -> None:
        period = 1.0 / READER_POLL_HZ
        while not self._stop.wait(period):
            try:
                for sample in self._reader.take(1):
                    self._ingest_status(sample.data)
            except Exception:
                log.exception("nav.read_loop.failed")

    @property
    def can_publish(self) -> bool:
        return self._publish_fn is not None or self._writer is not None

    def _publish(self, data: str) -> None:
        if self._publish_fn is not None:
            self._publish_fn(data)
            return
        if self._writer is None:
            raise RuntimeError("nav link not started")
        self._writer.write(self._string(data=data))

    # -- outbound ------------------------------------------------------------

    def send_goal(self, x: float, y: float, yaw: float, frame: str = "") -> str:
        goal_id = new_goal_id()
        self._publish(goal_message(goal_id, x, y, yaw, frame))
        self.goals_sent += 1
        log.warning("nav.goal.sent", goal_id=goal_id, x=x, y=y, yaw=yaw, frame=frame or None)
        return goal_id

    def cancel(self, goal_id: str | None = None) -> None:
        self._publish(cancel_message(goal_id))
        self.cancels_sent += 1
        log.warning("nav.goal.cancel_sent", goal_id=goal_id)

    # -- inbound -------------------------------------------------------------

    def _ingest_status(self, data: str) -> bool:
        try:
            status = json.loads(data)
            if not isinstance(status, dict):
                raise ValueError(f"status is {type(status).__name__}, not an object")
            if status.get("v") != NAV_SCHEMA_VERSION:
                raise ValueError(f"unsupported nav status schema v={status.get('v')!r}")
            if not isinstance(status.get("session"), str):
                raise ValueError("status carries no session")
            goal = status.get("goal")
            if goal is not None and not (
                isinstance(goal, dict) and "id" in goal and "state" in goal
            ):
                raise ValueError("status goal has no id/state")
        except Exception as exc:
            self.statuses_rejected += 1
            log.warning("nav.status.unparseable", error=str(exc))
            return False
        with self._lock:
            self._status = status
            self._status_at = self._clock()
        self.statuses_received += 1
        return True

    def latest_status(self) -> tuple[dict[str, Any] | None, float | None]:
        """The newest heartbeat and its age, or (None, None) once it is stale.

        Dropped rather than aged, like the world summary: a "goal active" from a
        relay that died three seconds ago is a confident lie about the robot.
        """
        with self._lock:
            status, at = self._status, self._status_at
        if status is None or at is None:
            return None, None
        age = self._clock() - at
        if age > STATUS_OFFLINE_AFTER_S:
            return None, None
        return status, age

    def goal(self, goal_id: str) -> dict[str, Any] | None:
        """This goal's record from the newest live heartbeat, if it is the current one."""
        status, _ = self.latest_status()
        goal = (status or {}).get("goal")
        return goal if goal and goal.get("id") == goal_id else None

    def diagnostics(self) -> dict[str, Any]:
        status, age = self.latest_status()
        return {
            "started": self._started,
            "relay_online": status is not None,
            "status_age_s": None if age is None else round(age, 2),
            "statuses_received": self.statuses_received,
            "statuses_rejected": self.statuses_rejected,
            "goals_sent": self.goals_sent,
            "cancels_sent": self.cancels_sent,
        }


_nav_singleton: NavLink | None = None
_nav_lock = threading.Lock()


def get_nav_link() -> NavLink:
    global _nav_singleton
    with _nav_lock:
        if _nav_singleton is None:
            _nav_singleton = NavLink()
        return _nav_singleton
