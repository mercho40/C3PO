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

# Existing local dev servers: back on :3000, web on :3001
bun run dev

# Real robot instead (direct LAN connection to g1-orin.local)
bun run dev:robot
```

The Python bridge needs its one-time CycloneDDS/`uv` setup before that command;
[`apps/bridge/README.md`](apps/bridge/README.md) covers it. Claude Code sessions
also get the bridge automatically via `.mcp.json`.

## Commands

| Command               | Description                                           |
| --------------------- | ----------------------------------------------------- |
| `bun run dev`         | Start the existing local development servers          |
| `bun run dev:robot`   | Start API and web against the robot over the LAN      |
| `bun run start`       | Start production builds against the configured bridge |
| `bun run build`       | Build the monorepo                                    |
| `bun run check-types` | Type-check across the monorepo                        |
| `bun run test`        | Test across the monorepo                              |
| `bun run format`      | Format with Prettier                                  |

On the robot, the corresponding integrated surface is `c3po up [profile]` and
`c3po down`; see [`docs/OPERATIONS.md`](docs/OPERATIONS.md). Per-app commands
remain for development and diagnostics.
