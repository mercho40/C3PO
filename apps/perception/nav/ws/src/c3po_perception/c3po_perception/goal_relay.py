"""JSON goals from the bridge -> Nav2's NavigateToPose action, and its progress back.

    /c3po/nav_goal    (std_msgs/String JSON)  -> navigate_to_pose (nav2_msgs/action/NavigateToPose)
    /c3po/nav_status  (std_msgs/String JSON)  <- feedback, result, and a 2 Hz heartbeat

WHY A RELAY AND NOT AN ACTION CLIENT IN THE BRIDGE
--------------------------------------------------
A ROS action is three services and two topics with generated types. Speaking
it from the bridge would mean rosidl on the host or five hand-written IDLs that
must match Humble's byte-for-byte — exactly what D2 put the container boundary
there to avoid. One String in, one String out, the same pattern as the world
summary; the shapes live in nav_goal.py, which is pure and tested off-robot.

THIS NODE DOES NOT MAKE THE ROBOT MOVE
--------------------------------------
It hands Nav2 a goal. Nav2's velocity still leaves on /c3po/cmd_vel and still
dies at the bridge's default-closed gate unless an operator ran
`arm_navigation` (D2.1). A goal sent while the gate is shut plans, publishes
and moves nothing — Stage 4's reading. It does not activate Nav2's lifecycle
either: with `autostart: false` the action server simply is not there, which
the heartbeat reports as `nav2_ready: false`, and a goal sent then is
`rejected` with that reason rather than queued for later. A queued goal that
starts walking when someone activates Nav2 an hour later is the failure.

The pose in the heartbeat is the robot in `global_frame` from TF — `map` when
slam_toolbox is localizing, `odom` otherwise. The bridge stores it with each
landmark so `navigate_to_landmark` can send coordinates in a frame Nav2 owns,
rather than the vendor leg odometry `get_state` reports (a different origin).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.time import Time
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from c3po_perception.nav_goal import (
    TERMINAL_STATES,
    duration_s,
    goal_record,
    parse_goal,
    quaternion_to_yaw,
    state_from_goal_status,
    status_payload,
    yaw_to_quaternion,
)

GOAL_TOPIC = "/c3po/nav_goal"
STATUS_TOPIC = "/c3po/nav_status"
STATUS_HZ = 2.0


class GoalRelay(Node):
    def __init__(self) -> None:
        super().__init__("goal_relay")
        self.declare_parameter("global_frame", "odom")
        self.declare_parameter("robot_base_frame", "base_footprint")
        self._frame = str(self.get_parameter("global_frame").value)
        self._base = str(self.get_parameter("robot_base_frame").value)

        # New per process: FAST-LIO's odom re-origins when this container
        # restarts, and so does this id. The bridge uses it to refuse an
        # odom-frame landmark saved in a previous session.
        self._session = uuid.uuid4().hex[:8]

        self._client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._tf = Buffer()
        self._tf_listener = TransformListener(self._tf, self)

        self._goal: Optional[Dict[str, Any]] = None
        self._handle: Any = None
        self._cancel_pending = False

        self._pub = self.create_publisher(String, STATUS_TOPIC, 10)
        self.create_subscription(String, GOAL_TOPIC, self._on_goal, 10)
        self.create_timer(1.0 / STATUS_HZ, self._publish)
        self.get_logger().info(
            f"goal_relay: {GOAL_TOPIC} -> navigate_to_pose in '{self._frame}', "
            f"session {self._session}"
        )

    # -- inbound -------------------------------------------------------------

    def _on_goal(self, msg: String) -> None:
        try:
            goal = parse_goal(json.loads(msg.data))
        except (ValueError, TypeError) as exc:  # JSONDecodeError is a ValueError
            # Refused loudly, never half-read. A goal that cannot be parsed has
            # no id we could report it under, so the log is the only witness.
            self.get_logger().warn(f"nav goal refused: {exc}")
            return

        if goal["cancel"]:
            self._cancel(goal["id"])
            return
        if self._goal is not None and self._goal["id"] == goal["id"]:
            return  # a re-delivery of the goal we are already running

        frame = goal["frame"] or self._frame
        self._goal = goal_record(goal, frame)
        self._handle = None
        self._cancel_pending = False

        if not self._client.server_is_ready():
            self._finish("rejected", "nav2_not_active")
            return

        pose = PoseStamped()
        pose.header.frame_id = frame
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = goal["x"]
        pose.pose.position.y = goal["y"]
        qx, qy, qz, qw = yaw_to_quaternion(goal["yaw"])
        pose.pose.orientation.x, pose.pose.orientation.y = qx, qy
        pose.pose.orientation.z, pose.pose.orientation.w = qz, qw

        request = NavigateToPose.Goal()
        request.pose = pose
        goal_id = goal["id"]
        self.get_logger().info(
            f"goal {goal_id}: ({goal['x']:.2f}, {goal['y']:.2f}, yaw {goal['yaw']:.2f}) in {frame}"
        )
        future = self._client.send_goal_async(
            request, feedback_callback=lambda fb: self._on_feedback(goal_id, fb)
        )
        future.add_done_callback(lambda f: self._on_response(goal_id, f))
        self._publish()

    def _cancel(self, goal_id: Optional[str]) -> None:
        if self._goal is None or self._goal["state"] in TERMINAL_STATES:
            return
        if goal_id is not None and goal_id != self._goal["id"]:
            return
        if self._handle is None:
            # Not accepted yet: cancel the moment it is, never let it run.
            self._cancel_pending = True
            return
        self.get_logger().info(f"goal {self._goal['id']}: cancel requested")
        self._handle.cancel_goal_async()

    # -- Nav2 callbacks ------------------------------------------------------

    def _current(self, goal_id: str) -> bool:
        # A preempted goal's late callbacks must not overwrite the new goal.
        return self._goal is not None and self._goal["id"] == goal_id

    def _on_response(self, goal_id: str, future: Any) -> None:
        if not self._current(goal_id):
            return
        handle = future.result()
        if not handle.accepted:
            self._finish("rejected", "nav2_rejected_goal")
            return
        self._handle = handle
        self._set_state("accepted")
        handle.get_result_async().add_done_callback(lambda f: self._on_result(goal_id, f))
        if self._cancel_pending:
            self._cancel(goal_id)

    def _on_feedback(self, goal_id: str, msg: Any) -> None:
        if not self._current(goal_id) or self._goal is None:
            return
        fb = msg.feedback
        self._goal["state"] = "active"
        self._goal["distance_remaining_m"] = round(float(fb.distance_remaining), 2)
        eta = fb.estimated_time_remaining
        self._goal["eta_s"] = duration_s(eta.sec, eta.nanosec)
        nav = fb.navigation_time
        self._goal["nav_time_s"] = duration_s(nav.sec, nav.nanosec)
        self._goal["recoveries"] = int(fb.number_of_recoveries)

    def _on_result(self, goal_id: str, future: Any) -> None:
        if not self._current(goal_id):
            return
        code = future.result().status
        state = state_from_goal_status(code)
        # Humble's NavigateToPose result is std_msgs/Empty: there is no error
        # code to forward. Say what we know rather than invent a cause.
        reason = None if code == GoalStatus.STATUS_SUCCEEDED else f"goal_status_{code}"
        self._finish(state, reason)

    # -- outbound ------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if self._goal is not None:
            self._goal["state"] = state
        self._publish()

    def _finish(self, state: str, reason: Optional[str]) -> None:
        if self._goal is not None:
            self._goal["state"] = state
            self._goal["reason"] = reason
            self.get_logger().info(f"goal {self._goal['id']}: {state} ({reason})")
        self._handle = None
        self._publish()

    def _pose(self) -> Optional[Tuple[float, float, float]]:
        try:
            t = self._tf.lookup_transform(self._frame, self._base, Time())
        except TransformException:
            return None  # map->odom not there yet, or no odom: say None, never (0, 0)
        tr, q = t.transform.translation, t.transform.rotation
        return (tr.x, tr.y, quaternion_to_yaw(q.x, q.y, q.z, q.w))

    def _publish(self) -> None:
        payload = status_payload(
            stamp_unix=time.time(),
            session=self._session,
            global_frame=self._frame,
            nav2_ready=self._client.server_is_ready(),
            pose=self._pose(),
            goal=self._goal,
        )
        self._pub.publish(String(data=json.dumps(payload, separators=(",", ":"))))


def main() -> None:
    rclpy.init()
    node = GoalRelay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
