# C3PO Backend (`apps/back`)

Elysia control plane: session auth (Better Auth), the skill catalogue, chat
persistence, and an MCP client that proxies skill calls to the bridge
(`apps/bridge`). Where it sits in the system:
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

## Setup

```bash
cp .env.example .env
```

`.env.example` is the authority on every variable — including the TIC AI
gateway gotchas (`AGENT_*`) and how `BRIDGE_URL` changes against the real
robot. Read it there rather than expecting a list here.

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
> and apply the SQL directly instead, in order:
>
> ```bash
> psql "$DATABASE_URL" -f migrations/0000_magenta_black_tom.sql
> psql "$DATABASE_URL" -f migrations/0001_whole_smasher.sql
> ```

The bridge must be running with `BRIDGE_TRANSPORT=http` and reachable at
`BRIDGE_URL` for `/state`, `/skills/*/invoke` and `/tasks` to work (see
`apps/bridge/README.md`). Without it those routes return
`502 bridge_unavailable`; auth and route validation still work. Standing
blocker: under Bun the MCP client currently cannot connect to the bridge at
all — see `docs/OPERATIONS.md`.

## Development

```bash
bun run dev
```

Runs on `http://localhost:3000` (`PORT` in `.env` to change). All routes
except `/health` require a session — sign up / log in via `apps/web`, or
directly against `POST /api/auth/sign-up/email` (Better Auth's REST surface;
handy for headless/API-only testing).

`bun scripts/smoke-agent.ts` checks the internal agent's LLM link (key,
model, tool calling) without needing the bridge, the DB, or a port.
`bun scripts/smoke-routes.ts` smoke-tests the back↔bridge link at the route
level (needs the bridge running in HTTP mode; no DB/auth/port boot).

| Command               | Description                                 |
| --------------------- | ------------------------------------------- |
| `bun run dev`         | Dev server, watch mode                      |
| `bun run build`       | Compile to a standalone binary (`./server`) |
| `bun run start`       | Run the compiled binary (`build` first)     |
| `bun run check-types` | `tsc --noEmit`                              |
| `bun run db:generate` | Generate a Drizzle migration from schema.ts |
| `bun run db:migrate`  | Apply migrations (see known issue above)    |
| `bun run db:studio`   | Open Drizzle Studio                         |

## Source layout

```
src/
  index.ts             Elysia app — /health open, everything else behind {auth: true}
  lib/auth.ts          Better Auth config (Drizzle adapter, admin + organization plugins)
  lib/auth-plugin.ts   Elysia plugin defining the `auth: true` macro (see Auth below)
  db/                  Drizzle schema + client + chat persistence queries
  bridge/client.ts     MCP client — calls the bridge's tools over BRIDGE_URL
  routes/
    state.ts           GET /state — proxies get_state
    skills.ts          GET /skills, GET /skills/:name, POST /skills/:name/{invoke,dry-run}
    tasks.ts           GET /tasks, POST /tasks/:task_id/cancel
    agent.ts           POST /agent — streaming internal-agent chat (needs AGENT_API_KEY)
    chats.ts           GET /chats, GET /chats/:id, DELETE /chats/:id — history
    telemetry.ts       GET /telemetry/{surroundings,voice} — the bridge's own,
                       proxied so a browser never reaches port 8001 directly
    voice.ts           POST /voice/{start,stop}, GET /voice/status — explicit
                       operator-owned legacy or OpenAI Realtime voice session
  voice/loop.ts        legacy transcript → text-agent → Piper fallback
  voice/realtime.ts    PCM ↔ OpenAI WebSocket, tool dispatch, durable transcript
                       (design: docs/ARCHITECTURE.md §4.1)
  skills/              catalogue derived from the bridge at runtime (listTools +
                       cache — see catalogue.ts), not hand-mirrored; metadata like
                       danger level and preconditions rides in the tools' _meta
  agent/runtime.ts     Vercel AI SDK agent loop over the skill registry
```

## Auth

Every route except `/health` is guarded by session auth — the
`.guard({ auth: true }, ...)` in `index.ts`. The `betterAuth` plugin in
`src/lib/auth-plugin.ts` defines that macro; a route module that actually
reads `user` must `.use(betterAuth)` itself to get it _typed_ — the
composition-root guard enforces auth at runtime but leaves `user` untyped
inside the module. Pulling the plugin in from several modules is free
(Elysia deduplicates by plugin name).

Two modules are mounted **beside** that guard rather than inside it:
`skillsRoutes` and `auditRoutes` carry their own `{ auth: true }` guard, and
nesting them inside the outer one resolved the session twice per request
(measured on Elysia 1.4.28) — which matters for `/skills/:name/invoke`, behind
PARAR and the ~1 Hz VR walk loop. `mounted.test.ts` pins both mounts.
`POST /skills/stop_everything/invoke` never waits on the catalogue (no
schema, no admin check) and is raced against an 8 s timer rather than given an
SDK timeout — the SDK's timeout cancels the call on the bridge, mid-stop. On the
timer it answers 504 `bridge_timeout` while the stop keeps running. Other
skills read the catalogue, which is reused for 5 s (the VR walk loop invokes
about once a second) and whose `tools/list` is bounded at 3 s. `POST /agent`
passes the request's abort signal to the agent loop, so a stopped reply (or a
closed tab) ends its next steps instead of leaving it dispatching skills
unseen. Unexpected errors are answered with a generic `internal_error` body;
the real one goes to the log (a drizzle error's message is its SQL).
`GET /audit` (the command log) is admin-only; `GET /health` is public and also
reports which social sign-in providers are configured (`auth.providers`), so
the login page renders only buttons that work.

**Security caveat:** `/agent` can move the robot, and every authenticated
caller shares the one hand-issued TIC AI key; tighten further
(admin/org-scoped) before any multi-tenant use.

## Database

Drizzle ORM + PostgreSQL. Schema and its conventions live in
`src/db/schema.ts` (the comments there are the authority). Better Auth owns
`user`, `session`, `account`, `verification`, `organization`, `member`,
`invitation`; the C3PO operational tables are `chat`, `chat_message`, and
`tool_call_log`.

`tool_call_log` is the audit trail of every skill dispatched to the robot, by
any path: `source` is `agent`, `voice` or `direct` (`/skills/:name/invoke` —
every VR button, the walk loop, bring-up and PARAR), and `user_id` says who.
The write is fire-and-forget on every path: a stop must never wait on the
database. A 200 whose body reports failure (`status: "failed"`, a non-empty
`error`) is logged as `error` — the bridge never raises. `chat_id` is
`ON DELETE SET NULL`, not cascade: deleting a chat must not erase the record of
what it made the robot do.

- **`session` vs `sessions`:** `session` (singular) is Better Auth's login
  session. The architecture also calls an operator/supervisor run a session
  (`sessions`, planned — not yet in the schema). Never conflate the two;
  glossary in `docs/ARCHITECTURE.md`.
- **`migrations/meta/` must stay committed.** Without the journal, every
  `db:generate` on a fresh clone emits a full-schema `0000` that collides
  with what is already applied.
- **Migrations are additive-only** for now. There is no rollback story;
  adding one before the first destructive migration is cheaper than after.
- **pgvector (future work):** drizzle-kit does not emit `CREATE EXTENSION`.
  Any migration that introduces a `vector` column or index into a fresh DB
  must ensure `CREATE EXTENSION IF NOT EXISTS vector` runs first — and the
  DB needs the pgvector package installed and a role with CREATE privilege.
  The planned `landmarks`/`episodes` `vector(1024)` columns were sized for
  Voyage `voyage-3-large`; no embedding code exists yet, so nothing depends
  on that choice. TIC AI advertises a `tic-embed` (same gateway and key as
  the agent) as a possible substitute, but `GET /models` did not list it on
  2026-08-18 — check the endpoint before depending on it.
