"""The fake stack must satisfy every topic and frame the costmap config names.

`nav2-fake` exists to exercise the Nav2 lifecycle, the planner, the controller
and the /c3po/cmd_vel path with NO sensors claimed, so it can run while the
Livox and the RealSense stay with gemm. That only works while the synthetic
sources cover everything nav2_params.yaml actually subscribes to.

Twice now they have not, and both times the failure pointed somewhere else:

  * no odom -> base_footprint TF: costmaps never updated, and it read as a Nav2
    misconfiguration rather than a missing transform.
  * no /cloud_registered_body: local_costmap's buffer went stale and
    controller_server aborted follow_path — while the goal was still ACCEPTED
    and PLANNED, because global_costmap lists only `scan`. That looks exactly
    like a controller tuning problem.

Neither is caught by importing anything: the config is YAML and the fake stack
is launch files. So this reads both as text and checks they agree. It is a
contract test, not a smoke test — it cannot tell you the fake data is sensible,
only that nothing the costmaps require is entirely absent.
"""

from __future__ import annotations

import re

from conftest import NAV_PKG

NAV2_PARAMS = NAV_PKG / "config" / "nav2_params.yaml"
NAV2_LAUNCH = NAV_PKG / "launch" / "nav2.launch.py"
FAKE_LAUNCH = NAV_PKG / "launch" / "fake.launch.py"


def _fake_mode_text() -> str:
    """Everything that runs under `sources:=fake`, as one blob.

    fake.launch.py is the sensor-free stack shared with Stage 3; nav2.launch.py
    holds the extra scaffolding that exists only because Nav2 is running (the
    static TFs and the empty cloud), each guarded by
    LaunchConfigurationEquals("sources", "fake").
    """
    return NAV2_LAUNCH.read_text() + "\n" + FAKE_LAUNCH.read_text()


def _observation_topics() -> set[str]:
    """Every `topic:` under a costmap obstacle_layer observation source."""
    params = NAV2_PARAMS.read_text()
    return set(re.findall(r"^\s*topic:\s*(/\S+)\s*$", params, re.MULTILINE))


def test_fake_stack_publishes_every_costmap_observation_topic():
    topics = _observation_topics()
    # Guard the regex itself: if the YAML is reshaped so this finds nothing, the
    # assertions below would all pass vacuously and the test would go quiet.
    assert "/scan" in topics, f"parsed observation topics look wrong: {topics}"
    assert "/cloud_registered_body" in topics, (
        f"parsed observation topics look wrong: {topics}")

    fake = _fake_mode_text()
    missing = sorted(t for t in topics if t not in fake)
    assert not missing, (
        f"nav2_params.yaml subscribes to {missing} but nothing in the fake stack "
        "publishes it. Under sources:=fake that buffer never updates and "
        "controller_server aborts follow_path — see nav2.launch.py's fake_cloud."
    )


def test_fake_stack_provides_every_costmap_sensor_frame():
    """`sensor_frame` is the raytrace origin; an unresolvable one drops the cloud.

    A transform exception inside ObservationBuffer produces the same stale-buffer
    symptom as publishing nothing, so publishing the topic without its frame
    would look fixed and fail identically.
    """
    params = NAV2_PARAMS.read_text()
    frames = set(re.findall(r"^\s*sensor_frame:\s*(\w+)", params, re.MULTILINE))
    assert frames, "no sensor_frame found — regex is stale"

    fake = _fake_mode_text()
    missing = sorted(f for f in frames if f not in fake)
    assert not missing, (
        f"nav2_params.yaml raytraces from {missing}, which the fake stack never "
        "broadcasts. Every cloud is then dropped on a transform exception."
    )


def test_fake_cloud_declares_xyz_fields():
    """Zero points is fine; zero *fields* throws inside the observation buffer.

    bufferCloud() transforms through tf2, which iterates x/y/z BY NAME and
    raises std::runtime_error when they are absent — so an empty cloud is only
    safe while it still declares its fields.
    """
    launch = NAV2_LAUNCH.read_text()
    cloud = re.search(r"FAKE_CLOUD = \((.*?)\n\)", launch, re.DOTALL)
    assert cloud, "FAKE_CLOUD literal not found in nav2.launch.py"
    body = cloud.group(1)
    for field in ("x", "y", "z"):
        assert f"name: {field}," in body, (
            f"FAKE_CLOUD must declare the '{field}' field even with zero points")
    assert "width: 0" in body, (
        "FAKE_CLOUD is meant to carry NO points — this source is marking-only, "
        "so invented points would mark obstacles no fake scan can ever clear")


def test_fake_cloud_outruns_the_expected_update_rate():
    """Publishing the topic too slowly is the same failure, just slower.

    `expected_update_rate: 0.5` means the buffer is stale after half a second,
    so anything at or below 2 Hz still aborts follow_path.
    """
    launch = NAV2_LAUNCH.read_text()
    hz = re.search(r'FAKE_CLOUD_HZ = "([\d.]+)"', launch)
    assert hz, "FAKE_CLOUD_HZ not found in nav2.launch.py"

    params = NAV2_PARAMS.read_text()
    rates = [float(r) for r in
             re.findall(r"^\s*expected_update_rate:\s*([\d.]+)", params, re.MULTILINE)]
    assert rates, "no expected_update_rate found — regex is stale"

    assert float(hz.group(1)) > 1.0 / min(rates), (
        f"fake cloud at {hz.group(1)} Hz cannot keep a buffer fresh that expects "
        f"an update every {min(rates)} s")


# --- the LiDAR source ------------------------------------------------------
#
# Our own driver does NOT get the sensor while the vendor `lidar_driver` service
# holds it: measured 2026-08-21, it reported success and published nothing while
# rt/utlidar/* kept flowing at 10 Hz. The republish is the route that works.

def test_the_lidar_defaults_to_the_republish_not_our_own_driver():
    launch = (NAV_PKG / "launch" / "odometry.launch.py").read_text()
    decl = launch.split('DeclareLaunchArgument("lidar_source"', 1)[-1].split(")", 1)[0]
    assert '"republish"' in decl, (
        "lidar_source must default to the republish — our own driver silently "
        "publishes nothing while the vendor service holds the sensor")


def test_the_republish_config_reads_the_velodyne_layout():
    """The republish is a PointCloud2 with ring+time, not livox CustomMsg.

    lidar_type 1 expects CustomMsg: the callback never fires, and FAST-LIO sits
    silent with no error — the same shape as the first-light failure. 2 is the
    velodyne reader, which is what ring+time actually is.
    """
    cfg = (NAV_PKG / "config" / "fastlio_utlidar_g1.yaml").read_text()
    assert "lidar_type: 2" in cfg
    assert "/utlidar/cloud_livox_mid360" in cfg
    assert "/utlidar/imu_livox_mid360" in cfg, "FAST-LIO needs the IMU too"


def test_the_domain_bridge_carries_named_topics_only():
    """Domain 42 exists so our TF, costmaps and velocities cannot reach the wire
    the control board and the co-tenant share. A wildcard bridge would quietly
    undo that, so the config must name its topics."""
    cfg = (NAV_PKG / "config" / "lidar_domain_bridge.yaml").read_text()
    assert "from_domain: 0" in cfg and "to_domain: 42" in cfg
    assert "*" not in cfg.split("topics:", 1)[-1], "no wildcards across the domain boundary"
