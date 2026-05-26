"""Decode robot fault codes into human-readable labels.

Faults arrive as a stream of `(timestamp, source, code)` triples, either
inside `lowstate.faults` (DDS) or as `errors` / `add_error` / `rm_error`
messages over WebRTC (per `legion1581/unitree_ui/docs/error-handling.md`).
Each `code` is a single bit value (1, 2, 4, 8, 16, ...) — multiple
simultaneous faults arrive as multiple entries, not a packed bitmask.

`source` is a small integer keyed into a label table; per-motor sources
(301-399 and 3000-3999) are synthesised at lookup time as "Motor N" and
share source 300's code catalogue (same overcurrent / encoder / overheat
semantics, attributed to a specific joint).

Catalogue ported from legion1581/unitree_ui/src/protocol/errors-catalog.ts
(MIT). Covers both Go2 and G1 firmware. G1-only entries are tagged in the
comments. Unknown sources fall back to `Source N`; unknown codes to the
hex repr.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# ---------------------------------------------------------------------------
# Source labels
# ---------------------------------------------------------------------------

_SOURCE_LABELS: Final[dict[int, str]] = {
    100: "Communication firmware error",
    200: "Communication firmware error",  # Also: cooling fans on some firmware
    300: "Motor malfunction",
    400: "Radar malfunction",
    500: "UWB malfunction",
    600: "Motion Control",
    700: "BMS error",
    # G1-only
    800: "Chassis error",
    900: "Power distribution switch anomaly",
    1000: "Emergency Stop",
}


# ---------------------------------------------------------------------------
# Code labels — keyed `"<source>_<hex(code)>"` (lowercase, unpadded).
# ---------------------------------------------------------------------------
#
# Wheel-specific overrides for 300_40 / 300_80 only appear on the wheeled
# Go2 variant; they don't conflict with other entries so we include them.

_CODE_LABELS: Final[dict[str, str]] = {
    # 100 — Communication firmware
    "100_1":  "DDS message timeout",
    "100_2":  "Distribution switch abnormal",
    "100_10": "Battery communication error",
    "100_20": "Abnormal mote control communication",
    "100_40": "MCU communication error",
    "100_80": "Motor communication error",
    # 200 — Cooling fans
    "200_1":  "Rear left fan jammed",
    "200_2":  "Rear right fan jammed",
    "200_4":  "Front fan jammed",
    # 300 — Motor
    "300_1":   "Overcurrent",
    "300_2":   "Overvoltage",
    "300_4":   "Driver overheating",
    "300_8":   "Generatrix undervoltage",
    "300_10":  "Winding overheating",
    "300_20":  "Encoder abnormal",
    "300_40":  "Calibration data abnormality",  # wheeled variant
    "300_80":  "Abnormal reset",                # wheeled variant
    "300_100": "Motor communication interruption",
    # G1-only motor warnings (graceful-degradation thresholds)
    "300_1000":     "Command anomaly",
    "300_10000":    "Status anomaly",
    "300_1000000":  "Motor humidity anomaly",
    "300_2000000":  "Encoder remote",
    "300_4000000":  "MOS almost overheat",
    "300_8000000":  "Encoder close",
    "300_10000000": "Winding almost overheat",
    # 400 — Radar / LiDAR
    "400_1":  "Motor rotate speed abnormal",
    "400_2":  "PointCloud data abnormal",
    "400_4":  "Serial port data abnormal",
    "400_10": "Abnormal dirt index",
    # 500 — UWB
    "500_1": "UWB serial port open abnormal",
    "500_2": "Robot information retrieval abnormal",
    # 600 — Motion control / thermal
    "600_4": "Overheating software protection",
    "600_8": "Low battery software protection",
    # 700 — BMS: no per-bit strings shipped; falls back to hex
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaultRecord:
    """Raw fault triple as it arrives on the wire."""

    timestamp: float  # unix seconds (may be 0.0 if unknown)
    source: int
    code: int  # single-bit value


@dataclass(frozen=True)
class DecodedFault:
    """Fault enriched with human-readable labels."""

    timestamp: float
    source: int
    code: int
    source_label: str
    code_label: str
    key: str  # stable "<source>:<code>" for dedup


def code_hex(code: int) -> str:
    """Lowercase, unpadded hex of a code (matches the catalogue key style)."""
    return f"{code:x}"


def lookup_source(source: int) -> str:
    """Return a human label for a fault source, synthesising motor sources."""
    if (label := _SOURCE_LABELS.get(source)) is not None:
        return label
    if 301 <= source <= 399:
        return f"Motor {source - 300}"
    if 3000 <= source <= 3999:
        return f"Motor {source % 100}"
    return f"Source {source}"


def lookup_code(source: int, code: int) -> str:
    """Return a human label for a (source, code) pair.

    Per-motor sources (301-399 and 3000-3999) share source 300's bit catalogue.
    """
    if (direct := _CODE_LABELS.get(f"{source}_{code_hex(code)}")) is not None:
        return direct
    if (301 <= source <= 399) or (3000 <= source <= 3999):
        if (motor := _CODE_LABELS.get(f"300_{code_hex(code)}")) is not None:
            return motor
    return f"Code 0x{code_hex(code)}"


def decode(record: FaultRecord) -> DecodedFault:
    """Enrich a raw fault triple with labels + a stable dedup key."""
    return DecodedFault(
        timestamp=record.timestamp,
        source=record.source,
        code=record.code,
        source_label=lookup_source(record.source),
        code_label=lookup_code(record.source, record.code),
        key=f"{record.source}:{record.code}",
    )


def fault_key(source: int, code: int) -> str:
    """Stable identifier for dedup across snapshot + delta messages."""
    return f"{source}:{code}"
