"""Bridge process configuration — read once, at import, before anything else.

IMPORT THIS FIRST. Importing it loads the bridge's own `.env`, and a great deal
of this package reads `os.environ` at import time (`watchdog.py`, every
`skills/*` module's `SIM_MODE`, `listen.py`'s STT settings). A module that is
imported before this one sees the shell's environment only, silently. That is
why `bridge.server` — which every tool and route module imports — imports this
module before anything else.

The bridge loads its own `.env` so that starting it does not require a shell
wrapper to `set -a; . ./.env` first. That wrapper was the main reason
`run_c3po` had to exist, and with it gone a unit file can name the interpreter
directly. Anything already in the environment wins — see `bridge/env_file.py`,
which also explains why systemd's own `EnvironmentFile=` cannot be used on
this file.

The names below are import-time constants: `SIM_MODE` decides at import
whether DDS is initialised at all (`bridge.server`). Tests that need another
mode monkeypatch the name in the module that reads it.
"""

from __future__ import annotations

import os

from bridge.env_file import load_env_file

load_env_file(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

SIM_MODE = os.environ.get("SIM_MODE", "stub")
ROBOT_HOST = os.environ.get("ROBOT_HOST", "127.0.0.1")
DDS_DOMAIN_ID = int(os.environ.get("DDS_DOMAIN_ID", "0"))
# Pin CycloneDDS to one NIC. Empty = autodetermine, correct on a dev machine.
# Onboard the G1's Jetson this must be `eth0` — see `init_dds`.
DDS_INTERFACE = os.environ.get("DDS_INTERFACE", "").strip() or None

# Back↔bridge link: how this server is reached. Default is stdio (what Claude
# Code's MCP client expects). Set BRIDGE_TRANSPORT=http to expose FastMCP's
# streamable-http transport so `apps/back` can connect as an MCP client
# (see apps/back/src/bridge/client.ts). stdio stays untouched either way.
BRIDGE_TRANSPORT = os.environ.get("BRIDGE_TRANSPORT", "stdio")
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8001"))
BRIDGE_CORS_ORIGINS = tuple(
    origin.strip()
    for origin in os.environ.get("BRIDGE_CORS_ORIGINS", "").split(",")
    if origin.strip()
)
