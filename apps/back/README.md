# C3PO Backend (`apps/back`)

Elysia control plane: session auth (Better Auth), the skill catalogue, and
an MCP client that proxies requests to the bridge (`apps/bridge`). See
[`../../docs/SPEC.md`](../../docs/SPEC.md) §3 for the full architecture.

## Setup

```bash
cp .env.example .env
# Edit .env — DATABASE_URL, BETTER_AUTH_SECRET (openssl rand -base64 32,
# must match apps/web's), BRIDGE_URL. See .env.example for the full list.
```

Needs a Postgres instance. For local dev:

```bash
brew install postgresql@16
brew services start postgresql@16
createdb m4   # or whatever database name your DATABASE_URL points at
```

Apply migrations:

```bash
bun run db:migrate
```

> **Known issue:** `drizzle-kit migrate` (this script) has hung indefinitely
> against a local Postgres in testing here, even though the driver connects
> fine directly (isolated with a one-off `postgres` package script) — looks
> like a `drizzle-kit` CLI bug, not a connection problem. If it hangs, Ctrl-C
> and apply the SQL files directly instead, **in filename order**:
> `psql "$DATABASE_URL" -f migrations/0000_magenta_black_tom.sql`
> `psql "$DATABASE_URL" -f migrations/0001_whole_smasher.sql`
> `psql "$DATABASE_URL" -f migrations/0002_simple_deadpool.sql`
> (and any later migration files, same way). `migrations/meta/` is
> committed (not gitignored, since 2026-08-11 — see its own note in
> `.gitignore`) precisely so `drizzle-kit generate` computes the next
> migration as an incremental diff against what's already applied, instead
> of re-emitting a full-schema baseline that collides with it.

The bridge (`apps/bridge`) needs to be running and reachable at `BRIDGE_URL`
for `/state`, `/skills/*/invoke`, and `/tasks` to work — start it with
`BRIDGE_TRANSPORT=http` (see `apps/bridge/README.md`). Without it those
routes return `502 bridge_unavailable`; auth and route validation still work.

## Development

```bash
bun run dev
```

Runs on `http://localhost:3000` (`PORT` env var to change). All routes
except `/health` require a session — sign up / log in via `apps/web`, or
directly against `/api/auth/sign-up/email` (Better Auth's REST surface).

## Scripts

| Command             | Description                                    |
| -------------------- | ----------------------------------------------- |
| `bun run dev`        | Dev server, watch mode                          |
| `bun run build`      | Compile to a standalone binary (`./server`)     |
| `bun run start`      | Run the compiled binary (`build` first)         |
| `bun run check-types` | `tsc --noEmit`                                 |
| `bun run test`        | Run the unit tests (`bun:test`, no DB needed — auth/bridge are mocked) |
| `bun run db:generate` | Generate a Drizzle migration from schema.ts    |
| `bun run db:migrate`  | Apply migrations (see known issue above)       |
| `bun run db:studio`   | Open Drizzle Studio                            |

## Source layout

```
src/
  index.ts          Elysia app — composes routes behind {auth: true}, except /health
  lib/auth.ts        Better Auth config (Drizzle adapter, admin + organization plugins)
  db/                Drizzle schema + client
  bridge/client.ts    MCP client — calls the bridge's tools over BRIDGE_URL
  routes/
    state.ts          GET /state — proxies get_state
    skills.ts          GET /skills, POST /skills/:name/{invoke,dry-run}
    tasks.ts            GET /tasks, POST /tasks/:task_id/cancel
    agent.ts            POST /agent — streaming internal-agent chat (needs ANTHROPIC_API_KEY)
  skills/            Catalogue derived live from the bridge's MCP listTools()
                     (see catalogue.ts) — not a hand-duplicated mirror anymore
  agent/runtime.ts    Vercel AI SDK agent loop over the skill registry
```

## Auth

Every route except `/health` is guarded by session auth (`{ auth: true }` in
`index.ts`) — a valid Better Auth session cookie is required. `/agent` in
particular can both move the robot and spend Anthropic API tokens; tighten
further (admin/org-scoped) before any multi-tenant use.
