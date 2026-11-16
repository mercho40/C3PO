"""How old each detection is when the world summary goes out. Pure: no ROS.

The vision container stamps its payload at the frame grab. The summary is
re-emitted at 4 Hz from the last payload, so without this the bridge sees every
object as new until DETECTOR_OFFLINE_AFTER_S, up to 1.5 s after the frame.
Both containers run --network host on one kernel, so the two clocks agree.
"""

from __future__ import annotations

from typing import Any

# Past this, the stamp is not a clock skew but a broken stamp. Rejecting it
# matters most for a future stamp, which would otherwise clamp to age 0: fresh.
MAX_STAMP_SKEW_S = 5.0


def capture_stamp(raw: Any, now: float) -> float:
    """The detector's grab stamp, or ValueError. A missing stamp is `now`."""
    stamp = now if raw is None else float(raw)
    # `not <` so a NaN fails too.
    if not abs(now - stamp) < MAX_STAMP_SKEW_S:
        raise ValueError(f"stamp_unix {raw!r} is {now - stamp:+.1f} s from this clock")
    return stamp


def aged(objects: list[dict], stamp_unix: float, now: float) -> list[dict]:
    """Copies of `objects`, each with `age_s` since the capture."""
    age = round(max(0.0, now - stamp_unix), 1)
    return [dict(o, age_s=age) for o in objects]
