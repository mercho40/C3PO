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
> and apply the SQL directly instead — every file in `migrations/`, in order
> (the `NNNN_` prefixes sort correctly):
>
> ```bash
> for f in migrations/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f" || break; done
> ```
>
> On a database that already has some of them, start at the first one missing.
> Nothing is recorded in drizzle's `__drizzle_migrations` table this way, so a
> later `db:migrate` against the same database will try them all again.

The bridge must be running with `BRIDGE_TRANSPORT=http` and reachable at
`BRIDGE_URL` for `/state`, `/skills/*/invoke` and `/tasks` to work (see
`apps/bridge/README.md`). Without it those routes return
`502 bridge_unavailable`; auth and route validation still work. Against the
real robot `BRIDGE_URL` is the SSH tunnel's local end (`.env.example`).

## Development

```bash
bun run dev
```

Runs on `http://localhost:3000` (`PORT` in `.env` to change). All routes
except `/health` require a session. Public sign-up is closed (`disableSignUp`
on email and on the GitHub/Google providers, `src/lib/auth.ts`), so accounts
are made from this directory with `bun scripts/create-user.ts <email> <name>
[--admin]`; the same script takes `--reset-password <email>` and `--delete
<email>`. Then log in via `apps/web`, or directly against
`POST /api/auth/sign-in/email` for headless/API-only testing.

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

Nothing runs at import. `index.ts` is the composition root: `loadConfig()`
(every setting, read once), the real edges (`deps.ts`: Postgres, Better Auth,
the bridge's MCP and HTTP clients), `createServices` / `createApp` (`app.ts`),
the background loops, `listen`. Every module gets what it needs as an
argument, so a test builds the same app over fakes (`src/testing/fakes.ts`
`testApp`) and drives it in process with `app.handle()` or Eden's
`treaty(app)` — no database, bridge, port or `mock.module`.

```
src/
  index.ts             composition root: config, real deps, loops, listen
  app.ts               createServices(deps) + createApp(deps): every route mounted;
                       /health open, everything else behind {auth: true}
  config.ts            loadConfig(): the only reader of process.env; fails fast,
                       naming the required variables that are missing
  deps.ts              the real edges, lazy (nothing connects until used)
  lib/auth.ts          createAuth: Better Auth (Drizzle adapter, admin + organization)
  lib/auth-plugin.ts   createAuthPlugin: the `auth: true` macro (see Auth below)
  lib/admin-bootstrap.ts  grants role "admin" to ADMIN_EMAILS at boot
  db/                  createDb, the chat store (chats.ts, incl. logToolCall),
                       the command-log reader (audit.ts)
  bridge/client.ts     createBridgeClient: MCP callTool / listTools, one session
  bridge/http.ts       createBridgeHttp: the bridge's HTTP routes — one timeout
                       policy, one error mapping, the proof header built once
  bridge/payloads.ts   the bridge's JSON payloads as TypeBox, for `response:`:
                       derived from contracts/ where it describes them, hand-
                       written for telemetry and «Caras»; extra fields pass
  bridge/stop.ts       stopRobot / dispatchDirect: THE stop path (PARAR, soft_stop,
                       the /tasks estop)
  bridge/url.ts        BRIDGE_URL's default and its sibling HTTP routes
  skills/dispatch.ts   dispatchSkill: send a skill and audit it — the one dispatch
                       the agent, realtime voice, /invoke and the stop share
  skills/invoke.ts     /invoke's gate, validation and bridge grant; SAFETY_SKILLS
  skills/catalogue.ts  createCatalogue: derived from the bridge at runtime
                       (listTools + cache), not hand-mirrored; metadata like
                       danger level and preconditions rides in the tools' _meta
  agent/confirmations.ts  pending «Confirmar» cards; outcome subscribers
  agent/confirm-service.ts  Confirmar / Cancelar / the face confirm: grant AND run
  agent/turns.ts       agent turns in flight; PARAR aborts them and drops the cards
  agent/stop-watch.ts  a stop latched on the bridge directly aborts them too
  agent/models.ts      the gateway, its fallback, the vision model
  agent/prompt.ts      system prompt, voice preamble, world snapshot
  agent/policy.ts      which skills each mode is offered
  agent/tools.ts       the AI SDK tools: bridge skills, back's look / verify
  agent/runtime.ts     createAgentRuntime: one Vercel AI SDK turn
  voice/service.ts     createVoiceService: legacy loop, realtime session,
                       listen-after-greet, one engine at a time
  voice/loop.ts        legacy transcript → text-agent → Piper fallback; with the
                       bridge on C3PO_STT=back it fetches each robot-mic WAV and
                       transcribes it with tic-transcript (AGENT_TRANSCRIBE_MODEL)
  voice/realtime.ts    PCM ↔ OpenAI WebSocket, tool dispatch, durable transcript
                       (design: docs/ARCHITECTURE.md §4.1)
  voice/conversation.ts, events.ts, pcm.ts, transcribe.ts, engine.ts
                       turn assembly, the bridge's voice events, PCM resampling,
                       speech to text, the engine choice
  routes/              HTTP surfaces only — each a factory over the services
    state.ts           GET /state — proxies get_state
    skills.ts          GET /skills, GET /skills/:name,
                       POST /skills/:name/{invoke,dry-run,confirm,confirm-face,cancel}
    safety.ts          GET /safety/status, POST /safety/ack (admin, «Reanudar»)
    tasks.ts           GET /tasks, POST /tasks/:task_id/cancel
    agent.ts           POST /agent — streaming internal-agent chat (needs AGENT_API_KEY)
    chats.ts           GET /chats, GET/PATCH/DELETE /chats/:id — history
    audit.ts           GET /audit — the command log (tool_call_log), admin-only
    telemetry.ts       GET /telemetry/{surroundings,scan,voice,gate} — the bridge's
                       own, proxied so a browser never reaches port 8001 directly
    map.ts             GET /map/costmap.png — Nav2's costmap, same reason
    camera.ts          GET /camera/{status,frame.jpg,stream.mjpg} — the robot
                       camera, same reason (when PUBLIC_ROBOT_CAM_URL points here;
                       dev:robot reads the bridge's 8001 instead)
    face.ts            /face — the «Caras» page (admins): people, switches, enroll,
                       delete, photo, each behind a Confirmar card; GET /face/seen,
                       the camera's face boxes, for any signed-in user
    teleop.ts          GET /teleop/token — the teleop socket's token (admins)
    voice.ts           POST /voice/{start,stop}, GET /voice/status — explicit
                       operator-owned legacy or OpenAI Realtime voice session;
                       POST /voice/transcribe — headset push-to-talk STT
  testing/fakes.ts     testApp and its fakes (tests only)
```

## Response schemas

The JSON routes that relay the bridge declare Elysia `response:` schemas
(`bridge/payloads.ts`), so `apps/web` gets their types through Eden instead of
restating them. Each checks the bridge's body itself and answers what it
could not vouch for as **502 `bridge_bad_payload`** with the first field that
did not match (`{error, path, message}`, logged once per change), never
Elysia's own 422, which reads as the caller's mistake:

| Route                                           | Schema                             | Bridge answer that fails                                                                            |
| ----------------------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------- |
| `GET /state`                                    | contracts `RobotState`             | 502 `bridge_bad_payload`                                                                            |
| `GET /safety/status`                            | contracts `SafetyStatusBody`       | 502 `bridge_bad_payload`; a non-200 is 502 `bridge_error`                                           |
| `GET /telemetry/{surroundings,scan,voice,gate}` | hand-written (bridge routes named) | 502 `bridge_bad_payload` (also for a 200 that is not JSON); the bridge's 503 passes with its reason |
| `GET /face/people`                              | hand-written `FacesView`           | 502 `bridge_bad_payload`; the bridge refusing back's proof is 502 `bridge_error`                    |

Every object allows extra properties: a field the bridge adds neither fails a
route nor is stripped. **The stop path declares no schema** — PARAR
(`stop_everything`), `soft_stop`, the `/tasks` estop and `POST /safety/ack`
(«Reanudar», typed as the contract's `StopAcknowledged | StopAckRefused` but
never checked): a stop must not be answered 502 because the bridge described
its success in a new way. Binary routes (camera, costmap PNG, the face photo)
stay raw `Response`s, and so do the «Caras» mutations and `/face/seen`.

## Auth

Every route except `/health` is guarded by session auth — the
`.guard({ auth: true }, ...)` in `app.ts`. The auth plugin
(`createAuthPlugin`, `src/lib/auth-plugin.ts`) defines that macro and is
handed to every route factory as `authPlugin`; a route module that actually
reads `user` must `.use(deps.authPlugin)` itself to get it _typed_ — the
composition-root guard enforces auth at runtime but leaves `user` untyped
inside the module. Pulling the plugin in from several modules is free
(Elysia deduplicates by plugin name).

Two modules are mounted **beside** that guard rather than inside it:
`skillsRoutes` and `auditRoutes` carry their own `{ auth: true }` guard, and
nesting them inside the outer one resolved the session twice per request
(measured on Elysia 1.4.28) — which matters for `/skills/:name/invoke`, behind
PARAR and the ~1 Hz VR walk loop. `app.test.ts` boots the app and counts one
session lookup per request on both, and checks every route of every module is
mounted.
`POST /skills/stop_everything/invoke` never waits on the catalogue (no
schema, no admin check) and is raced against an 8 s timer rather than given an
SDK timeout — the SDK's timeout cancels the call on the bridge, mid-stop. On the
timer it answers 504 `bridge_timeout` while the stop keeps running. Other
skills read the catalogue, which is reused for 5 s (the VR walk loop invokes
about once a second) and whose `tools/list` is bounded at 3 s. `POST /agent`
passes the request's abort signal to the agent loop, so a stopped reply (or a
closed tab) ends its next steps instead of leaving it dispatching skills
unseen. PARAR (the `stop_everything` invoke), `soft_stop`, the `/tasks` estop and
the spoken stop abort every running agent turn (`agent/turns.ts` `abortAll`).
A stop latched on the bridge directly, such as the headset's, does too:
`agent/stop-watch.ts` polls `/safety/status` while a turn runs.
An ended turn (abort or the 240 s budget) cancels the bridge call it is waiting on,
except a stop; the latch itself is the
bridge's (`docs/OPERATIONS.md` §4). `damp` and `zero_torque` are never offered to
a model (`agent/policy.ts` `agentToolExclusions`). Unexpected errors are answered with a generic `internal_error` body;
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
