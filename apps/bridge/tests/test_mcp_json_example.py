"""The committed `.mcp.json.example` is the two entries apps/bridge/README.md describes.

`.mcp.json` itself is gitignored (each developer's hosts and paths differ), so
the example is the only copy in the repo. The safety property it carries is the
split: `c3po-sim` is a stdio child on this machine that can only reach a
simulator, and `c3po-bridge` is the real robot's daemon through the tunnel.
"""

from __future__ import annotations

import json
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[3] / ".mcp.json.example"


def _servers() -> dict:
    return json.loads(EXAMPLE.read_text())["mcpServers"]


def test_exactly_the_two_documented_entries():
    assert sorted(_servers()) == ["c3po-bridge", "c3po-sim"]


def test_the_sim_entry_is_a_stdio_child_that_cannot_be_the_robot():
    sim = _servers()["c3po-sim"]
    assert sim["command"] == "uv"
    assert sim["args"][-2:] == ["-m", "bridge.mcp_server"]
    assert sim["env"]["BRIDGE_TRANSPORT"] == "stdio"
    assert sim["env"]["SIM_MODE"] == "isaac"
    assert sim["env"]["DDS_DOMAIN_ID"] != "0"  # the real G1's domain


def test_the_robot_entry_dials_the_tunnel_not_the_lan():
    # The bridge binds the robot's loopback; g1-orin.local:8001 is refused.
    assert _servers()["c3po-bridge"] == {"type": "http", "url": "http://127.0.0.1:8001/mcp"}
