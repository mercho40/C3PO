"""scripts/robot/c3po_preflight reads /safety/status by substring, so pin the substrings.

The shell matches `"person_rule":false` and friends against the route's body.
A body rendered with a space after the colon would make each warning silently
vanish, so this renders the real status the way the route does and checks that
every needle the script matches is in it.
"""

from __future__ import annotations

from pathlib import Path

from starlette.responses import JSONResponse

from bridge import estop, safety

PREFLIGHT = Path(__file__).resolve().parents[3] / "scripts" / "robot" / "c3po_preflight"


def test_every_safety_needle_the_preflight_matches_is_what_the_route_sends(monkeypatch):
    monkeypatch.setattr(safety, "PERSON_RULE", False)
    monkeypatch.setattr(safety, "GEOFENCE_M", 0.0)
    estop.signal_stop()
    body = JSONResponse(safety.status()).body.decode()
    script = PREFLIGHT.read_text()
    for needle in (
        '"confirm_configured":',
        '"person_rule":false',
        '"radius_m":null',
        '"stop_standing":true',
    ):
        assert "*'" + needle in script, needle
        assert needle in body, needle
