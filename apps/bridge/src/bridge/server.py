"""The FastMCP instance every tool and route registers on.

Importing this module has two side effects, both deliberate:

- it loads the bridge configuration (`bridge.config`), and
- outside `SIM_MODE=stub`, it initialises DDS and warms the state sampler, so
  the subscriber is alive and accumulating LowState messages before the first
  tool call lands.

Tool modules (`bridge.tools.*`) and HTTP route modules (`bridge.routes.*`)
import `mcp` from here and register with decorators; `bridge.mcp_server`
imports both packages and runs the server.
"""

from __future__ import annotations

# Config first: it loads `.env`, which the imports below read at import time.
from bridge.config import (
    BRIDGE_HOST,
    BRIDGE_PORT,
    DDS_DOMAIN_ID,
    DDS_INTERFACE,
    ROBOT_HOST,
    SIM_MODE,
)

from mcp.server.fastmcp import FastMCP  # noqa: E402

if SIM_MODE != "stub":
    from bridge.sdk.connection import init_dds
    from bridge.sdk.state import get_sampler

    init_dds(robot_host=ROBOT_HOST, domain_id=DDS_DOMAIN_ID, interface=DDS_INTERFACE)
    # Warm the subscriber singleton so messages start flowing immediately.
    get_sampler()

mcp = FastMCP(
    name="c3po-bridge",
    instructions=(
        "Tools for controlling a Unitree G1 humanoid robot (or its Isaac Sim "
        "emulation). In stub mode, tools log and return fake data. In isaac/real "
        "mode, get_state reads live DDS state and walk_to/turn actually drive "
        "the robot. Long-running tools return a task_id; use cancel_task to "
        "interrupt one task, stop_everything to halt all motion, and "
        "list_active_tasks to see what's in flight."
    ),
    host=BRIDGE_HOST,
    port=BRIDGE_PORT,
)
