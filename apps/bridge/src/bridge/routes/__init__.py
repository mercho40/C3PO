"""Plain-HTTP routes on the MCP server's own port, beside `/mcp`. None of them is an MCP tool.

Each module registers its routes on `bridge.server.mcp` when imported; importing
this package registers all of them. What each is for, and why it is not a tool,
is in its module.

- `telemetry` — read-only robot data for the console: costmap, gate, lidar ring
- `safety`    — the governor's status, the operator's confirmation and «Reanudar»
- `face`      — the face gate's people and settings, and enrollment
- `camera`    — the head camera, relayed or served from the videohub buffer
"""

from bridge.routes import camera, face, safety, telemetry  # noqa: F401 - they register on import
