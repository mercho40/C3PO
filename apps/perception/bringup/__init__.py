"""Perception bring-up, as data rather than as a shell script.

`scripts/robot/perception_up` was 381 lines of bash holding three near-identical
`docker create` invocations, a stage table expressed as `case` statements, and
the sensor-arbitration rules. Every part of that is a decision — which stage
claims what, which container gets `--runtime nvidia`, which one must NOT be torn
down by the others — and none of it could be exercised without a robot. The bugs
went with it: `perception_up stt` tearing down a running nav2, a stage claiming a
LiDAR it no longer needed, a readiness gate reporting "perception up" while the
camera was dead.

Here the decisions are values, the docker calls go through one injectable
runner, and `apps/perception/tests` checks both without a Jetson.

STDLIB ONLY, AND PYTHON 3.8. This runs on the robot under the system
interpreter, not the bridge's venv — the whole point is that bringing perception
up must not depend on `uv sync` having worked. 3.8 is the floor the rest of this
app already targets (`ruff target-version = py38`).
"""
