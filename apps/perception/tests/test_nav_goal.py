"""The /c3po/nav_goal and /c3po/nav_status shapes, both sides, on a laptop.

`c3po_perception.nav_goal` is pure (the goal relay imports it; it imports no
ROS). The other half — the bridge's messages through this parser and back —
is apps/bridge/tests/test_nav_link.py, because `bridge.sdk.nav_link` needs
structlog and this harness stays dependency-free (pyproject.toml).
"""

from __future__ import annotations

import math

import pytest
from c3po_perception.nav_goal import (
    TERMINAL_STATES,
    parse_goal,
    quaternion_to_yaw,
    state_from_goal_status,
    yaw_to_quaternion,
)


def test_a_goal_parses_with_defaults():
    g = parse_goal({"v": 1, "id": "nav_1", "x": 1, "y": -2})
    assert g == {"cancel": False, "id": "nav_1", "x": 1.0, "y": -2.0, "yaw": 0.0, "frame": ""}


def test_cancel_with_and_without_an_id():
    assert parse_goal({"v": 1, "cancel": True}) == {"cancel": True, "id": None}
    assert parse_goal({"v": 1, "cancel": True, "id": "nav_1"})["id"] == "nav_1"


@pytest.mark.parametrize("bad", [
    [],
    {"v": 2, "id": "a", "x": 0, "y": 0},
    {"v": 1, "x": 0, "y": 0},                          # no id: status could not be attributed
    {"v": 1, "id": "a", "x": "1", "y": None},
    {"v": 1, "id": "a", "x": float("nan"), "y": 0},
    {"v": 1, "id": "a", "x": True, "y": 0},
    {"v": 1, "id": "a", "x": 500, "y": 0},             # units bug, not a goal
    {"v": 1, "id": "a", "x": 0, "y": 0, "frame": "base_link"},
    {"v": 1, "id": "x" * 65, "x": 0, "y": 0},
])
def test_bad_goals_are_refused(bad):
    with pytest.raises((ValueError, TypeError)):
        parse_goal(bad)


@pytest.mark.parametrize("yaw", [0.0, 0.5, -1.2, math.pi - 1e-6, -math.pi + 1e-6])
def test_yaw_round_trips_through_the_quaternion(yaw):
    assert quaternion_to_yaw(*yaw_to_quaternion(yaw)) == pytest.approx(yaw, abs=1e-9)


def test_positive_yaw_is_counter_clockwise():
    """REP-103, and D7's sign convention: +left. A flipped sign here walks the
    robot to the mirror image of every landmark."""
    _, _, z, w = yaw_to_quaternion(math.pi / 2)
    assert z > 0 and w > 0


def test_goal_status_codes_map_to_the_words_the_bridge_expects():
    assert [state_from_goal_status(c) for c in range(7)] == [
        "pending", "accepted", "active", "active", "succeeded", "canceled", "aborted"]
    assert state_from_goal_status(99) == "pending"
    assert {"succeeded", "canceled", "aborted", "rejected"} == TERMINAL_STATES
