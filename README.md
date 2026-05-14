# C3PO

An embodiment layer for LLMs — give Claude (or any MCP-capable model) a body in the form of a Unitree G1 humanoid.

The system is sim-first against Isaac Sim on a separate Ubuntu host; the same code path drives a real G1 on the LAN once hardware arrives. Architecture, components, libraries, and wire formats live in [`docs/SPEC.md`](docs/SPEC.md).

## Stack

- **Runtime:** Bun + Turborepo
- **Backend (`apps/back`):** Elysia, Better Auth (admin + organization plugins), Drizzle ORM, PostgreSQL
- **Frontend (`apps/web`):** SvelteKit 5 (runes), Tailwind CSS 4, bits-ui / shadcn-svelte, Eden Treaty
- **Bridge (`apps/bridge`):** Python 3.12 (uv), FastMCP, `unitree_sdk2_python`, CycloneDDS, py_trees
- **Simulator:** Isaac Sim + `unitree_sim_isaaclab` on a separate Ubuntu host (LAN)
- **LLM driver:** today, Claude Code (via MCP stdio); long-term, an internal Anthropic Messages API agent inside `apps/back`

## Setup

### 1. JS / TS workspaces

```bash
bun install
```

Copy env files (`BETTER_AUTH_SECRET` must match between back and web):

```bash
cp apps/back/.env.example apps/back/.env
cp apps/web/.env.example apps/web/.env
openssl rand -base64 32   # use for BETTER_AUTH_SECRET
```

Run database migrations:

```bash
cd apps/back && bunx drizzle-kit migrate
```

### 2. Python bridge

```bash
cd apps/bridge
uv sync                                # installs Python 3.12 + deps into .venv
./scripts/postsync.sh                  # patches unitree_sdk2py's broken __init__.py
cp .env.example .env                   # set SIM_MODE, ROBOT_HOST, DDS_DOMAIN_ID
```

You will also need the CycloneDDS C library locally — see [`apps/bridge/README.md`](apps/bridge/README.md) for the source-build steps (no Homebrew formula on macOS).

### 3. Isaac Sim host

The Mac talks DDS to a separate Ubuntu machine running Isaac Sim + [`unitree_sim_isaaclab`](https://github.com/unitreerobotics/unitree_sim_isaaclab). Default DDS domain is `1`.

## Development

```bash
bun run dev
```

Boots both TS apps via Turbo:

- **Frontend:** http://localhost:3001
- **Backend:** http://localhost:3000

The bridge runs separately — as an MCP server registered in `.mcp.json` (auto-launched by Claude Code) or manually via `uv run python -m bridge.mcp_server` from `apps/bridge`.

## Scripts

| Command               | Description                       |
| --------------------- | --------------------------------- |
| `bun run dev`         | Start TS apps in development      |
| `bun run build`       | Build TS apps                     |
| `bun run check-types` | Type-check across monorepo        |
| `bun run format`      | Format with Prettier              |
| `bun run start`       | Start production (requires build) |

### Backend (apps/back)

| Command                     | Description         |
| --------------------------- | ------------------- |
| `bunx drizzle-kit generate` | Generate migrations |
| `bunx drizzle-kit migrate`  | Apply migrations    |
| `bunx drizzle-kit studio`   | Open Drizzle Studio |

### Bridge (apps/bridge)

| Command                                  | Description                              |
| ---------------------------------------- | ---------------------------------------- |
| `uv sync`                                | Install / update Python deps             |
| `./scripts/postsync.sh`                  | Re-apply unitree_sdk2py patch after sync |
| `uv run python -m bridge.mcp_server`     | Run the stdio MCP server                 |
| `uv run python scripts/rotate.py 1.5708` | Rotate robot 90° CCW (utility)           |
| `uv run python scripts/dds_scan.py`      | Enumerate DDS participants/topics        |

## Project structure

```
apps/
  back/                 Elysia API + Better Auth + Drizzle (port 3000)
  web/                  SvelteKit supervisor UI (port 3001)
  bridge/               Python sidecar — MCP, Unitree SDK, DDS, voice (planned)
    src/bridge/
      mcp_server.py       FastMCP stdio server
      sdk/                DDS connection + LowState/sim_state subscribers
      skills/             Skill implementations (walk_to so far)
    scripts/              Diagnostics + utilities
docs/
  SPEC.md               Full architecture spec
packages/               Reserved for shared TS packages (Phase 1+)
```

## Status

Phase 0b complete: Claude Code → MCP → bridge → DDS → Isaac Sim works end-to-end. Robot walks 1–2 m per command, rotates 90/180° on demand, returns live pose/state at ~100 Hz. Phase 1 (rest of the skill catalogue + supervisor UI + voice + internal agent) is the next arc — see `docs/SPEC.md` §12.
