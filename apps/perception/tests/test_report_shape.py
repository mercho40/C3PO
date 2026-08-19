"""The domain-42 handshake: the container's JSON vs `world_model.build()`.

Two modules, two languages of dependency (ROS 2 Humble on python 3.10 in a
container; plain CycloneDDS on python 3.12 on the host), one shared shape, and
a transport that cannot type-check it. The report contract (perception_link.py
/ apps/perception/README.md) chose
`std_msgs/String` carrying JSON precisely so that "absent" and "empty" stay
distinguishable — the cost of that choice is that NOTHING enforces the key
names. Rename `objects_omitted` to `omitted` on one side and the other side
reads `0`: no exception, no dropped sample, no log line. Truncation silently
stops being declared, which is the exact failure `objects_omitted` exists to
prevent.

This file is that enforcement, and the Stage 0 (apps/perception/README.md)
verify step names it: it
asserts the publisher's JSON keys are exactly `world_model.build()`'s keyword
arguments.

------------------------------------------------------------------------------
WHY THE PUBLISHER IS READ AS SOURCE AND NOT IMPORTED
------------------------------------------------------------------------------
`world_model_publisher.py` imports `rclpy`, `nav_msgs`, `sensor_msgs` and
`std_msgs` at module scope, and the report is assembled inside
`WorldModelPublisher._emit`, a method on an `rclpy.node.Node` subclass. Those
packages do not exist on the Mac and are not going to: installing ROS 2 Humble
to run a key-name comparison would make this suite unrunnable on the machine it
was written to run on, and faking `rclpy` in `sys.modules` well enough to
construct a Node would mean asserting against our own mock rather than against
the shipped code.

So the key set is extracted from the file's AST. That is not a weaker check —
it reads the literal dict that is published, from the file that publishes it,
and fails if the dict stops being a literal. It is a check of the wire shape;
the corresponding check of the wire *values* is the from_report() half below,
which runs the real bridge code against a real report.

What the AST half cannot see, and Stage 3 must: that `_emit` fires on every
tick including empty ones. Silence cannot distinguish "the detector looked and
saw nothing" from "the container is gone", so the heartbeat is load-bearing and
is verified live, against a running container, not here.

If the nav lane ever factors the dict out into a module-level pure function
(`build_report(...)`, no ROS imports), this file finds it there too — see
`_report_dict_node`. That refactor would be an improvement and needs no change
here.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest
from bridge import world_model
from bridge.world_model import Observation
from conftest import PERCEPTION_LINK_PY, WORLD_MODEL_PUBLISHER_PY

# --------------------------------------------------------------------------
# The mapping, stated once and in full
# --------------------------------------------------------------------------
#
# The wire is *not* `build()`'s kwargs plus nothing — it is those kwargs, plus
# an envelope, minus what the bridge owns. Every entry below is a decision from
# the report contract (perception_link.py / apps/perception/README.md), and
# each one is the kind of thing that gets "tidied up" by someone who
# does not know why it is there.

# Transport metadata. Not arguments to anything: `report_version` lets the
# bridge reject a container that has changed what a field MEANS
# (perception_link.py's version rejection refuses an unknown version loudly
# rather than half-parsing it), and `stamp_unix` is
# how the bridge ages the whole report without trusting its own clock to agree
# with the container's.
ENVELOPE_KEYS = frozenset({"report_version", "stamp_unix"})

# Renamed on purpose, both times to say "this is the CONTAINER's contribution,
# and the bridge adds its own to it" rather than "this is the final value".
#   objects_omitted -> extra_omitted : summed with what the bridge truncates.
#   notes           -> source_notes  : appended after the bridge's own
#                                      degradation notes, never replacing them.
WIRE_TO_KWARG = {
    "objects_omitted": "extra_omitted",
    "notes": "source_notes",
}

# Kwargs that must NEVER appear on the wire. Both are policy, not observation:
# `max_objects` is a token budget the container knows nothing about, and
# `landmarks` are bridge-side state from skills/landmarks.py, merged after the
# fact. A container that started sending either would be forking the contract
# into two languages, only one of which has tests that run with no robot.
BRIDGE_OWNED_KWARGS = frozenset({"landmarks", "max_objects"})


def _parse(path: Path) -> ast.Module:
    if not path.exists():
        pytest.fail(
            f"{path} does not exist. This test is the contract between the "
            "perception container and the bridge; it is not skipped when one "
            "side is missing, because a missing side is the drift."
        )
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _report_dict_node(tree: ast.Module) -> ast.Dict:
    """The literal dict that gets published, wherever it currently lives.

    Accepts `report = {...}` (the shipped shape, inside `_emit`) or a `return
    {...}` from any function whose name mentions "report" (the shape a future
    pure-function refactor would have). Anything else is a structure this test
    cannot read, and it says so loudly instead of guessing.
    """
    assigned: list[ast.Dict] = []
    returned: list[ast.Dict] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "report":
                    assigned.append(node.value)
        elif isinstance(node, ast.FunctionDef) and "report" in node.name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    returned.append(sub.value)

    candidates = assigned or returned
    assert len(candidates) == 1, (
        f"expected exactly one published report dict in "
        f"{WORLD_MODEL_PUBLISHER_PY.name}, found {len(candidates)}. Either the "
        "report is no longer a dict literal (this test can no longer read it) "
        "or there are now two of them (the wire shape has forked)."
    )
    return candidates[0]


def _literal_str_keys(node: ast.Dict) -> set[str]:
    keys: set[str] = set()
    for key in node.keys:
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
            "every key of the published report must be a plain string literal "
            "— a computed or **-splatted key cannot be checked against "
            "build()'s signature, and an unchecked key is the whole failure "
            "this test exists to catch."
        )
        keys.add(key.value)
    return keys


def _module_constant(tree: ast.Module, name: str, path: Path):
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    pytest.fail(f"{path.name} has no module-level {name}")


def _int_set(node: ast.expr, path: Path, name: str) -> set[int]:
    """literal_eval, plus the `frozenset({...})`/`set([...])` wrappers it refuses."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("frozenset", "set")
        and len(node.args) == 1
    ):
        node = node.args[0]
    try:
        value = ast.literal_eval(node)
    except ValueError:  # pragma: no cover - only on a shape we cannot read
        pytest.fail(f"{path.name}: {name} is not a literal this test can read")
    return {int(v) for v in value}


def build_kwargs() -> set[str]:
    params = inspect.signature(world_model.build).parameters
    # build() is keyword-only by design (`def build(*, ...)`) so that a caller
    # can never silently reorder the contract.
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()), (
        "world_model.build() must stay keyword-only: positional arguments make "
        "a field reorder a silent value swap across this boundary."
    )
    return set(params)


def published_keys() -> set[str]:
    return _literal_str_keys(_report_dict_node(_parse(WORLD_MODEL_PUBLISHER_PY)))


# --------------------------------------------------------------------------
# The shape: the publisher's JSON keys ARE build()'s kwargs
# --------------------------------------------------------------------------


def test_published_keys_are_exactly_build_kwargs():
    wire = published_keys()
    payload = wire - ENVELOPE_KEYS
    mapped = {WIRE_TO_KWARG.get(k, k) for k in payload}
    expected = build_kwargs() - BRIDGE_OWNED_KWARGS

    missing = expected - mapped
    extra = mapped - expected
    assert not missing, (
        f"world_model.build() accepts {sorted(missing)} but the container never "
        "sends it. The bridge will silently use the default — which for "
        "detector_online, lidar_online and extra_omitted means reporting a "
        "clear scene or an under-declared truncation."
    )
    assert not extra, (
        f"the container publishes {sorted(extra)}, which build() does not "
        "accept. Either it is a new field the bridge must consume, or it is "
        "envelope metadata that belongs in ENVELOPE_KEYS with a reason."
    )


def test_the_envelope_is_present_and_is_not_mistaken_for_a_builder_argument():
    wire = published_keys()
    assert ENVELOPE_KEYS <= wire, (
        f"missing envelope keys {sorted(ENVELOPE_KEYS - wire)}. Without "
        "report_version the bridge cannot reject an incompatible container; "
        "without stamp_unix it cannot age the report."
    )
    assert not (ENVELOPE_KEYS & build_kwargs())


def test_no_bridge_owned_policy_leaks_onto_the_wire():
    # Stated separately from the equality above because the failure mode is
    # different: this one is a container that has started making decisions
    # (token budget, landmark memory) that are not its to make.
    assert not (published_keys() & BRIDGE_OWNED_KWARGS)


def test_the_container_and_the_bridge_agree_on_the_report_version():
    pub = _parse(WORLD_MODEL_PUBLISHER_PY)
    version = ast.literal_eval(_module_constant(pub, "REPORT_VERSION", WORLD_MODEL_PUBLISHER_PY))

    link = _parse(PERCEPTION_LINK_PY)
    supported = _int_set(
        _module_constant(link, "SUPPORTED_REPORT_VERSIONS", PERCEPTION_LINK_PY),
        PERCEPTION_LINK_PY,
        "SUPPORTED_REPORT_VERSIONS",
    )

    assert version in supported, (
        f"the container publishes report_version={version}; the bridge accepts "
        f"{sorted(supported)}. perception_link.py's version rejection is LOUD on purpose — a "
        "newer container may have changed what a field means, and half-reading "
        "it produces a confident, wrong world model — so this mismatch is a "
        "perception blackout, not a degradation."
    )


# --------------------------------------------------------------------------
# The values: a real report through the real bridge code
# --------------------------------------------------------------------------


def wire_report(**overrides) -> dict:
    """One tick of `/c3po/world_summary`, exactly as the report contract
    (perception_link.py / apps/perception/README.md) defines it.

    Written out by hand rather than derived from the AST: this is the
    independent statement of the shape, so a rename that slips past the AST
    check (because both sides were renamed) still has to be made here
    deliberately.
    """
    report = {
        "report_version": 1,
        "stamp_unix": 1_700_000_000.0,
        "pose": {"x_m": 1.5, "y_m": -0.25, "yaw_deg": 90.0},
        "pose_age_s": 0.08,
        "detector_online": True,
        "objects": [
            {"label": "person", "range_m": 2.4, "bearing_deg": 12.0,
             "confidence": 0.87, "age_s": 0.2},
            {"label": "chair", "range_m": 1.1, "bearing_deg": -35.0,
             "confidence": 0.62, "age_s": 0.2},
        ],
        "objects_omitted": 0,
        "lidar_online": True,
        "free_space": {"ahead_m": 3.2, "left_m": 1.4, "right_m": 2.0},
        "notes": [],
    }
    report.update(overrides)
    return report


def from_wire(**overrides):
    # Through JSON, not around it: the report crosses domain 42 as a
    # std_msgs/String, so tuples become lists and int keys would become
    # strings. Anything that survives here survives the wire.
    return world_model.from_report(json.loads(json.dumps(wire_report(**overrides))))


def test_a_full_report_becomes_a_snapshot_with_every_source_ok():
    wm = from_wire()
    d = wm.to_dict()

    assert d["sources"] == {"pose": "ok", "detector": "ok", "lidar": "ok"}
    assert d["pose"] == {"x_m": 1.5, "y_m": -0.25, "yaw_deg": 90.0}
    assert d["free_space"]["ahead_m"] == pytest.approx(3.2)
    # Nearest first, and the object dicts have become Observations — the wire
    # carries plain dicts, and `Observation(**obj)` is the only reason the key
    # names in wire_report() can be trusted.
    assert all(isinstance(o, Observation) for o in wm.objects)
    assert [o.label for o in wm.objects] == ["chair", "person"]
    assert d["objects"][0]["bearing_deg"] == pytest.approx(-35.0)
    assert "notes" not in d or not d["notes"]


def test_bearing_sign_is_carried_through_unchanged():
    # The same convention test_grounding.py pins at the camera, restated at the
    # far end of the wire: positive is LEFT, all the way from the D435i to the
    # model's prompt. Nothing between them may normalise, wrap or negate it.
    wm = from_wire()
    by_label = {o.label: o for o in wm.objects}
    assert by_label["person"].bearing_deg == pytest.approx(12.0)
    assert by_label["chair"].bearing_deg == pytest.approx(-35.0)


# --------------------------------------------------------------------------
# Absent is not empty — across the boundary this time
# --------------------------------------------------------------------------


def test_an_offline_detector_never_yields_an_empty_object_list():
    """The rule, at the one place a JSON `[]` could quietly become "all clear".

    The report contract (perception_link.py / apps/perception/README.md) emits
    `objects: []` alongside `detector_online: false`, because the
    heartbeat has to keep ticking. Read naively that is "I looked and there is
    nothing there" — the reading that walks a robot into a wall. `from_report`
    must resolve it the other way: no objects key at all, an explicit offline
    status, and a sentence the model will actually read.
    """
    wm = from_wire(detector_online=False, objects=[], objects_omitted=0)
    d = wm.to_dict()

    assert d["sources"]["detector"] == "offline"
    # An absent key cannot be misread as an empty scene; an empty list can.
    assert "objects" not in d
    assert d["notes"], "an offline source must always produce a note"
    assert any("OFFLINE" in n for n in d["notes"])
    assert any("not assume the path is clear" in n.lower() for n in d["notes"])


def test_an_online_detector_that_sees_nothing_is_a_different_and_useful_fact():
    # The other half of the same rule. This must NOT be warned about: "I looked
    # and the way is clear" is exactly the observation the robot needs to move.
    wm = from_wire(objects=[], objects_omitted=0)
    d = wm.to_dict()

    assert d["sources"]["detector"] == "ok"
    assert "objects" not in d  # nothing to list, but nothing to warn about
    assert not any("OFFLINE" in n for n in d.get("notes", []))


def test_an_offline_lidar_is_not_infinite_free_space():
    wm = from_wire(lidar_online=False, free_space=None)
    d = wm.to_dict()

    assert d["sources"]["lidar"] == "offline"
    assert "free_space" not in d
    assert any("LiDAR is OFFLINE" in n for n in d["notes"])


def test_a_missing_report_is_the_every_source_offline_snapshot():
    # Perception down, or its last report older than REPORT_OFFLINE_AFTER_S.
    # `from_report(None)` falls through to build() with nothing, which is the
    # honest "we cannot see" rather than an empty scene.
    wm = world_model.from_report(None)
    d = wm.to_dict()

    assert d["sources"] == {"pose": "offline", "detector": "offline", "lidar": "offline"}
    assert "objects" not in d
    assert "free_space" not in d
    assert len(d["notes"]) >= 3


# --------------------------------------------------------------------------
# Truncation is always declared
# --------------------------------------------------------------------------


def test_container_truncation_is_summed_with_the_bridges_own():
    # The container capped 40 detections at MAX_OBJECTS_ON_WIRE=32 and said so
    # (objects_omitted: 8). The bridge then caps 32 at MAX_OBJECTS=8, dropping
    # 24 more. The model must be told 32, not 24 and not 8 — either alone is a
    # number that looks plausible while under-reporting the scene.
    objects = [
        {"label": f"box{i}", "range_m": 1.0 + i * 0.1, "bearing_deg": 0.0,
         "confidence": 0.5, "age_s": 0.1}
        for i in range(32)
    ]
    wm = from_wire(objects=objects, objects_omitted=8)

    assert len(wm.objects) == world_model.MAX_OBJECTS
    assert wm.objects_omitted == (32 - world_model.MAX_OBJECTS) + 8 == 32
    assert any("32 more object" in n for n in wm.to_dict()["notes"])


def test_truncation_reported_by_an_offline_detector_is_not_counted():
    # An offline detector's counts are as untrustworthy as its list. Reporting
    # "24 more objects" from a source we have just declared offline would give
    # the model a scene it has no evidence for.
    wm = from_wire(detector_online=False, objects=[], objects_omitted=24)
    assert wm.objects_omitted == 0
    assert "objects_omitted" not in wm.to_dict()


# --------------------------------------------------------------------------
# Notes the container alone can produce
# --------------------------------------------------------------------------


def test_container_notes_reach_the_model_after_the_bridges_own():
    # A rejected detector payload, or a scan arriving in the wrong frame:
    # facts about perception's health that no amount of inspecting its output
    # would reveal, so they cannot be reconstructed on this side.
    note = "Scan arrives in 'livox_frame', not 'base_footprint'; free-space bearings are unrotated."
    wm = from_wire(notes=[note])
    assert note in wm.to_dict()["notes"]


def test_container_notes_are_dropped_when_the_detector_is_offline():
    # Report-contract ordering (perception_link.py / apps/perception/README.md):
    # the bridge's own degradation lines come first and, with the
    # detector offline, they are the more important thing for the model to read
    # — a stale complaint about a payload we are no longer receiving is noise
    # in front of "you cannot see".
    note = "Detector payload rejected: unsupported objects schema v=2"
    wm = from_wire(detector_online=False, objects=[], notes=[note])
    notes = wm.to_dict()["notes"]

    assert note not in notes
    assert any("OFFLINE" in n for n in notes)


# --------------------------------------------------------------------------
# Ages, and the snapshot the model actually pays for
# --------------------------------------------------------------------------


def test_a_stale_pose_is_reported_stale_rather_than_dropped():
    wm = from_wire(pose_age_s=world_model.STALE_AFTER_S + 1.0)
    assert wm.to_dict()["sources"]["pose"] == "stale"


def test_a_stale_detection_is_reported_stale():
    objects = [{"label": "person", "range_m": 2.0, "bearing_deg": 0.0,
                "confidence": 0.9, "age_s": world_model.STALE_AFTER_S + 1.0}]
    wm = from_wire(objects=objects)
    d = wm.to_dict()
    assert d["sources"]["detector"] == "stale"
    assert any("stale" in n.lower() for n in d["notes"])


def test_the_snapshot_still_fits_the_token_budget_it_was_designed_for():
    # The whole point of the layer: a few hundred tokens, not a point cloud.
    # A container that starts sending richer objects would erode this silently.
    objects = [
        {"label": f"object{i}", "range_m": 1.0 + i * 0.1, "bearing_deg": -30.0 + i,
         "confidence": 0.5, "age_s": 0.3}
        for i in range(32)
    ]
    wm = from_wire(objects=objects, objects_omitted=8)
    assert wm.approx_tokens() < 400
