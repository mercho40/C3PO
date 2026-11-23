"""The bridge's own settings, read once at import. Import this before any other bridge module.

The `.env` is loaded HERE, first: other bridge modules (watchdog, safety, the
skills) read the environment at their own import time, and SIM_MODE decides at
import whether DDS is initialised at all.

Read these as attributes (`config.SIM_MODE`), never `from bridge.config import
SIM_MODE`: a from-import copies the value into the importing module, so a test
that sets `config.SIM_MODE` would reach some modules and not others.
"""

from __future__ import annotations

import os

from bridge.env_file import load_env_file

# CONFIG BEFORE ANYTHING READS IT. This has to run above the `os.environ` reads
# below, which is why it is here at import rather than in `main()` — SIM_MODE
# decides at import time whether DDS is initialised at all.
#
# The bridge loads its own `.env` so that starting it does not require a shell
# wrapper to `set -a; . ./.env` first. That wrapper was the main reason
# `run_c3po` had to exist, and with it gone a unit file can name the interpreter
# directly. Anything already in the environment wins — see `bridge/env_file.py`,
# which also explains why systemd's own `EnvironmentFile=` cannot be used on
# this file.
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
