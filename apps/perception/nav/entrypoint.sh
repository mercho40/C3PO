#!/usr/bin/env bash
# Both overlays, in order. Sourcing only /opt/ros/humble — what the upstream ros
# image's entrypoint does — leaves livox_ros_driver2, fast_lio and
# c3po_perception invisible, which surfaces as "package not found" from
# `ros2 launch` rather than as anything pointing at the entrypoint.
set -e
source /opt/ros/humble/setup.bash
source /opt/c3po/ws/install/setup.bash
exec "$@"
