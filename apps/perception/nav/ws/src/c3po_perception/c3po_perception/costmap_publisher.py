"""Nav2's global costmap -> one small PNG on domain 42, for the operator console.

    /global_costmap/costmap  (nav_msgs/OccupancyGrid)
        -> /c3po/costmap     (std_msgs/String, JSON with a base64 PNG)

WHY THE COSTMAP AND NOT FAST-LIO'S POINT CLOUD
----------------------------------------------
`/Laser_map` is the largest payload FAST-LIO produces and it grows with mapped
area — it is the one unbounded allocation in the stack, which is why
`map_en: false` in fastlio_mid360_g1.yaml. The global costmap is bounded by
construction (a rolling 24 x 24 m window at 0.10 m), already published at 1 Hz,
and — the part that actually matters — it is WHAT NAV2 PLANS AGAINST. An
operator debugging a refused or looping path needs to see the planner's belief,
not a prettier rendering of the raw returns.

WHY THIS IS SAFE TO PUT ON DOMAIN 42, WHEN POINT CLOUDS ARE NOT
---------------------------------------------------------------
`net.core.rmem_max` on this Jetson is 212992 (208 KiB, untouched default). One
~20k-point PointCloud2 is ~520 KB in ~390 fragments at 10 Hz: it drops, and
reliable QoS turns the drops into a NACK storm. A 240 x 240 costmap is 57,600
cells raw and compresses to well under a kilobyte — three orders of magnitude
smaller, at 1 Hz. Measured, not assumed; and MAX_ENCODED_BYTES below refuses to
publish if that ever stops being true.

WHY JSON-WRAPPED BASE64 AND NOT A BYTE TOPIC
--------------------------------------------
Same reason the world model is a std_msgs/String: it keeps ONE wire pattern
across this boundary and needs no IDL on the bridge side. base64 costs 33 %,
which on a ~600 byte payload is ~200 bytes. A second message type — and a
second hand-written struct in apps/bridge that must stay in lockstep forever —
is not worth saving that.

QoS IS LOAD-BEARING
-------------------
Nav2's costmap publishers are TRANSIENT_LOCAL (latched). A default subscription
is VOLATILE and will simply never receive the map, with no error — the same
silent-nothing failure this project keeps meeting. The profile below matches
deliberately; do not "simplify" it to a depth-only int.
"""

from __future__ import annotations

import base64
import json

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from c3po_perception.costmap_png import encode_occupancy_png

# Bumped when the JSON shape changes in a way a consumer would notice. The
# bridge rejects anything else loudly rather than half-parsing it.
COSTMAP_SCHEMA_VERSION = 1

OUT_TOPIC = "/c3po/costmap"
IN_TOPIC = "/global_costmap/costmap"

# Refuse rather than flood. At 0.10 m this is a 100 x 100 m window, far beyond
# the 24 x 24 m the costmap is configured for — so hitting it means someone
# changed the costmap size without thinking about this hop.
MAX_CELLS = 1_000_000

# A costmap that will not compress is a costmap that should not be on this
# domain. ~600 bytes is typical; 256 KiB is past `net.core.rmem_max` and into
# the fragmentation regime that makes reliable QoS misbehave.
MAX_ENCODED_BYTES = 256 * 1024


class CostmapPublisher(Node):
    def __init__(self) -> None:
        super().__init__("costmap_publisher")

        self.declare_parameter("in_topic", IN_TOPIC)
        self.declare_parameter("out_topic", OUT_TOPIC)
        # 0 disables throttling. The global costmap publishes at 1 Hz already,
        # so the default passes everything through; this exists for the local
        # costmap, which is 2 Hz and could be added later.
        self.declare_parameter("max_hz", 0.0)

        in_topic = self.get_parameter("in_topic").value
        out_topic = self.get_parameter("out_topic").value
        self._min_period = 0.0
        max_hz = float(self.get_parameter("max_hz").value)
        if max_hz > 0:
            self._min_period = 1.0 / max_hz

        # Match Nav2's latched costmap publisher exactly — see the docstring.
        costmap_qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        self._pub = self.create_publisher(String, out_topic, 1)
        self.create_subscription(OccupancyGrid, in_topic, self._on_costmap, costmap_qos)

        self._last_sent = 0.0
        self._published = 0
        self._refused = 0
        self.get_logger().info(f"costmap_publisher: {in_topic} -> {out_topic}")

    def _on_costmap(self, msg: OccupancyGrid) -> None:
        now = self.get_clock().now().nanoseconds * 1e-9
        if self._min_period and (now - self._last_sent) < self._min_period:
            return

        info = msg.info
        cells = info.width * info.height
        if cells <= 0 or cells > MAX_CELLS:
            self._refuse(f"costmap is {info.width}x{info.height} = {cells} cells")
            return

        try:
            png = encode_occupancy_png(msg.data, info.width, info.height)
        except ValueError as exc:
            # Metadata and data disagree. Publishing a partial map would be
            # worse than publishing none — the operator cannot tell a truncated
            # map from a small one.
            self._refuse(str(exc))
            return

        if len(png) > MAX_ENCODED_BYTES:
            self._refuse(f"encoded costmap is {len(png)} bytes, over the domain-42 budget")
            return

        # Everything the console needs to place the image under the robot
        # marker. origin is the pose of cell (0,0) — the BOTTOM-LEFT corner —
        # in `frame_id`, and the PNG is emitted top-down, so a renderer draws it
        # from (origin_x, origin_y + height*resolution) downward.
        payload = {
            "v": COSTMAP_SCHEMA_VERSION,
            "stamp_unix": msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            "frame_id": msg.header.frame_id,
            "width": int(info.width),
            "height": int(info.height),
            "resolution_m": round(float(info.resolution), 4),
            "origin_x_m": round(float(info.origin.position.x), 3),
            "origin_y_m": round(float(info.origin.position.y), 3),
            "png_base64": base64.b64encode(png).decode("ascii"),
        }
        self._pub.publish(String(data=json.dumps(payload, separators=(",", ":"))))
        self._last_sent = now
        self._published += 1
        if self._published == 1 or self._published % 60 == 0:
            self.get_logger().info(
                f"costmap #{self._published}: {info.width}x{info.height} "
                f"@{info.resolution:.2f}m -> {len(png)} B png"
            )

    def _refuse(self, why: str) -> None:
        self._refused += 1
        # throttle_duration_sec so a persistently bad costmap does not fill the
        # container log at the costmap's own rate.
        self.get_logger().warn(f"costmap refused: {why}", throttle_duration_sec=10.0)


def main() -> None:
    rclpy.init()
    node = CostmapPublisher()
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
