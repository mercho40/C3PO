"""The bridge's MCP tools — the skill catalogue — one module per domain.

Importing this package registers every tool on `bridge.server.mcp`: each
module's `@mcp.tool` decorators run at import. `bridge.mcp_server` imports it
once at startup, and `tests/test_mcp_catalogue.py` pins the resulting surface.

A tool module validates, logs, stubs, and delegates. The behaviour lives in
`bridge.skills.*`; the target-specific seams live in `bridge.sdk.*`
(docs/ARCHITECTURE.md §5).
"""

from bridge.tools import (  # noqa: F401 - imported for tool registration
    arms,
    gestures,
    landmarks,
    locomotion,
    navigation,
    posture,
    safety,
    speech,
    state,
    surroundings,
    tasks,
)
