# C3PO — Technical Specification

**Project:** an embodiment layer that gives Claude (or any MCP-capable LLM) a Unitree G1 humanoid body.
**Status:** spec, pre-implementation. Plan reference: `~/.claude/plans/glistening-chasing-marshmallow.md`.
**Sim target:** Isaac Sim + `unitree_sim_isaaclab` running on a separate Ubuntu machine on the local network. The Mac plays the same role it would play with a real G1 — developer host issuing commands over DDS.

---

## 1. System Overview

Three user types drive the same robot through a shared **skill registry**:

- **Remote supervisor** — human in a SvelteKit web UI, watches and intervenes.
- **External LLM clients** — Claude Code, Claude Desktop, or any MCP-capable client driving via MCP.
- **Co-located human** — speaks to the robot; wake-word triggers a voice loop that feeds an internal Claude agent.

A **Python bridge** on the Mac wraps the Unitree SDK and exposes skills + voice + state. An **Elysia control plane** orchestrates sessions, agent runtime, MCP server, web API, and persistence. A **SvelteKit web UI** is the supervisor surface. The robot is emulated on a separate Ubuntu box running Isaac Sim, talking DDS over LAN.

```
   ┌──────────────────────────────────────────────────────────┐
   │                       MAC (developer host)               │
   │                                                          │
   │  apps/web  ──Eden(HTTP+WS)──▶  apps/back  ──WS──▶  apps/bridge
   │  (Svelte)                      (Elysia)              (Python)
   │                                   │                    │
   │                                   │                    │ DDS / Cyclone
   │                                   ▼                    │  (UDP unicast,
   │                              Postgres                  │   peer config)
   │                              (Neon)                    │
   │                                                        │
   └────────────────────────────────────────────────────────┼─┘
                                                            │
   ┌─────────── External MCP clients ────────────┐          │
   │  Claude Code (this terminal)                │          │
   │  Claude Desktop                              │ stdio    │
   │  Any MCP-capable client                      │  or HTTP │
   │                                              ├──────────┘
   │  (initial route hits apps/bridge directly;   │
   │   later route hits apps/back's MCP adapter)  │
   └──────────────────────────────────────────────┘
                                                            │ LAN
                                                            ▼
   ┌──────────────────────────────────────────────────────────┐
   │                  UBUNTU (Isaac Sim host)                 │
   │                                                          │
   │  Isaac Lab + Isaac Sim                                   │
   │  unitree_sim_isaaclab  → emits / consumes DDS topics     │
   │                                                          │
   └──────────────────────────────────────────────────────────┘
```

---

## 2. Component Index

| # | Workspace            | Lang / Runtime    | Role                                          | Status        |
|---|----------------------|-------------------|-----------------------------------------------|---------------|
| 1 | `apps/back`          | TS / Bun          | Control plane, agent runtime, MCP, REST/WS    | exists        |
| 2 | `apps/web`           | TS / SvelteKit 5  | Supervisor UI                                 | exists        |
| 3 | `apps/bridge`        | Python 3.12 / uv  | Robot SDK wrapper, voice loop, MCP entry      | **NEW**       |
| 4 | `packages/shared`    | TS                | Zod schemas, event types, error taxonomy      | **NEW**       |
| — | Isaac Sim host       | Python (Ubuntu)   | Simulator emulating the G1                    | external      |
| — | Claude Code / Desktop| —                 | MCP client driving the robot                  | external      |

Workspace naming: existing apps use `@repo/back` and `@repo/web`. New TS workspace will be `@repo/shared`. The Python workspace doesn't carry a JS package name but lives at `apps/bridge` with a thin `package.json` so Turbo sees it as a workspace.

---

## 3. apps/back — Elysia control plane

### Purpose
Single source of truth for the **skill catalogue** (what the robot can do, in human-readable + LLM-readable form). Hosts the Internal Agent runtime that drives the robot via Claude. Hosts an MCP server adapter so external LLM clients can also drive. Owns durable state (sessions, episodes, landmarks, tool-call log). Exposes a typed REST + WebSocket API to the supervisor UI via Eden Treaty.

### Runtime
- **Bun** ≥ 1.3.12 (already pinned at root)
- **Elysia** (`latest`) with TypeBox for HTTP-route validation; **Zod** for skill/tool schemas
- TypeScript 5.9.x

### Dependencies (additions)
```jsonc
{
  "ai":                      "^5.x",        // Vercel AI SDK
  "@ai-sdk/anthropic":       "^2.x",        // Claude provider
  "@modelcontextprotocol/sdk":"^1.x",        // MCP server (TS)
  "@elysiajs/zod":           "^1.x",        // Zod ↔ TypeBox bridge for routes that share skill schemas
  "zod":                     "^4.x",
  "drizzle-orm":             "^0.45.1",     // existing
  "drizzle-kit":             "^0.31.10",    // existing
  "@repo/shared":            "workspace:*"  // NEW
}
```

### Source layout (target)
```
apps/back/src/
  index.ts                # composes plugins, exports `App` (existing)
  lib/
    auth.ts               # Better Auth + admin + organization (existing)
    env.ts                # NEW — typed env loader (Zod)
  db/
    schema.ts             # MODIFY — add 4 tables + pgvector
    drizzle.ts            # existing
  routes/
    health.ts             # extracted from index.ts
    dashboard.ts          # extracted from index.ts
    sessions.ts           # NEW — supervisor REST: list/start/stop session
    skills.ts             # NEW — REST: catalogue + invoke + dry-run + cancel
    ws-supervisor.ts      # NEW — WebSocket fan-out to web UI
  skills/
    define.ts             # NEW — defineSkill() helper, registry collector
    index.ts              # NEW — barrel export of all skills
    walk-to.ts            # NEW
    say.ts                # NEW
    get-state.ts          # NEW
    …                     # ~12 in v1
  agent/
    runtime.ts            # NEW — AI SDK + tool loop + streaming
    session.ts            # NEW — Session Manager (start/stop/idle)
    memory.ts             # NEW — pgvector retrieval + summary writer
    prompt.ts             # NEW — system prompt + context assembly
  mcp/
    server.ts             # NEW — MCP adapter exposing skill registry
    auth.ts               # NEW — API-token auth for MCP endpoint
  bridge/
    client.ts             # NEW — typed WS client to apps/bridge
    fanout.ts             # NEW — broadcasts bridge events to subscribers
```

### Public surfaces

**REST (Eden-typed, all `{ auth: true }` unless noted):**
- `GET /health` — public
- `GET /skills` — list skill catalogue (schemas, descriptions, danger levels)
- `POST /skills/:name/invoke` — run a skill; body validated against Zod schema; returns `{ task_id }`
- `POST /skills/:name/dry-run` — same but returns simulated result, no robot motion
- `POST /tasks/:task_id/cancel` — graceful cancel
- `POST /sessions` — start a supervisor session
- `DELETE /sessions/:id` — end session

**WebSocket** (`/ws/supervisor`, cookie-authenticated at upgrade):
- Server → Client: `state`, `progress`, `result`, `voice_event`, `agent_token`, `agent_tool_call`
- Client → Server: `chat_user_turn`, `cancel_task`, `estop`

**MCP** (`/mcp`, streamable HTTP, API-token):
- Each skill maps 1:1 to an MCP tool (same Zod schema → JSON Schema).
- Long-running tools use MCP `progressToken` + Tasks primitive (matches our internal event shape).

**Internal: bridge WS client** (`apps/back/src/bridge/client.ts`):
- Connects to `apps/bridge` (default `ws://127.0.0.1:7077`).
- Send: `execute_skill`, `cancel_task`, `subscribe_state`, `voice_command` (e.g., enable wake).
- Receive: `state`, `progress`, `result`, `voice_event`, `bridge_log`.

### Drizzle additions
```ts
// apps/back/src/db/schema.ts (additions)
sessions          // id, organizationId, userId, startedAt, endedAt, summary, status
toolCallLog       // id, sessionId, taskId, skillName, params (jsonb), result (jsonb),
                  // status, startedAt, endedAt, env ('sim'|'real')
landmarks         // id, organizationId, name, xyzWorld (jsonb), description,
                  // embedding (vector(1024)), lastSeenAt
episodes          // id, organizationId, sessionId, summary, transcript (jsonb),
                  // outcome, embedding (vector(1024)), createdAt
mcpTokens         // id, organizationId, hashedToken, scope (jsonb), createdAt, lastUsedAt
```
- IDs: `text` UUIDs (Better Auth convention).
- Timestamps: `timestamp().defaultNow()` + `.$onUpdate(() => new Date())`.
- JSON: `jsonb` (new column type for the project; document in CLAUDE.md).
- pgvector extension enabled in the same migration; `embedding vector(1024)` columns indexed with HNSW.
- All tenant-scoped tables have `organizationId text` FK to reuse Better Auth's organization plugin.

### Env vars (new)
```
ANTHROPIC_API_KEY=sk-ant-…
BRIDGE_WS_URL=ws://127.0.0.1:7077
MCP_API_TOKEN=…                 # used by MCP clients
EMBEDDING_PROVIDER=anthropic    # or 'voyage' / 'openai'
EMBEDDING_MODEL=voyage-3-large  # 1024-dim default
SESSION_IDLE_TIMEOUT_S=30
AGENT_MAX_STEPS=12
```
Plus existing: `DATABASE_URL`, `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `WEB_URL`, OAuth IDs.

---

## 4. apps/web — SvelteKit supervisor UI

### Purpose
Operator console: live state, chat with the internal agent (text), event timeline, skill catalog, e-stop, session history, replay.

### Runtime
- **SvelteKit 5** (existing) with runes globally enabled
- **Tailwind 4** + **bits-ui** + shadcn-svelte style components (existing)
- **Eden Treaty** via `apps/web/src/lib/api.ts` (existing)

### Dependencies (additions)
```jsonc
{
  "ai":          "^5.x",        // Vercel AI SDK Svelte adapter for streaming chat
  "@ai-sdk/svelte": "^3.x",
  "@repo/shared": "workspace:*"
}
```
(Web uses AI SDK only for chat-stream UI helpers — actual model calls happen in `apps/back`.)

### Source layout (additions)
```
apps/web/src/
  lib/
    components/
      supervisor/
        ChatPanel.svelte
        StatePanel.svelte
        SkillCatalog.svelte
        ToolCallCard.svelte
        EventTimeline.svelte
        EStopButton.svelte
    stores/
      supervisor-ws.svelte.ts  # rune-based WS subscription store
  routes/
    (protected)/
      supervisor/
        +page.svelte           # operator console (single-robot v1)
        +page.server.ts        # SSR session bootstrap
        sessions/
          +page.svelte         # session history list
          [id]/+page.svelte    # session replay
```

### Auth
- Existing `(protected)` group + `+layout.server.ts` redirect-to-`/login` (unchanged).
- Supervisor WS connects with cookie credentials — `apps/back` validates at upgrade.

---

## 5. apps/bridge — Python sidecar (the bridge)

### Purpose
The hardware-touching half of the system. Wraps `unitree_sdk2_python`; runs `py_trees` skill executor; handles voice (wake word, streaming STT, streaming TTS); samples robot state and pushes events upstream. Exposes:
1. A **WebSocket protocol** (`apps/back` is the consumer) for the orchestrated app.
2. A **stdio MCP server** so Claude Code can drive directly during early development without `apps/back` involved.

### Runtime
- **Python 3.12** managed via **uv** (system Python is 3.9.6, unusable).
- Project layout:
  ```
  apps/bridge/
    pyproject.toml                # uv project, ruff, pytest, mypy
    .python-version               # 3.12
    package.json                  # shim: dev script shells `uv run …`
    .env.example
    src/bridge/
      __init__.py
      main.py                     # entrypoint (WS server + MCP launcher)
      mcp_server.py               # stdio MCP server (FastMCP-style)
      transport/
        ws_server.py              # WebSocket server (consumed by apps/back)
        events.py                 # typed event dataclasses (mirror @repo/shared)
        protocol.py               # message envelopes, framing, heartbeats
      skills/
        __init__.py
        registry.py               # name → handler dispatch
        base.py                   # Skill base class, progress emitter, cancel token
        walk_to.py
        say.py
        get_state.py
        stand_up.py
        sit_down.py
        damp.py
        turn.py
        wave.py
        point_at.py
        look.py
        describe_scene.py
        remember_landmark.py
        recall_landmark.py
        stop_everything.py
      sdk/
        loco_client.py            # wraps unitree_sdk2_python LocoClient
        state.py                  # DDS state subscription
        connection.py             # CycloneDDS peer config, env-driven
        env_mode.py               # SIM_MODE / robot IP gating
      voice/
        wake.py                   # openWakeWord (custom or stock)
        stt.py                    # Deepgram streaming
        tts.py                    # Cartesia streaming
        audio_io.py               # PortAudio via sounddevice
        loop.py                   # turn-taker, reflex cancel matcher
      safety/
        envelopes.py              # velocity/workspace clamps
        estop.py                  # local pubsub (asyncio) for emergency stop
      tools/
        seed_landmarks.py         # dev helper
    tests/
      test_skills.py
      test_protocol.py
  ```

### Dependencies (`pyproject.toml`)
```toml
[project]
name = "bridge"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    # MCP server
    "mcp[cli]>=1.0",                # official Anthropic Python SDK (FastMCP-style decorators)
    # Robot SDK
    "unitree_sdk2py>=1.0",          # unitree_sdk2_python (PyPI name varies; pin via git ref if needed)
    "cyclonedds>=0.10.2",
    # Behavior trees
    "py_trees>=2.3",
    # Async + transport
    "websockets>=13",
    "anyio>=4",
    "pydantic>=2.8",
    # Voice
    "openwakeword>=0.6",            # wake word
    "deepgram-sdk>=4",              # streaming STT
    "cartesia>=1.4",                # streaming TTS
    "sounddevice>=0.5",
    "numpy>=2",
    # Logging / config
    "structlog>=24",
    "python-dotenv>=1",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "ruff>=0.7",
    "mypy>=1.13",
]

[tool.uv]
managed = true
```
- `unitree_sdk2_python` is published under varying names; we'll pin via the official GitHub URL in `tool.uv.sources` to avoid PyPI ambiguity.
- `mujoco` is **not** a dependency — Isaac Sim runs on the Ubuntu host, not on the Mac.

### Concurrency model
- **`asyncio` everywhere.** A single event loop hosts: WS server, MCP server (stdio), DDS subscribers (bridged through a thread pool), audio capture, voice pipeline.
- DDS sub/pub from `unitree_sdk2_python` is callback-based and threaded; bridged into asyncio via `asyncio.run_coroutine_threadsafe` and a typed event queue.
- Each skill runs as an `asyncio.Task` with a `CancelToken`. Skills emit progress through a typed `ProgressEmitter` (writes to outgoing WS + MCP progressToken).

### Entry modes
- **Mode 1 — Standalone MCP server** (Step A of bring-up):
  ```
  uv run python -m bridge.mcp_server
  ```
  Registered in `.mcp.json`. Tools = skills directly. No `apps/back` involved.
- **Mode 2 — Full sidecar** (later phases):
  ```
  uv run python -m bridge.main
  ```
  Starts WS server on `:7077` for `apps/back`, plus voice loop + state sampler.
- Both modes share the same skill registry; only the transport differs.

### Env vars (`apps/bridge/.env.example`)
```
# Robot connection
SIM_MODE=isaac                  # 'isaac' | 'mujoco_local' | 'real'
DDS_DOMAIN_ID=0
ROBOT_HOST=192.168.1.42         # Ubuntu Isaac Sim host (or real G1 Jetson IP)
CYCLONEDDS_URI=                 # auto-generated by sdk/connection.py from ROBOT_HOST

# Bridge transport
BRIDGE_WS_HOST=127.0.0.1
BRIDGE_WS_PORT=7077

# Voice
DEEPGRAM_API_KEY=
CARTESIA_API_KEY=
CARTESIA_VOICE_ID=
WAKE_WORD_MODEL=hey_claude.tflite   # path or stock id

# Logging
LOG_LEVEL=info
```

### CycloneDDS peer configuration (macOS gotcha)
macOS multicast is unreliable; we generate a unicast peer XML at startup based on `ROBOT_HOST`:
```xml
<CycloneDDS>
  <Domain id="any">
    <General>
      <Interfaces>
        <NetworkInterface autodetermine="true" />
      </Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <Peers>
        <Peer address="${ROBOT_HOST}" />
      </Peers>
      <ParticipantIndex>auto</ParticipantIndex>
    </Discovery>
  </Domain>
</CycloneDDS>
```
Written to `apps/bridge/.dds.xml`, exposed via `CYCLONEDDS_URI=file://…`. Same pattern works for the real G1 — just change `ROBOT_HOST`.

---

## 6. packages/shared — Zod schemas + types

### Purpose
Single source of truth for skill parameter shapes, event types, and error taxonomy. Imported by both `apps/back` (for routes, agent, MCP server) and `apps/web` (via Eden's type chain). Python code in `apps/bridge` mirrors these by hand for v1; can be auto-generated from JSON Schema later if drift becomes painful.

### Layout
```
packages/shared/
  package.json              # name "@repo/shared", "type": "module"
  tsconfig.json             # extends back's; emits .d.ts
  src/
    index.ts
    skills.ts               # SkillDefinition, DangerLevel, Classification
    events.ts               # SkillEvent union (progress / result / state / voice_event)
    errors.ts               # error taxonomy (PreconditionFailed, Cancelled, …)
    primitives.ts           # Pose, Battery, Posture, FrameRef, Vector3, etc.
```

### Type sketch
```ts
// packages/shared/src/primitives.ts
export const Pose = z.object({
  x_meters_world: z.number(),
  y_meters_world: z.number(),
  yaw_radians_world: z.number(),
});
export type Pose = z.infer<typeof Pose>;

// packages/shared/src/events.ts
export const SkillEvent = z.discriminatedUnion('type', [
  z.object({ type: z.literal('progress'), task_id: z.string(),
             phase: z.string(), progress: z.number(), data: z.record(z.unknown()).optional() }),
  z.object({ type: z.literal('result'),   task_id: z.string(),
             status: z.enum(['ok','error','cancelled']),
             data: z.unknown().optional(), error: z.string().optional() }),
  z.object({ type: z.literal('state'),    pose: Pose,
             battery_pct: z.number(), posture: z.string(),
             faults: z.array(z.string()) }),
  z.object({ type: z.literal('voice_event'),
             kind: z.enum(['wake','partial','final','tts_started','tts_ended']),
             text: z.string().optional(), session_id: z.string() }),
]);
export type SkillEvent = z.infer<typeof SkillEvent>;
```

### No runtime code
Pure types + Zod schemas. No build step beyond TS — Bun consumes `.ts` directly.

---

## 7. External: Ubuntu Isaac Sim host

### Role
Emulates the G1 robot. Same DDS topics as the real robot, so all `apps/bridge` code paths work identically against sim or hardware — only `ROBOT_HOST` changes.

### Stack on Ubuntu
- Isaac Lab + Isaac Sim (already installed by user)
- `unitree_sim_isaaclab` repo: <https://github.com/unitreerobotics/unitree_sim_isaaclab>
- CycloneDDS for the topic layer.

### Topics consumed by `apps/bridge`
- `lowstate` (read) — joint state, IMU, battery, faults
- `lowcmd` (write) — low-level joint commands (rare in v1; high-level path preferred)
- `rt/sportmodestate` (read) — locomotion FSM state
- `api/sport/request` ↔ `api/sport/response` (RPC) — high-level locomotion (`Move`, `StandUp`, `Damp`, `WaveHand`)

### Network
- Same subnet as Mac.
- DDS unicast peer config (above) avoids macOS multicast pain.
- `DDS_DOMAIN_ID` must match on both ends (default `0`).

### Bring-up checklist (Ubuntu side, out of this repo)
1. `unitree_sim_isaaclab` cloned and runnable.
2. G1 scene launches and exposes the topics above.
3. `cyclonedds tools` installed for sniffing (`ddsperf` / `cyclonedds ps`).
4. Firewall allows the DDS port range from the Mac's IP.

---

## 8. External: Claude Code / external MCP clients

### Role
Drive the robot's skills as MCP tools — the "give Claude a body" demo path. Two connection paths:

- **Direct to bridge** (early development): Claude Code launches `apps/bridge`'s stdio MCP server via `.mcp.json`. No Elysia involved. Fast iteration.
- **Through Elysia** (later): Claude Desktop connects to `apps/back`'s MCP HTTP endpoint with an API token. Same skill catalogue; adds session/audit/memory benefits.

### Bootstrap-only nature
Both paths above use Claude Code / Claude Desktop as the **driver of the conversation**. This is a bootstrap convenience — it lets us validate the skill ABI against a real frontier LLM in hours rather than weeks, before any agent runtime exists. **It is not the long-term primary driver.** See §12.1 for the migration to the regular Claude API.

### `.mcp.json` entry (Step A — wired up and verified)
```jsonc
{
  "mcpServers": {
    "c3po-bridge": {
      "command": "uv",
      "args": ["run", "--project", "apps/bridge", "python", "-m", "bridge.mcp_server"],
      "env": {
        "SIM_MODE": "stub"
      }
    }
  }
}
```
At Step B, the `env` block changes to `SIM_MODE=isaac` and adds `ROBOT_HOST` etc.

---

## 9. Wire formats

### Skill invocation (Eden REST → bridge WS)
```
POST /skills/walk_to/invoke
{
  "params": { "target": { "landmark": "window" }, "stop_distance_m": 1.0 },
  "dry_run": false,
  "session_id": "ses_abc"
}
→ 202 { "task_id": "tsk_…", "estimated_duration_s": 15 }
```
Internally: `apps/back` → `bridge/client.ts` sends:
```json
{ "type": "execute_skill",
  "task_id": "tsk_…",
  "skill_name": "walk_to",
  "params": { … },
  "session_id": "ses_abc",
  "env": "isaac" }
```
Bridge responds with a stream of `SkillEvent`s (see §6 schema).

### MCP tool call (Step A)
```jsonc
// Tool definition emitted by FastMCP
{
  "name": "walk_to",
  "description": "Walk to a world-frame position or known landmark.",
  "inputSchema": { /* JSON Schema from Zod */ },
  "outputSchema": { /* result shape */ }
}
```
Long-running tools use `progressToken`; the server emits MCP `notifications/progress` events that map 1:1 to our `SkillEvent.progress`.

### Cancel
- REST: `POST /tasks/{id}/cancel` body `{ mode: "graceful" | "estop" }`
- WS (web → back): `{ type: "cancel_task", task_id, mode }`
- Bridge: cancellation token flips, skill task observes between progress emits, gracefully ramps velocity to zero (or transitions to `damp` for estop).

---

## 10. Network ports & topology

| Service             | Host    | Port  | Transport              | Auth                    |
|---------------------|---------|-------|------------------------|-------------------------|
| `apps/back` HTTP    | Mac     | 3000  | HTTP                   | Better Auth cookie      |
| `apps/back` WS      | Mac     | 3000  | WebSocket (`/ws/*`)    | cookie at upgrade       |
| `apps/back` MCP     | Mac     | 3000  | streamable HTTP `/mcp` | API token               |
| `apps/web` dev      | Mac     | 3001  | HTTP (Vite)            | —                       |
| `apps/bridge` WS    | Mac     | 7077  | WebSocket              | shared token (loopback) |
| `apps/bridge` MCP   | Mac     | stdio | stdio                  | implicit (process)      |
| Isaac Sim DDS       | Ubuntu  | 7400+ | UDP (CycloneDDS)       | none (LAN)              |

`apps/bridge` binds to **127.0.0.1** by default — never public. The bridge↔back link is loopback-only on the dev machine, with a shared header token to prevent another local process from connecting.

---

## 11. Build & dev workflow

### Root commands (existing, unchanged)
```bash
bun install            # installs all TS workspaces
bun run dev            # turbo dev → spawns back, web, bridge
bun run check-types    # turbo check-types
bun run build          # turbo build (TS apps only)
```

### Bridge-specific
```bash
# First-time setup
cd apps/bridge
uv sync                            # installs Python 3.12 + deps into .venv

# During dev (turbo handles via the package.json shim)
uv run python -m bridge.main       # full sidecar mode
uv run python -m bridge.mcp_server # standalone MCP for Claude Code

# Tests
uv run pytest
```

### Turbo integration
`apps/bridge/package.json`:
```jsonc
{
  "name": "@repo/bridge",
  "private": true,
  "scripts": {
    "dev":   "uv run --project . python -m bridge.main",
    "build": "uv sync",
    "check-types": "uv run mypy src",
    "test":  "uv run pytest"
  }
}
```
This is enough for `turbo run dev` to start the bridge alongside back+web. No changes to `turbo.json`.

### Database
```bash
cd apps/back
bunx drizzle-kit generate    # after schema edits
bunx drizzle-kit migrate     # apply
bunx drizzle-kit studio      # GUI
```

---

## 12. Implementation phases (cross-reference)

These map to the plan file but are restated with the Isaac-Sim-on-Ubuntu reality and the **MCP-first** reordering chosen during the last session.

| Phase | Goal                                                              | Demo                                                            | Effort |
|-------|-------------------------------------------------------------------|-----------------------------------------------------------------|--------|
| 0a    | `apps/bridge` scaffold + stub MCP server registered in `.mcp.json`| Claude Code calls `walk_to` stub, sees fake result              | 1 day  |
| 0b    | DDS peer config + Isaac Sim handshake (`get_state` real)          | `get_state` returns real pose from Isaac Sim                    | 2-3 d  |
| 1     | `walk_to` real, full ABI: task_id, progress, cancel               | Robot in Isaac walks, progress streams, cancel works            | 1-2 wk |
| 2     | Remaining ~11 skills + safety envelopes + landmark seed           | All skills exercised via MCP and tested                         | 2-3 wk |
| 3     | `@repo/shared` + `apps/back` skill registry + agent runtime + memory | Operator types in supervisor UI, Claude decomposes & executes | 2-3 wk |
| 4     | Voice loop (wake → STT → agent → TTS) + reflex cancel             | Spoken command works end-to-end                                 | 2-3 wk |
| 5     | Elysia-side MCP adapter + API tokens                              | Claude Desktop drives the robot via the orchestrated path       | 1 wk   |
| 6     | Replay, tracing (OTel), org-tenancy gating, polish                | Sessions replayable; multi-tenant gating verified               | 1 wk   |

**Total v1 scope:** ≈ 3–4 months solo against Isaac Sim.

### 12.1 Driver evolution: from MCP-bootstrap to Anthropic API

The chosen sequencing exploits a clean property of the design: the **skill registry is independent of who drives it.** The same Zod-typed catalogue is consumed by every driver. This means we can swap the conversation host without touching the skills, the bridge, or the protocol.

| Stage | Conversation host                 | Tool runtime location          | What ships when                                 |
|-------|-----------------------------------|--------------------------------|-------------------------------------------------|
| Now   | **Claude Code** (this terminal)   | `apps/bridge` stdio MCP server | Phase 0a–0b (bootstrap, fastest demo)           |
| Mid   | **Claude Desktop** or other MCP client | `apps/back` MCP HTTP adapter   | Phase 5 (multi-client, audited, tenant-aware)    |
| Long-term | **`apps/back` Internal Agent** (Anthropic Messages API directly via AI SDK) | `apps/back` Skill Dispatcher | Phase 3 (the "give Claude a body" product shape) |

The long-term default is the third row. `apps/back` calls Anthropic's regular Messages API (`anthropic.messages.create` via `@ai-sdk/anthropic`) with the skill registry exposed as `tools`. The agent loop runs server-side: Claude responds → tool calls dispatch to `apps/bridge` over the existing WS protocol → results stream back into the next turn → response continues. The supervisor UI sees the same streaming events it already sees from MCP-driven flows.

**What this requires** (already enumerated in Phase 3 of §12):
1. `ai` + `@ai-sdk/anthropic` installed in `apps/back` (already in §3 dependency list).
2. `ANTHROPIC_API_KEY` in `apps/back/.env` (already in §3 env list).
3. `apps/back/src/agent/runtime.ts` — a tool-calling loop that converts the Zod skill catalogue into AI SDK `tool()` definitions, streams to the supervisor UI via the existing WS, and dispatches each tool call through `apps/back/src/bridge/client.ts`.
4. Memory + session manager (already specified in §3).

**Why it's fully doable:**
- AI SDK 5's `streamText` + `tools` is purpose-built for this exact pattern.
- Each Zod skill schema converts to an AI SDK tool with a one-line wrapper (`tool({ inputSchema: skill.parameters, execute: (p) => bridgeClient.executeSkill(skill.name, p) })`).
- The bridge protocol is identical regardless of who originated the tool call. No second integration to write.
- Streaming progress events, cancel, and dry-run already flow through `bridge/client.ts` — the agent runtime just subscribes the same way the MCP adapter does.
- Anthropic's API supports prompt caching, parallel tool use, and the new long-running tool patterns we already designed around.

**No fork in the codebase.** All three driver stages share: the skill registry, the bridge protocol, the WS event types, the database schema. Migration is additive — adding the Internal Agent doesn't require removing or refactoring the MCP path. Both can run forever.

---

## 13. Open decisions to confirm before code

1. **Embedding provider.** Claude doesn't ship one; pick `voyage-3-large` (1024-dim) or OpenAI `text-embedding-3-large`. Default in this spec: Voyage.
2. **MCP client direct connection vs. through Elysia.** v1 plan keeps both. Step A starts with direct.
3. **Wake-word model.** "hey claude" custom or stock placeholder? Custom needs ~30 min training data; stock ("alexa") works for dev only.
4. **Audio I/O location.** v1 uses Mac's microphone/speakers; eventually moves to G1's onboard mics + speakers. The audio device selection will be env-driven.
5. **Postgres host.** Audit showed Neon. pgvector availability on Neon is fine (built-in). Confirm before migration.
6. **Single-robot vs. multi-robot data model.** v1 is single-robot, but `organizationId` already gates everything; adding `robotId` is a non-breaking addition later.

---

## 14. Out of scope (v1)

- Real G1 hardware (design ready; only `ROBOT_HOST` and `SIM_MODE=real` change).
- VLA-based manipulation (UnifoLM, Pi0-FAST). Tool-call seam already present.
- Semantic perception (ConceptGraphs, HOV-SG). v1 uses hand-seeded landmarks.
- Ambient/always-on agent thinking. v1 is reactive.
- Multi-robot fleet (one org = one robot).
- Eye-contact / face-tracking attention. v1 uses wake word.
- On-Jetson deployment of the bridge.
- Session replay with audio playback (text replay only).

---

## 15. Glossary

- **Skill** — a discrete robot capability with a Zod-typed parameter schema, preconditions, expected duration, danger level, and an executor. Defined in TS, executed in Python.
- **Task** — one invocation of a skill, identified by a `task_id`. Streams progress; supports cancel.
- **Session** — a window during which an agent (internal or external) is interacting with the robot. Bounded by start/end events; produces an episode.
- **Episode** — durable record of a session (transcript, tool calls, outcome, embedding) for memory recall.
- **Bridge** — the Python sidecar at `apps/bridge` that owns the SDK, audio, and DDS connection.
- **Internal agent** — Claude running inside `apps/back` driving the robot via the skill registry.
- **External MCP client** — Claude Code, Claude Desktop, or any MCP-capable client driving via MCP.
- **Reflex cancel** — bridge-local fast-path cancel triggered by safety phrases without an LLM round-trip (~100–300 ms).

