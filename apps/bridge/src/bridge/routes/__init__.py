"""The bridge's plain-HTTP routes — everything on :8001 that is not MCP.

Importing this package registers every route on `bridge.server.mcp` via
`@mcp.custom_route`. They are served only by the streamable-HTTP transport;
under stdio they are registered and unreachable.

Telemetry and camera routes are READ-ONLY by construction: none can arm the
gate, publish, or reach anything that actuates. The voice routes are the one
exception — `audio/output` plays PCM on the robot's speaker, and an
`audio/input` subscription arms the spoken-stop reflex.
"""

from bridge.routes import camera, telemetry, voice  # noqa: F401 - imported for registration
