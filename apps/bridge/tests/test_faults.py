"""Tests for sdk/faults.py — fully implemented, pure logic, but not yet
wired to any caller (blocked on the WebRTC transport, per state.py's own
docstring). Worth testing now, before it's finally connected under time
pressure and any bugs here get blamed on the wiring instead.
"""

from __future__ import annotations

from bridge.sdk.faults import (
    FaultRecord,
    code_hex,
    decode,
    fault_key,
    lookup_code,
    lookup_source,
)


def test_code_hex_matches_catalogue_key_style():
    assert code_hex(1) == "1"
    assert code_hex(16) == "10"
    assert code_hex(0x1000000) == "1000000"


def test_lookup_source_known_source():
    assert lookup_source(300) == "Motor malfunction"
    assert lookup_source(1000) == "Emergency Stop"


def test_lookup_source_unknown_source_falls_back_to_generic_label():
    assert lookup_source(12345) == "Source 12345"


def test_lookup_source_synthesises_motor_labels_in_both_ranges():
    assert lookup_source(305) == "Motor 5"  # 301-399 range
    assert lookup_source(3007) == "Motor 7"  # 3000-3999 range, mod 100


def test_lookup_code_known_pair():
    assert lookup_code(300, 1) == "Overcurrent"
    assert lookup_code(100, 0x80) == "Motor communication error"


def test_lookup_code_per_motor_source_shares_300s_catalogue():
    # A per-motor source (e.g. 305 = "Motor 5") has no entries of its own in
    # _CODE_LABELS -- it should fall back to the shared 300_* catalogue.
    assert lookup_code(305, 1) == "Overcurrent"
    assert lookup_code(3007, 0x20) == "Encoder abnormal"


def test_lookup_code_unknown_code_falls_back_to_hex():
    assert lookup_code(300, 0xDEAD) == "Code 0xdead"


def test_lookup_code_bms_source_has_no_catalogue_falls_back_to_hex():
    # 700 (BMS) is documented as shipping no per-bit strings.
    assert lookup_code(700, 1) == "Code 0x1"


def test_decode_enriches_and_builds_stable_key():
    record = FaultRecord(timestamp=123.0, source=300, code=1)

    decoded = decode(record)

    assert decoded.source_label == "Motor malfunction"
    assert decoded.code_label == "Overcurrent"
    assert decoded.key == "300:1"
    assert decoded.timestamp == 123.0


def test_fault_key_matches_decode_key_format():
    record = FaultRecord(timestamp=0.0, source=400, code=2)
    assert fault_key(record.source, record.code) == decode(record).key
