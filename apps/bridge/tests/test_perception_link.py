"""Tests for `bridge.sdk.perception_link` — the gate and the staleness rules.

These run with **no robot, no DDS, no perception stack and no CycloneDDS C
library**: `PerceptionLink.start()` is the only method that touches DDS, and
nothing here calls it. Everything under test is the pure logic between the
reader and the actuator — which is deliberate, because that logic is the part
whose failures are silent:

* a gate that defaults open lets a restarting container walk the robot;
* a stale setpoint re-issued forever means "wedged planner" reads as "walking";
* a report that is two seconds old, reported as current, is a confident and
  wrong description of a room the robot is standing in.

The actuator is a list. If a test ever needs a real one, the test is wrong.
"""

from __future__ import annotations

import json
import pathlib
import re

from bridge.sdk import perception_link as pl


class FakeClock:
    """Monotonic time we control, because every rule here is a deadline."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_link():
    """A link with a fake clock and a list for an actuator. Never started."""
    clock = FakeClock()
    sent: list[tuple[float, float, float]] = []
    link = pl.PerceptionLink(forward=lambda *v: sent.append(v), clock=clock)
    return link, clock, sent


def a_report(**overrides) -> str:
    base = {
        "report_version": 1,
        "stamp_unix": 1_000_000.0,
        "pose": {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 0.0},
        "pose_age_s": 0.1,
        "detector_online": True,
        "objects": [],
        "objects_omitted": 0,
        "lidar_online": True,
        "free_space": {"ahead_m": 2.0},
        "notes": [],
    }
    base.update(overrides)
    return json.dumps(base)


# --------------------------------------------------------------------------
# The gate is DEFAULT CLOSED
# --------------------------------------------------------------------------


def test_a_fresh_link_is_closed_and_has_actuated_nothing():
    link, _clock, sent = make_link()

    assert link.is_enabled() is False
    assert link.status()["enabled"] is False
    assert link.status()["last_sent"] is None
    assert sent == []


def test_cmd_vel_arriving_at_a_closed_gate_is_counted_and_dropped():
    # Stage 4 in one assertion: Nav2 plans, the gate refuses, nothing moves.
    link, _clock, sent = make_link()

    for _ in range(5):
        assert link._apply_cmd_vel(0.3, 0.0, 0.0) is False
    link._tick()

    assert link.status()["dropped_while_disabled"] == 5
    assert link.status()["cmd_vel_received"] == 5
    assert link.status()["last_sent"] is None
    assert sent == []


def test_nothing_reaches_the_actuator_until_enable_is_called():
    link, _clock, sent = make_link()

    link._apply_cmd_vel(0.3, 0.0, 0.0)
    link._tick()
    assert sent == []

    link.enable(reason="test")
    link._apply_cmd_vel(0.3, 0.0, 0.0)
    link._tick()
    assert sent == [(0.3, 0.0, 0.0)]


def test_an_armed_gate_with_no_traffic_still_sends_nothing():
    # Arming is permission, not a command. Zeros here would look to everything
    # downstream like a live commander holding the robot still.
    link, _clock, sent = make_link()
    link.enable(reason="test")

    for _ in range(5):
        link._tick()

    assert sent == []


def test_disable_is_unilateral_and_forgets_the_setpoint():
    # `stop_everything` calls this. It must not depend on the container, on
    # Nav2, or on anything still being alive on domain 42.
    link, _clock, sent = make_link()
    link.enable(reason="test")
    link._apply_cmd_vel(0.4, 0.0, 0.0)

    link.disable("stop_everything")

    assert link.is_enabled() is False
    assert link._apply_cmd_vel(0.4, 0.0, 0.0) is False
    link._tick()
    # What it does send is a brake, never the setpoint it was holding.
    assert sent == [(0.0, 0.0, 0.0)]


def test_disable_brakes_briefly_then_goes_quiet():
    link, clock, sent = make_link()
    link.enable(reason="test")
    link._apply_cmd_vel(0.4, 0.0, 0.0)
    link.disable("stop")

    link._tick()
    clock.advance(pl.BRAKE_AFTER_STALE_S + 0.01)
    before = len(sent)
    link._tick()
    link._tick()

    assert all(v == (0.0, 0.0, 0.0) for v in sent)
    assert len(sent) == before, "a closed gate must stop being a source of traffic"


def test_the_arm_expires_on_its_own():
    # A gate that stays open because nobody remembered to close it is no gate.
    link, clock, sent = make_link()
    link.enable(reason="test", ttl_s=10.0)
    link._apply_cmd_vel(0.3, 0.0, 0.0)
    link._tick()
    assert sent == [(0.3, 0.0, 0.0)]

    clock.advance(11.0)

    assert link.is_enabled() is False
    assert link._apply_cmd_vel(0.3, 0.0, 0.0) is False
    assert "TTL" in (link.status()["disabled_reason"] or "")


def test_an_explicit_none_ttl_never_expires():
    link, clock, _sent = make_link()
    link.enable(reason="supervised window", ttl_s=None)
    clock.advance(10_000.0)
    assert link.is_enabled() is True


# --------------------------------------------------------------------------
# The clamp is ours, and it is the enforcing one
# --------------------------------------------------------------------------


def test_velocity_is_clamped_to_our_own_limits_not_nav2s():
    link, _clock, sent = make_link()
    link.enable(reason="test")

    link._apply_cmd_vel(9.0, -9.0, 9.0)
    link._tick()

    assert sent == [(pl.CLAMP_VX[1], pl.CLAMP_VY[0], pl.CLAMP_WZ[1])]
    assert link.status()["clamped"] == 1


def test_reverse_is_clamped_harder_than_forward():
    link, _clock, sent = make_link()
    link.enable(reason="test")

    link._apply_cmd_vel(-9.0, 0.0, 0.0)
    link._tick()

    assert sent[0][0] == pl.CLAMP_VX[0]
    assert pl.CLAMP_VX[0] > -pl.CLAMP_VX[1], "backwards must be the slower direction"


def test_a_setpoint_inside_the_limits_is_not_counted_as_clamped():
    link, _clock, _sent = make_link()
    link.enable(reason="test")
    link._apply_cmd_vel(0.1, 0.0, 0.1)
    assert link.status()["clamped"] == 0


# --------------------------------------------------------------------------
# The cmd_vel deadman sits ABOVE the firmware's 1 s one
# --------------------------------------------------------------------------


def test_the_deadman_is_shorter_than_the_firmwares():
    from bridge.skills._locomotion import VELOCITY_DURATION_S

    assert pl.CMD_VEL_DEADMAN_S < VELOCITY_DURATION_S
    assert pl.CMD_VEL_DEADMAN_S + pl.BRAKE_AFTER_STALE_S < VELOCITY_DURATION_S


def test_a_fresh_setpoint_is_re_issued_every_tick():
    # The firmware forgets a setpoint after 1 s; re-issuing is what keeps a
    # walk going while Nav2 is quiet between its own 20 Hz ticks.
    link, clock, sent = make_link()
    link.enable(reason="test")
    link._apply_cmd_vel(0.2, 0.0, 0.0)

    # Two ticks inside the deadman window; the third would cross it, which is
    # the next test's subject.
    for _ in range(2):
        clock.advance(1.0 / pl.ISSUE_HZ)
        link._tick()

    assert sent == [(0.2, 0.0, 0.0)] * 2


def test_a_wedged_planner_gets_brakes_then_silence():
    link, clock, sent = make_link()
    link.enable(reason="test", ttl_s=None)
    link._apply_cmd_vel(0.4, 0.0, 0.0)

    # Still fresh.
    link._tick()
    assert sent[-1] == (0.4, 0.0, 0.0)

    # Past our deadman: active braking, well before the firmware's 1 s.
    clock.advance(pl.CMD_VEL_DEADMAN_S + 0.01)
    link._tick()
    assert sent[-1] == (0.0, 0.0, 0.0)

    # Past the brake window: silence, and the firmware deadman is the floor.
    clock.advance(pl.BRAKE_AFTER_STALE_S)
    n = len(sent)
    link._tick()
    link._tick()
    assert len(sent) == n


def test_cmd_vel_expired_reports_a_silent_planner_but_only_when_armed():
    link, clock, _sent = make_link()

    # Closed gate: nothing is expected to arrive, so nothing has expired.
    assert link.cmd_vel_expired() is False

    link.enable(reason="test", ttl_s=None)
    # Armed with nothing arriving is exactly the state worth reporting.
    assert link.cmd_vel_expired() is True

    link._apply_cmd_vel(0.2, 0.0, 0.0)
    assert link.cmd_vel_expired() is False

    clock.advance(pl.CMD_VEL_DEADMAN_S + 0.01)
    assert link.cmd_vel_expired() is True


# --------------------------------------------------------------------------
# A stale report degrades to offline, never to "a current, empty scene"
# --------------------------------------------------------------------------


def test_a_fresh_report_is_returned_with_its_age():
    link, clock, _sent = make_link()
    assert link._ingest_report(a_report()) is True

    clock.advance(0.25)
    report, age = link.latest_report()

    assert report is not None and report["report_version"] == 1
    assert age == 0.25
    assert link.reports_received == 1


def test_a_report_older_than_the_offline_threshold_is_dropped_entirely():
    # Returned-with-a-big-age would let a caller decide to trust it. Handing
    # back None routes it through world_model.build()'s explicit offline
    # degradation instead, which is the only honest answer.
    link, clock, _sent = make_link()
    link._ingest_report(a_report())

    clock.advance(pl.REPORT_OFFLINE_AFTER_S + 0.01)

    assert link.latest_report() == (None, None)
    # ...but the link still knows one arrived, so a dead publisher is
    # distinguishable from one that never existed.
    assert link.reports_received == 1
    assert link.diagnostics()["last_report_age_s"] is not None
    assert link.diagnostics()["report_age_s"] is None


def test_a_report_with_an_unsupported_version_is_refused_not_half_read():
    link, _clock, _sent = make_link()

    assert link._ingest_report(a_report(report_version=2)) is False
    assert link.latest_report() == (None, None)
    assert link.reports_received == 0
    assert link.reports_rejected == 1


def test_an_unparseable_payload_leaves_the_previous_report_alone():
    link, _clock, _sent = make_link()
    link._ingest_report(a_report())

    assert link._ingest_report("{not json") is False
    assert link._ingest_report("[1, 2, 3]") is False

    report, _age = link.latest_report()
    assert report is not None
    assert link.reports_rejected == 2


def test_no_report_at_all_reads_as_absent():
    link, _clock, _sent = make_link()
    assert link.latest_report() == (None, None)
    assert link.diagnostics()["report_present"] is False


# --------------------------------------------------------------------------
# Diagnostics — what Stage 0's crossing test prints
# --------------------------------------------------------------------------


def test_diagnostics_names_the_domain_and_both_topics():
    link, _clock, _sent = make_link()
    d = link.diagnostics()

    assert d["domain_id"] == 42
    assert d["topics"]["world_summary"] == "rt/c3po/world_summary"
    assert d["topics"]["cmd_vel"] == "rt/c3po/cmd_vel"
    # Zero reports before a container exists is the CORRECT Stage 0 result.
    assert d["reports_received"] == 0
    assert d["gate"]["enabled"] is False


def test_domain_xml_carries_the_id_it_is_created_with():
    # `Domain(id, cfg)` ignores a <Domain> block whose id does not match, and
    # ignores it silently — the participant then comes up on defaults, with
    # multicast, on an `lo` that has no MULTICAST flag, and discovers nothing.
    assert '<Domain id="42">' in pl._domain_xml(42)
    assert '<Domain id="7">' in pl._domain_xml(7)


def test_domain_42_is_unicast_loopback():
    xml = pl._domain_xml(42)
    assert "<AllowMulticast>false</AllowMulticast>" in xml
    assert 'name="lo"' in xml
    assert '<Peer address="127.0.0.1"/>' in xml


def test_importing_the_link_pulls_in_no_ros_and_no_dds():
    # The whole D2 premise: the bridge speaks plain CycloneDDS and the ROS graph
    # stops at the container boundary. DDS itself is imported lazily in start().
    import sys

    assert "rclpy" not in sys.modules
    assert not any(m.startswith("rclpy.") for m in sys.modules)


# --------------------------------------------------------------------------
# The three copies of the domain-42 config must agree
# --------------------------------------------------------------------------
#
# There are three of them and there is no way to make there be one: the nav
# container's rclpy nodes can only be configured through CYCLONEDDS_URI (a file,
# bind-mounted from apps/perception/config/), while the vision detector and this
# module both pass an explicit config string to `Domain(...)` because a bare
# participant would inherit whatever the environment happens to carry. Nothing
# at runtime compares them, and a disagreement does not raise — it produces
# participants that come up cleanly and discover a subset of each other. So the
# comparison happens here.
#
# Only the settings that take part in the DISCOVERY HANDSHAKE are compared.
# <Tracing> is deliberately absent from the inline copies (its OutputFile is
# /logs, which exists only inside a container) and <MaxMessageSize> is a
# writer-side fragmentation limit that this read-only participant has no use for.

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SHARED_XML = _REPO_ROOT / "apps" / "perception" / "config" / "cyclonedds-domain42.xml"
_DETECTOR_PY = (_REPO_ROOT / "apps" / "perception" / "vision" / "c3po_vision"
                / "detector.py")

# Each of these, on any side, is a way to build a participant that starts
# cleanly and then sees nothing:
#   AllowMulticast false  — `lo` on this Jetson has no MULTICAST flag at all.
#   name="lo"             — the wrong interface is a different network.
#   Peer 127.0.0.1        — with multicast off, this is the ONLY way anyone is
#                           found.
#   ParticipantIndex auto — "none" makes unicast discovery impossible outright.
#   MaxAutoParticipantIndex 32 — a unicast <Peer> with no port is expanded into
#                           one SPDP locator per index 0..N-1, and N is also the
#                           range a process searches for its own free index. The
#                           Cyclone default is 9; both containers run
#                           --network host, so ~13 domain-42 processes share
#                           this port space with the bridge.
_DISCOVERY_SETTINGS = (
    "<AllowMulticast>false</AllowMulticast>",
    'name="lo"',
    '<Peer address="127.0.0.1"/>',
    "<ParticipantIndex>auto</ParticipantIndex>",
    "<MaxAutoParticipantIndex>32</MaxAutoParticipantIndex>",
)


def _normalized(text: str) -> str:
    """Whitespace-insensitive, so the file's indentation and the inline
    string's line breaks do not count as a disagreement."""
    return re.sub(r"\s+", " ", text)


def test_the_shared_xml_file_and_this_modules_inline_copy_agree():
    shared = _normalized(_SHARED_XML.read_text(encoding="utf-8"))
    inline = _normalized(pl._domain_xml(42))
    for setting in _DISCOVERY_SETTINGS:
        assert setting in shared, (
            f"{_SHARED_XML} no longer carries {setting!r}. The nav container's "
            "rclpy nodes are configured by that file alone."
        )
        assert setting in inline, (
            f"perception_link._DOMAIN42_XML no longer carries {setting!r}; the "
            "nav container's file does. One side will discover a subset of the "
            "other, with no error anywhere."
        )
    assert '<Domain id="42">' in shared


def test_the_vision_detectors_inline_copy_agrees_too():
    # Read as source rather than imported: c3po_vision is not on this app's
    # path and is not installable here (its interpreter is the container's
    # python 3.8). The string is what ships, so the string is what is checked.
    detector = _normalized(_DETECTOR_PY.read_text(encoding="utf-8"))
    for setting in _DISCOVERY_SETTINGS:
        assert setting in detector, (
            f"{_DETECTOR_PY.name} no longer carries {setting!r}. It writes "
            "rt/c3po/objects into the same domain 42 the nav container reads."
        )


def test_diagnostics_carries_the_keys_the_gate_route_reads():
    """/telemetry/gate reads diagnostics() BY KEY, so renames must fail loudly.

    The route builds its body from diagnostics()["gate"] and reports the link's
    liveness from "started" / "domain_id" / "reports_received". Every one of
    those is looked up with .get(), which is right for a telemetry endpoint —
    it must not 500 while someone is trying to read why the robot is not
    moving — but it also means a rename downstream would turn the route into a
    cheerful `{}` instead of an error.

    That failure is worse than a crash here. The whole point of the endpoint is
    to prove the gate refused traffic; an empty body would read as "no drops"
    to a human skimming it, which is the exact opposite of what a broken gate
    would actually be doing.
    """
    link = pl.PerceptionLink()
    diag = link.diagnostics()

    assert "gate" in diag, (
        "diagnostics() no longer embeds the gate — /telemetry/gate in "
        "mcp_server.py builds its whole body from this key")
    for key in ("started", "domain_id", "reports_received"):
        assert key in diag, f"/telemetry/gate reports link.{key}"

    for key in ("enabled", "cmd_vel_received", "dropped_while_disabled", "last_sent"):
        assert key in diag["gate"], (
            f"gate.{key} is one of the four fields Stage 4 reads together to "
            "show Nav2 publishing into a shut gate")


def test_a_fresh_gate_reports_refusal_not_silence():
    """Closed-by-default must be VISIBLE, not merely true.

    last_sent is None and the gate is disabled before anything arms it — and
    those two have to be readable, because "nothing was sent" and "nothing was
    ever received" are different facts that look identical from outside.
    """
    link = pl.PerceptionLink()
    gate = link.diagnostics()["gate"]

    assert gate["enabled"] is False, "the cmd_vel gate must start closed"
    assert gate["last_sent"] is None, "nothing may have been sent before arming"
    assert gate["dropped_while_disabled"] == 0
    assert gate["disabled_reason"], (
        "a closed gate must say WHY it is closed — an operator reading this "
        "endpoint is asking why the robot will not move")


def test_main_starts_the_perception_link_on_real_hardware():
    """The link must be STARTED, not merely constructed.

    It was fully implemented, fully tested, and never started, so domain 42 had
    no participant on the bridge side. Every symptom pointed elsewhere: the
    costmap route 503'd exactly as it would with Nav2 down, `ros2 topic info`
    showed Subscription count: 0 on /c3po/cmd_vel so the gate had nothing to
    refuse, and describe_surroundings reported perception offline, which reads
    as "not deployed yet" rather than as a missing wire.

    Read as source rather than by importing: mcp_server pulls in the whole tool
    catalogue and its DDS-adjacent imports, which is precisely what the rest of
    this suite avoids.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "bridge" / "mcp_server.py"
    body = src.read_text()
    main_body = body.split("def main()", 1)[-1]

    assert "get_link().start()" in main_body, (
        "main() must start the perception link — constructing it via get_link() "
        "creates the singleton but joins no domain")


def test_starting_the_link_is_not_arming_it():
    """start() must never open the gate, whatever else it does.

    This is the property that makes starting at boot safe: the participant and
    the readers come up, and _default_forward — where a Twist becomes
    SET_VELOCITY — stays unreachable because _tick() returns before touching it
    while the gate is shut. A boot that armed navigation would be a robot that
    walks when the Jetson powers on.
    """
    link = pl.PerceptionLink()
    assert link.is_enabled() is False

    gate = link.diagnostics()["gate"]
    assert gate["enabled"] is False
    assert gate["arm_expires_in_s"] is None, "nothing may hold an arming window at rest"


def test_describe_surroundings_actually_consumes_the_perception_report():
    """THE THIRD TIME THIS SHAPE OF BUG HAS APPEARED, hence a test for it.

    PerceptionLink was written and never started. `from_report` was written and
    never called. describe_surroundings hardcoded detector_online=False behind a
    comment saying perception was not deployed, long after it was — so the robot
    had eyes, a wire, and a parser, and the tool that an operator and an agent
    both use to ask "what is around you" reported an offline scene.

    None of that fails a unit test, because every piece works in isolation. What
    catches it is asserting the CONNECTION exists.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "bridge" / "mcp_server.py"
    body = src.read_text()
    # The snapshot logic lives in surroundings_snapshot(), shared by the tool
    # and the read-only /telemetry/surroundings route so the console and the
    # agent cannot be shown different scenes.
    tool = body.split("def surroundings_snapshot", 1)[-1].split("@mcp.tool", 1)[0]

    assert "latest_report()" in tool, (
        "describe_surroundings must read the perception link — without it the "
        "snapshot reports offline while reports are arriving")
    assert "from_report" in tool, (
        "the container's wire shape has exactly one interpreter (world_model."
        "from_report); re-parsing it here would fork the D7 contract")
    assert "detector_online=False" in tool, (
        "the no-report fallback must still degrade explicitly — 'nothing "
        "detected' and 'nothing looked' are different answers (D7)")
