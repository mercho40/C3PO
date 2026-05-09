# C3PO Bridge

Python sidecar that wraps the Unitree G1 SDK, runs the skill executor, hosts the voice loop, and exposes everything to LLMs over MCP.

See `docs/SPEC.md` at the repo root for the full design.

## Quick start

```bash
cd apps/bridge
uv sync                                  # installs Python 3.12 + deps into .venv

# Run the standalone MCP server (Phase 0a — Claude Code drives directly)
uv run python -m bridge.mcp_server

# Run the full sidecar (Phase 1+ — exposes WS for apps/back)
uv run python -m bridge.main
```

## Phase status

- [x] Phase 0a — stub MCP server (`get_state`, `walk_to`, `say`)
- [ ] Phase 0b — DDS peer config + Isaac Sim handshake (real `get_state`)
- [ ] Phase 1+ — see `docs/SPEC.md` §12

## Environment

Copy `.env.example` to `.env` and fill in. For Phase 0a, defaults are fine — `SIM_MODE=stub` means tools log and return fake data.
