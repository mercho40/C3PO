# C3PO

An embodiment layer for LLMs — gives Claude (or any MCP-capable model) a Unitree G1 humanoid body.

The same skill code path drives a simulated G1 (Isaac Sim on a separate Ubuntu host) and the real robot. The bridge process runs in a different place for each — on the dev machine for sim, onboard the robot's Jetson for real hardware — because the G1's control board publishes DDS only on its internal wired LAN. How and why: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Layout

Bun + Turborepo monorepo.

```
apps/
  back/         Elysia API + Better Auth + Drizzle/PostgreSQL — control plane (port 3000)
  web/          SvelteKit 5 operator console (port 3001)
  bridge/       Python 3.12 sidecar (uv) — MCP server, Unitree SDK, DDS
  perception/   ROS 2 perception + navigation containers for the robot's Jetson
docs/           Architecture, decisions, operations, robot reference
scripts/robot/  Onboard implementation; one operator CLI: `c3po`
packages/       Reserved for shared TS packages
```

## Documentation

| Doc                                                | What it answers                                                       |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)     | How the system fits together — layers, topology, safety model         |
| [`docs/DECISIONS.md`](docs/DECISIONS.md)           | Why it is built this way — decision records with rationale            |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md)         | Deploying and operating — topology, stack controls, addressing        |
| [`docs/ROBOT-API.md`](docs/ROBOT-API.md)           | The G1's reverse-engineered control API — services, api_ids, FSM      |
| [`docs/ROBOT-HARDWARE.md`](docs/ROBOT-HARDWARE.md) | What the physical robot presents — network, peripherals, cohabitation |
| [`docs/MONDAY-RUNBOOK.md`](docs/MONDAY-RUNBOOK.md) | Ordered first hardware window — gates, pass criteria, rollbacks       |
| `apps/*/README.md`                                 | Developing each app                                                   |

## Quickstart

```bash
bun install

# Env — each app's .env.example documents its own variables
cp apps/back/.env.example apps/back/.env
cp apps/web/.env.example apps/web/.env

# Database (needs local PostgreSQL — see apps/back/README.md)
cd apps/back && bun run db:migrate && cd ../..

# Dev servers: web on :3001, back on :3000
bun run dev
```

The Python bridge is set up separately — CycloneDDS build, `uv sync`, and the SDK patch are covered in [`apps/bridge/README.md`](apps/bridge/README.md). Claude Code sessions get it automatically via `.mcp.json`.

## Commands

| Command               | Description                  |
| --------------------- | ---------------------------- |
| `bun run dev`         | Start TS apps in development |
| `bun run build`       | Build TS apps                |
| `bun run check-types` | Type-check across monorepo   |
| `bun run format`      | Format with Prettier         |

Per-app commands (database, bridge scripts, perception stages) live in each app's README.
