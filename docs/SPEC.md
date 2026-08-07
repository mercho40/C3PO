# C3PO — Technical Specification

**Project:** an embodiment layer that gives Claude (or any MCP-capable LLM) a Unitree G1 humanoid body.
**Status:** spec, pre-implementation. Plan reference: `~/.claude/plans/glistening-chasing-marshmallow.md`.
**Sim target:** Isaac Sim + `unitree_sim_isaaclab` running on a separate Ubuntu machine on the local network. For sim, the Mac is the developer host issuing commands over DDS.

**Real target:** a Unitree G1 (G1 Plus / PC4 variant). Unlike sim, the Mac **cannot** be the DDS host — see §10.2. The bridge runs onboard the robot's Jetson; `apps/back`, Postgres and `apps/web` stay off-robot.

---

## 1. System Overview

Three user types drive the same robot through a shared **skill registry**:

- **Remote supervisor** — human in a SvelteKit web UI, watches and intervenes.
- **External LLM clients** — Claude Code, Claude Desktop, or any MCP-capable client driving via MCP.
- **Co-located human** — speaks to the robot; wake-word triggers a voice loop that feeds an internal Claude agent.

A **Python bridge** wraps the Unitree SDK and exposes skills + voice + state. An **Elysia control plane** orchestrates sessions, agent runtime, MCP server, web API, and persistence. A **SvelteKit web UI** is the supervisor surface.

Where the bridge *runs* depends on the target. Against Isaac Sim it runs on the Mac and reaches the simulator over LAN DDS. Against real hardware it runs **onboard the robot's Jetson**, because the robot's DDS traffic never leaves its internal wired LAN (§10.2). Everything else — control plane, database, UI — stays off-robot in both cases.

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

Against **real hardware** the split moves — the bridge crosses onto the robot, and Wi-Fi
carries MCP/WS instead of DDS:

```
   ┌──────────────────────────────────────────────────────────┐
   │                       MAC / SERVER                       │
   │                                                          │
   │  apps/web  ──Eden(HTTP+WS)──▶  apps/back ──▶ Postgres     │
   │  (Svelte)                      (Elysia)      (pgvector)  │
   │                                   │                      │
   └───────────────────────────────────┼──────────────────────┘
                                       │ Wi-Fi  (MCP over HTTP,
                                       │         bridge WS + token)
   ┌───────────────────────────────────┼──────────────────────┐
   │                 G1 JETSON  (10.4.64.27)                  │
   │                                   ▼                      │
   │                             apps/bridge                  │
   │                              (Python)                    │
   │                          + link watchdog                 │
   │                                   │ DDS / Cyclone 0.10.2 │
   │                                   │ pinned to eth0       │
   │        eth0 192.168.123.164 ──────┼──────────            │
   └───────────────────────────────────┼──────────────────────┘
                                       │ internal wired LAN
                                       ▼
                        ┌──────────────────────────────┐
                        │  CONTROL BOARD .161          │
                        │  publishes /lowstate,        │
                        │  /api/sport, /api/arm, …     │
                        │  to multicast 239.255.0.1    │
                        └──────────────────────────────┘
```

---

## 2. Component Index

| #   | Workspace             | Lang / Runtime   | Role                                       | Status   |
| --- | --------------------- | ---------------- | ------------------------------------------ | -------- |
| 1   | `apps/back`           | TS / Bun         | Control plane, agent runtime, MCP, REST/WS | exists   |
| 2   | `apps/web`            | TS / SvelteKit 5 | Supervisor UI                              | exists   |
| 3   | `apps/bridge`         | Python 3.12 / uv | Robot SDK wrapper, voice loop, MCP entry   | exists   |
| 4   | `packages/shared`     | TS               | Zod schemas, event types, error taxonomy   | planned  |
| —   | Isaac Sim host        | Python (Ubuntu)  | Simulator emulating the G1                 | external |
| —   | G1 Jetson             | Ubuntu 20.04 ARM | Hosts `apps/bridge` when `SIM_MODE=real`   | external |
| —   | G1 control board      | firmware         | Publishes the robot's DDS topics (§10.2)   | external |
| —   | Claude Code / Desktop | —                | MCP client driving the robot               | external |

`apps/bridge` is the only workspace whose **host changes with the target**: the Mac for `stub`/`isaac`, the G1 Jetson for `real`.

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
  "ai": "^5.x", // Vercel AI SDK
  "@ai-sdk/anthropic": "^2.x", // Claude provider
  "@modelcontextprotocol/sdk": "^1.x", // MCP server (TS)
  "@elysiajs/zod": "^1.x", // Zod ↔ TypeBox bridge for routes that share skill schemas
  "zod": "^4.x",
  "drizzle-orm": "^0.45.1", // existing
  "drizzle-kit": "^0.31.10", // existing
  "@repo/shared": "workspace:*", // NEW
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
sessions; // id, organizationId, userId, startedAt, endedAt, summary, status
toolCallLog; // id, sessionId, taskId, skillName, params (jsonb), result (jsonb),
// status, startedAt, endedAt, env ('sim'|'real')
landmarks; // id, organizationId, name, xyzWorld (jsonb), description,
// embedding (vector(1024)), lastSeenAt
episodes; // id, organizationId, sessionId, summary, transcript (jsonb),
// outcome, embedding (vector(1024)), createdAt
mcpTokens; // id, organizationId, hashedToken, scope (jsonb), createdAt, lastUsedAt
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
  "ai": "^5.x", // Vercel AI SDK Svelte adapter for streaming chat
  "@ai-sdk/svelte": "^3.x",
  "@repo/shared": "workspace:*",
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
SIM_MODE=isaac                  # 'stub' | 'isaac' | 'mujoco_local' | 'real'
DDS_DOMAIN_ID=0                 # Isaac Sim uses 1; the real G1 uses 0
ROBOT_HOST=192.168.1.42         # Isaac Sim host; on real, the control board 192.168.123.161
DDS_INTERFACE=                  # empty = autodetermine (Mac). On the Jetson: eth0
CYCLONEDDS_URI=                 # auto-generated by sdk/connection.py from the two above

# Bridge transport
BRIDGE_WS_HOST=127.0.0.1        # onboard (SIM_MODE=real) this must bind the LAN iface,
BRIDGE_WS_PORT=7077             # and BRIDGE_WS_TOKEN stops being optional — see §10.1
BRIDGE_WS_TOKEN=

# Safety (SIM_MODE=real)
LINK_TIMEOUT_MS=1500            # operator-link silence before the watchdog ramps to zero

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

Written to `apps/bridge/.dds.xml`, exposed via `CYCLONEDDS_URI=file://…`.

**On the real G1 this needs one change: pin the interface.** `autodetermine="true"` is correct on the Mac, but the Jetson has `eth0`, `wlan0` *and* `docker0`, and CycloneDDS picks among them arbitrarily — observed directly, as `selected arbitrarily from: eth0, docker0, wlan0`. Landing on `wlan0` or `docker0` means seeing none of the robot. So `real` needs an explicit interface override:

```xml
<NetworkInterface name="eth0" />
```

surfaced as an optional `DDS_INTERFACE` env var alongside `ROBOT_HOST`, defaulting to today's autodetermine so the Isaac Sim path is untouched.

The unicast-peer half of the config still works onboard, and doesn't need to become multicast: with `AllowMulticast=false` our reader advertises unicast locators only, and the control board's writers will unicast to it rather than multicast. `ROBOT_HOST=192.168.123.161` is the only other change.

Convenient accident: the Jetson already ships CycloneDDS **0.10.2** at `~/cyclonedds_ws/install/cyclonedds` — the exact version pinned in `pyproject.toml`, so `CYCLONEDDS_HOME` points there and no source build is needed onboard (unlike the Mac). It is a third-party build sitting in a home directory, though, so a Unitree OTA could clobber it; that is the main argument for eventually containerizing the bridge.

---

## 6. packages/shared — Zod schemas + types

### Status: not built, and not worth building yet (revisited 2026-08-07)

This section was written assuming `apps/web` would need a shared package to
get typed access to `apps/back`. It doesn't: `apps/web/src/lib/api.ts` uses
**Eden Treaty** (`treaty<App>(...)`, importing `App` straight from
`apps/back/src/index.ts`'s router type) and gets full end-to-end type
inference — request bodies, response shapes, the works — with zero
duplication and zero shared package. That's the actual mechanism in use
today; this section's premise is already solved a different way.

The rest of the original rationale doesn't hold up either: the codebase
settled on **TypeBox** (`elysia`'s `t`, see `apps/back/src/skills/define.ts`)
for parameter schemas, not the Zod sketched below — introducing Zod now
would add a second, redundant schema library rather than remove
duplication. And the one place real hand-duplication *does* exist —
`apps/bridge`'s Python skill/protocol definitions vs. `apps/back/src/skills/
*.ts` — isn't something a TS-only package fixes anyway; Python can't import
it.

**Conclusion:** don't build this. If a real shared-type need shows up later
(e.g. a second TS consumer that isn't already Eden-linked to `apps/back`),
revisit then with a concrete driver instead of the speculative one below.

### Purpose (original, superseded — kept for context)

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
export const SkillEvent = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("progress"),
    task_id: z.string(),
    phase: z.string(),
    progress: z.number(),
    data: z.record(z.unknown()).optional(),
  }),
  z.object({
    type: z.literal("result"),
    task_id: z.string(),
    status: z.enum(["ok", "error", "cancelled"]),
    data: z.unknown().optional(),
    error: z.string().optional(),
  }),
  z.object({
    type: z.literal("state"),
    pose: Pose,
    battery_pct: z.number(),
    posture: z.string(),
    faults: z.array(z.string()),
  }),
  z.object({
    type: z.literal("voice_event"),
    kind: z.enum(["wake", "partial", "final", "tts_started", "tts_ended"]),
    text: z.string().optional(),
    session_id: z.string(),
  }),
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
      "args": [
        "run",
        "--project",
        "apps/bridge",
        "python",
        "-m",
        "bridge.mcp_server",
      ],
      "env": {
        "SIM_MODE": "stub",
      },
    },
  },
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
  "inputSchema": {
    /* JSON Schema from Zod */
  },
  "outputSchema": {
    /* result shape */
  },
}
```

Long-running tools use `progressToken`; the server emits MCP `notifications/progress` events that map 1:1 to our `SkillEvent.progress`.

### Cancel

- REST: `POST /tasks/{id}/cancel` body `{ mode: "graceful" | "estop" }`
- WS (web → back): `{ type: "cancel_task", task_id, mode }`
- Bridge: cancellation token flips, skill task observes between progress emits, gracefully ramps velocity to zero (or transitions to `damp` for estop).

---

## 10. Network ports & topology

| Service           | Host   | Port  | Transport              | Auth                    |
| ----------------- | ------ | ----- | ---------------------- | ----------------------- |
| `apps/back` HTTP  | Mac    | 3000  | HTTP                   | Better Auth cookie      |
| `apps/back` WS    | Mac    | 3000  | WebSocket (`/ws/*`)    | cookie at upgrade       |
| `apps/back` MCP   | Mac    | 3000  | streamable HTTP `/mcp` | API token               |
| `apps/web` dev    | Mac    | 3001  | HTTP (Vite)            | —                       |
| `apps/bridge` WS  | Mac    | 7077  | WebSocket              | shared token (loopback) |
| `apps/bridge` MCP | Mac    | stdio | stdio                  | implicit (process)      |
| Isaac Sim DDS     | Ubuntu | 7400+ | UDP (CycloneDDS)       | none (LAN)              |

`apps/bridge` binds to **127.0.0.1** by default — never public. The bridge↔back link is loopback-only on the dev machine, with a shared header token to prevent another local process from connecting.

### 10.1 Real hardware (`SIM_MODE=real`)

| Service           | Host       | Port  | Transport              | Auth                 |
| ----------------- | ---------- | ----- | ---------------------- | -------------------- |
| `apps/back` + web | Mac/server | 3000+ | as above               | as above             |
| `apps/bridge` MCP | G1 Jetson  | stdio → HTTP | stdio-over-SSH, then streamable HTTP | SSH key, then token |
| `apps/bridge` WS  | G1 Jetson  | 7077  | WebSocket over Wi-Fi   | **shared token (enforced)** |
| G1 internal DDS   | control board | 7400+ | UDP multicast, wired LAN | none (isolated)   |

Two deltas from the sim topology, both load-bearing:

- **The bridge WS stops being loopback.** It crosses Wi-Fi, so the shared token in the row above is no longer belt-and-braces — it is the only thing standing between the LAN and a humanoid's motion API. It must be enforced, not assumed.
- **MCP transport has to grow.** `stdio` is single-client. Pointing `.mcp.json`'s `command` at `ssh c3po '… uv run …'` gets Claude Code talking to an onboard bridge with zero new code, and is the right first step. But the moment both Claude Code *and* `apps/back`'s internal agent need the bridge, stdio can't serve them — that is when the streamable-HTTP transport becomes required rather than optional.

### 10.2 Why the bridge must run onboard

The G1 is two computers. The **Jetson** (`10.4.64.27` on Wi-Fi, `192.168.123.164` on the robot's internal wired LAN) is the general-purpose host we can SSH into. The **control board** at `192.168.123.161` is what actually publishes the robot's DDS topics — `/lowstate`, `/sportmodestate`, `/api/sport`, `/api/arm`, `/state_estimator/*` — as multicast to `239.255.0.1`, at roughly 24 MB/s.

That control board has no wireless interface and no SSH. Its traffic never leaves the internal wired LAN. So a Mac on Wi-Fi cannot join the robot's DDS domain, and no configuration change on the Jetson can expose those topics — you cannot add an interface to a machine that doesn't have one.

This has one consequence worth stating plainly: **`SIM_MODE=real` is not a drop-in swap of `ROBOT_HOST`.** It is a relocation of the bridge. (Cabling a Mac onto `192.168.123.0/24` does make the Mac-hosted path work, and is fine for bench bring-up — but it tethers the robot, which defeats the purpose.)

### 10.3 What belongs onboard

The test is: **must it keep working when the operator link drops?** That set is deliberately small.

| Component            | Onboard? | Why                                                                                  |
| -------------------- | -------- | ------------------------------------------------------------------------------------ |
| `apps/bridge`        | yes      | Needs internal-LAN DDS reach; nothing else can                                        |
| link watchdog        | yes      | **New.** Ramps velocity to zero and damps if the control link goes silent             |
| `apps/back` + agent  | no       | Calls the Anthropic API — a dropped link kills the agent wherever it runs             |
| Postgres             | no       | Durable store; the robot gets hard-powered-off, and the schema is tenant-scoped       |
| `apps/web`           | no       | Operator surface; must be reachable while the robot is off charging                   |

Running `apps/back` onboard is explicitly rejected. It buys no autonomy — the internal agent depends on a remote API regardless — while dragging Postgres either onto a device that loses power abruptly, or across Wi-Fi, where DB chatter is far less latency-tolerant than the handful of MCP calls per second the agent actually makes. It would move the wrong link onto the unreliable medium.

The **link watchdog** is the one genuinely new component this topology demands. §9's cancel path already specifies a graceful ramp to zero (or `damp` for e-stop), but `estop.py` lives bridge-side of the operator connection. Onboard, a Wi-Fi drop must trigger that ramp locally rather than leaving a walking robot unsupervised.

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
    "dev": "uv run --project . python -m bridge.main",
    "build": "uv sync",
    "check-types": "uv run mypy src",
    "test": "uv run pytest",
  },
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

| Phase | Goal                                                                 | Demo                                                          | Effort |
| ----- | -------------------------------------------------------------------- | ------------------------------------------------------------- | ------ |
| 0a    | `apps/bridge` scaffold + stub MCP server registered in `.mcp.json`   | Claude Code calls `walk_to` stub, sees fake result            | 1 day  |
| 0b    | DDS peer config + Isaac Sim handshake (`get_state` real)             | `get_state` returns real pose from Isaac Sim                  | 2-3 d  |
| 1     | `walk_to` real, full ABI: task_id, progress, cancel                  | Robot in Isaac walks, progress streams, cancel works          | 1-2 wk |
| 2     | Remaining ~11 skills + safety envelopes + landmark seed              | All skills exercised via MCP and tested                       | 2-3 wk |
| 3     | `@repo/shared` + `apps/back` skill registry + agent runtime + memory | Operator types in supervisor UI, Claude decomposes & executes | 2-3 wk |
| 4     | Voice loop (wake → STT → agent → TTS) + reflex cancel                | Spoken command works end-to-end                               | 2-3 wk |
| 5     | Elysia-side MCP adapter + API tokens                                 | Claude Desktop drives the robot via the orchestrated path     | 1 wk   |
| 6     | Replay, tracing (OTel), org-tenancy gating, polish                   | Sessions replayable; multi-tenant gating verified             | 1 wk   |

**Total v1 scope:** ≈ 3–4 months solo against Isaac Sim.

### 12.1 Driver evolution: from MCP-bootstrap to Anthropic API

The chosen sequencing exploits a clean property of the design: the **skill registry is independent of who drives it.** The same Zod-typed catalogue is consumed by every driver. This means we can swap the conversation host without touching the skills, the bridge, or the protocol.

| Stage     | Conversation host                                                           | Tool runtime location          | What ships when                                  |
| --------- | --------------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------ |
| Now       | **Claude Code** (this terminal)                                             | `apps/bridge` stdio MCP server | Phase 0a–0b (bootstrap, fastest demo)            |
| Mid       | **Claude Desktop** or other MCP client                                      | `apps/back` MCP HTTP adapter   | Phase 5 (multi-client, audited, tenant-aware)    |
| Long-term | **`apps/back` Internal Agent** (Anthropic Messages API directly via AI SDK) | `apps/back` Skill Dispatcher   | Phase 3 (the "give Claude a body" product shape) |

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

- ~~Real G1 hardware (design ready; only `ROBOT_HOST` and `SIM_MODE=real` change).~~ **Superseded.** The hardware is here, and that claim was wrong: the robot's DDS is confined to its internal wired LAN, so `real` relocates the bridge onto the Jetson rather than re-pointing it. See §10.2.
- VLA-based manipulation (UnifoLM, Pi0-FAST). Tool-call seam already present.
- Semantic perception (ConceptGraphs, HOV-SG). v1 uses hand-seeded landmarks.
- Ambient/always-on agent thinking. v1 is reactive.
- Multi-robot fleet (one org = one robot).
- Eye-contact / face-tracking attention. v1 uses wake word.
- ~~On-Jetson deployment of the bridge.~~ **Promoted to plan of record** — it is the only way to reach the robot's DDS without tethering. See §10.1–10.3.
- Session replay with audio playback (text replay only).

Newly in scope as a consequence: the **link watchdog** (§10.3), enforced auth on the bridge WS now that it leaves loopback (§10.1), and a streamable-HTTP MCP transport once more than one client needs the bridge.

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
- **Transport** — the bridge's pluggable connection layer to the robot. Two implementations: DDS (Isaac Sim today) and WebRTC (real G1 over native Wi-Fi). Skills are transport-agnostic.

---

## 16. Transport abstraction

The bridge supports two connection paths to the robot, selected at startup by `SIM_MODE`:

| `SIM_MODE`     | Transport        | Target                                           |
| -------------- | ---------------- | ------------------------------------------------ |
| `stub`         | none (in-memory) | tools log + return fake data                     |
| `isaac`        | DDS (CycloneDDS) | Isaac Sim + `unitree_sim_isaaclab` on Ubuntu LAN |
| `mujoco_local` | DDS (CycloneDDS) | local `unitree_mujoco` (deferred / unused today) |
| `real`         | DDS (CycloneDDS) | a real G1, bridge running **onboard the Jetson** |

**The `real` row changed.** It was WebRTC, on the assumption that a real G1 could only be reached the way the phone app reaches it. With SSH access to the Jetson that assumption no longer holds: the bridge sits directly on the robot's internal LAN and speaks the native DDS API, which is the same transport the sim path already uses. That collapses `isaac` and `real` onto one implementation and deletes a translation layer.

### 16.1 The seam

Skills (`apps/bridge/src/bridge/skills/*`) speak through a `Transport` interface, never directly to DDS or WebRTC:

```python
class Transport(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    def subscribe(self, topic: str, msg_type: type, on_message: Callable) -> None: ...
    def publish(self, topic: str, message: Any) -> None: ...
    def request(self, topic: str, api_id: int, param: str | dict) -> None: ...
    @property
    def is_connected(self) -> bool: ...
```

Topic _names_ come from `bridge.sdk.g1_protocol.topics_for(SIM_MODE)` (already wired). The Transport implementation maps each call to its wire — a DDS publish on `isaac`, a JSON DataChannel send on `real`.

Skill implementations stay identical: `walk_to` calls `transport.publish(topics.run_command, "[vx, vy, vyaw, h]")` on sim, or transitions through `transport.request(topics.sport_request, 7101, {"data": Mode.WALK})` and then a velocity stream on real.

### 16.2 DDS implementation (`bridge/sdk/transport/dds.py` — planned refactor)

Wraps the existing `unitree_sdk2py.core.channel.ChannelFactoryInitialize`, `ChannelSubscriber`, `ChannelPublisher`. Connection setup is `init_dds` (already in `sdk/connection.py`). The current `mcp_server.py` boot sequence and `StateSampler` move under this Transport.

Dependencies on this path: `cyclonedds==0.10.2` (Python bindings, builds against the local C library at `CYCLONEDDS_HOME`), `unitree_sdk2_python` (with the `b2`-import patch).

### 16.3 WebRTC implementation (`bridge/sdk/transport/webrtc.py` — deprioritized)

> **Status: no longer on the critical path.** This design existed to reach a G1 we could only talk to the way the phone app does. We can SSH the Jetson, so `real` uses DDS (§16.2) and this transport is unnecessary for the primary path.
>
> Corroborating detail from the robot itself: `/webrtcreq` and `/webrtcres` are ordinary DDS topics on the internal LAN. The WebRTC interface was always a shim *over* the native API we now reach directly — so going native skips a translation layer, and with it the `squat=706` quirk, the `wirelesscontroller` velocity workaround, and the `con_notify data2` blocker.
>
> Retained because it is still the only route that needs no onboard install, which makes it a plausible fallback for a locked-down or OTA-reset robot. Do not build it speculatively.

For real G1 over native Wi-Fi. Mirrors the protocol reverse-engineered in legion1581/unitree_ui (MIT). One `RTCPeerConnection`:

```
PeerConnection
├── DataChannel "data"      ── DDS topics as JSON envelopes  ─┐
├── Video transceiver       ── camera (recvonly)              │ shared across
└── Audio transceiver       ── mic (in) + speaker (out)       │ skills, state,
                                                              │ voice, video
```

Handshake on connect:

1. Discover the robot's IP (UDP multicast scan or supplied `ROBOT_HOST`).
2. SDP offer/answer with **AES-128-GCM envelope** (firmware ≥ 1.5.1 — see `unitree_ui/src/api/aes-key-derive.ts`).
3. DataChannel opens; robot sends a validation challenge as `{type:"validation"}`; bridge replies with the MD5-derived key.
4. Heartbeat ping/pong (`{type:"heartbeat"}`) every N seconds.
5. Subscribe to needed topics; the robot starts emitting `{type:"msg", topic:"rt/lf/lowstate", data:...}` envelopes.

Dependencies on this path: `aiortc` (or `legion1581/unitree_webrtc_connect` as a head start — same protocol, already in Python). **No CycloneDDS needed** for a `real`-only deployment.

Wire-format helpers we already shipped:

- `bridge.sdk.g1_protocol.topics_for(SIM_MODE)` — real-G1 topic profile.
- `bridge.sdk.g1_protocol.SKILL_REQUESTS["damp" | "wave" | ...]` — pre-built `(topic_kind, api_id, param)` triples for each skill.
- `bridge.sdk.faults.decode(record)` — for the `errors` / `add_error` / `rm_error` stream that arrives over the DataChannel.

### 16.4 Cutover strategy

Revised now that the robot is here and `real` is DDS. The transport no longer changes — the *host* does — so this is a deployment exercise rather than a protocol port:

1. Add the `DDS_INTERFACE` override (§5) so `real` pins `eth0`. Smallest change that makes onboard DDS deterministic.
2. Stand the bridge up on the Jetson: `uv` + Python 3.12 (aarch64 standalone build), `CYCLONEDDS_HOME=~/cyclonedds_ws/install/cyclonedds`, the `unitree_sdk2py` `b2`-import patch. Native install, not containerized — see below.
3. Point `.mcp.json` at `ssh c3po '… uv run -m bridge.mcp_server'`. Verify `get_state` returns real `/lowstate` before anything moves.
4. Bring skills up one at a time against hardware, lowest-consequence first. `topics_for("real")` and `SKILL_REQUESTS` already exist and are unchanged by this.
5. Add the link watchdog (§10.3) **before** any untethered locomotion.
6. Enforce the bridge WS token (§10.1) — it is no longer loopback-protected.
7. Containerize once the tool surface is stable, pinning the CycloneDDS runtime rather than depending on the third-party build in `~/cyclonedds_ws`.

Ordering note on step 7: native first, container later, deliberately. Containerizing during hardware bring-up stacks two unknowns, and a DDS-in-container failure is hard to distinguish from a real-robot failure while you're debugging both. Get a known-good baseline natively — `--network host` and a source bind-mount keep the eventual move cheap.

The optional-dependency split in the old step 3 is dropped: both `isaac` and `real` need `cyclonedds` + `unitree_sdk2_python` now, so there is no lighter `real`-only install to chase.

---

## 17. Peripheral connection paths

The robot has more than locomotion — camera, mic, speakers, LiDAR. Plan per peripheral, with the **bridge as the multiplexer**: it owns every connection to the robot and re-exposes streams to the supervisor UI / agent / VLM consumers.

### 17.1 Camera (Intel RealSense D435i)

| Path            | Real G1 (≥ 1.5.1)                                                                                                                                                                                        | Isaac Sim                                                                                                                               |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Wire            | WebRTC video transceiver, recvonly. After `rtc_inner_req: disable_traffic_saving` + `vid: on`, the robot pushes the camera stream.                                                                       | Optional — Isaac Lab's RTX rendering can emit an RGB topic. Off by default; not enabled in the current `unitree_sim_isaaclab` G1 scene. |
| Bridge          | aiortc track listener stores the latest decoded frame (jpeg or raw); exposes `get_frame()` (sync, for VLM) and a relay endpoint that forwards the track to the supervisor UI.                            | If we enable a sim camera later, subscribe to the topic, decode to numpy.                                                               |
| Supervisor UI   | Separate browser↔bridge WebRTC peer connection negotiated via Elysia; bridge proxies the robot track. Renders in a normal `<video>` element. PIP-style swap with the 3D viewport (UX from `unitree_ui`). | Same shape; bridge fakes a frame source when sim camera is off.                                                                         |
| Skills using it | `look()` and `describe_scene()` (Phase 2) — both call `get_frame()` then ship the JPEG to a VLM and return the caption.                                                                                  |

### 17.2 LiDAR (Livox Mid360)

| Path                | Real G1                                                                                                                                                                                                                                                                                            | Isaac Sim                                                                            |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Wire                | **Not surfaced by Explorer** (per `unitree_ui/docs/lidar.md`). Two options: (a) publish `"ON"` to `rt/utlidar/switch` over the WebRTC DataChannel and see if firmware accepts it for G1; (b) SSH the Jetson, run a Mid360 driver that publishes the cloud on a DDS topic the bridge subscribes to. | Isaac Sim supports LiDAR sensors via Isaac Lab; not configured in current G1 scenes. |
| Format (if enabled) | `rt/utlidar/voxel_map_compressed` — LZ4-block + 128×128×Z bit-packed occupancy grid (MSB-first within byte). `resolution` and `origin` in the envelope.                                                                                                                                            | Standard `sensor_msgs/PointCloud2` (or whatever Isaac is configured to emit).        |
| Bridge              | Decode LZ4 via `lz4.block.decompress`; iterate occupancy bits to point list; emit a downsampled cloud event to subscribers.                                                                                                                                                                        | Same shape but parse `PointCloud2` directly.                                         |
| Supervisor UI       | Three.js voxel mesh, decoded client-side (port `libvoxel.wasm` directly — it's MIT) or server-side.                                                                                                                                                                                                | Same.                                                                                |
| Phase               | **v2** — out of v1 scope. The decoder + topic name are documented for when we tackle it.                                                                                                                                                                                                           |

#### 17.2.1 World-frame pose (blocks `walk_to`/`turn` on real hardware, 2026-08-07 research)

`get_state().pose` (and therefore `walk_to`/`turn`, which loop on it) is null on real G1 —
`state.py`'s only pose source is Isaac Sim's JSON `rt/sim_state`, which doesn't exist on real
firmware. `rt/utlidar/robot_pose` (in `unitree_ui/src/protocol/topics.ts`, type would be
`geometry_msgs.msg.dds_.PoseStamped_` — present in `unitree_sdk2py`'s IDL) looked like a
candidate, but:

- `unitree_ui`'s own app **skips enabling the LiDAR switch for the G1 family entirely** — the
  code comment says the Explorer webview "never toggles it on" for humanoids. It only subscribes
  to `ROBOT_ODOM`/lidar topics for non-G1 (quadruped) families.
- Real-world G1 practitioners don't use it either: [`deepglint/FAST_LIO_LOCALIZATION_HUMANOID`](https://github.com/deepglint/FAST_LIO_LOCALIZATION_HUMANOID)
  (Livox Mid360 + G1 pose estimation) runs its own **FAST-LIO** SLAM stack (ROS1,
  `livox_ros_driver2` in `CustomMsg` mode for per-point timestamps, hardware IMU/LiDAR
  extrinsic calibration) — it makes no use of `rt/utlidar/robot_pose` or `rt/utlidar/switch` at
  all, estimating pose itself from raw LiDAR + IMU via an IEKF.

**Conclusion:** `rt/utlidar/*` is very likely a quadruped-only firmware feature that isn't
mature (or possibly not implemented at all) on G1 — don't spend time toggling it blind. The
proven path is a real SLAM stack (FAST-LIO or equivalent) reading the Mid360 directly, which is
a **ROS1 dependency** foreign to this repo's DDS-native/CycloneDDS stack — it would need either
a ROS1↔DDS bridge or a small adapter that republishes FAST-LIO's pose output onto a DDS topic
`state.py` can subscribe to. This is a multi-day SLAM integration project, not a config change;
scope it separately from the rest of Phase 1. Needs the robot powered on to even start
(confirming the Mid360 is reachable, whether `rt/utlidar/switch` does anything on this unit).

### 17.3 Microphone (G1 4-mic array)

| Path            | Real G1                                                                                                                                                                                                            | Isaac Sim                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| Wire            | WebRTC audio transceiver, the incoming half. Single mixed channel (firmware does the beamforming internally; direction-of-arrival is not exposed in Explorer).                                                     | None — sim has no audio.                                                       |
| Bridge          | aiortc audio track → continuous PCM stream. Two consumers: (a) wake-word engine (`openWakeWord`) listens continuously; (b) when wake fires, the post-wake audio buffer goes to streaming STT (Deepgram WebSocket). | Stub — bridge optionally accepts an audio file as a synthetic mic for testing. |
| Skills using it | The voice loop (Phase 4) — wake → STT → agent turn. Also a future `listen_for_sound(duration_s)` reactive perception skill.                                                                                        |

### 17.4 Speakers (G1 onboard speaker)

| Path            | Real G1                                                                                                                                     | Isaac Sim                                 |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| Wire            | WebRTC audio transceiver, the outgoing half (the `sendrecv` direction). Push Opus-encoded audio frames; firmware plays them on the speaker. | None.                                     |
| Bridge          | TTS produces PCM → Opus → aiortc audio track sender. Cartesia is the v1 provider; outputs streamable Opus or PCM.                           | Falls back to local macOS `say` or no-op. |
| Skills using it | `say(text, voice)` — the existing stub becomes real here (Phase 4).                                                                         |

### 17.5 Dexterous hands (G1 Dex3-1, 7-DOF each)

| Path            | Real G1                                                                                                                                         | Isaac Sim                                                                                                                                                |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| State topic     | `rt/lf/dex3/{left,right}/state` (MotorStates\_)                                                                                                 | `rt/dex1/{left,right}/state` — note the **`dex1` not `dex3` naming** in the sim scene (the previous Inspire-Hand reset had different topic names again). |
| Command topic   | `rt/api/dex3/{left,right}/request` (api_id TBC)                                                                                                 | `rt/dex1/{left,right}/cmd` (MotorCmds\_, accepts q/dq/tau/kp/kd directly).                                                                               |
| Bridge          | Same skill shape: `set_hand_pose(side, joint_q[7])`. Pose presets (open / closed / pinch / point) stored in `g1_protocol.HAND_POSES` (planned). | Same skill, different topic + 1-DOF gripper instead of 7-DOF hand.                                                                                       |
| Skills using it | `grip(side)`, `release(side)`, `point_at` (combined with arm gesture) — Phase 2.                                                                |

### 17.6 Summary — what the bridge owns

In the WebRTC era, the bridge is the **single point of contact for everything the robot emits or accepts**:

```
                            ┌──────────────────────────────────┐
                            │       apps/bridge (Python)       │
                            │                                  │
                            │  Transport (WebRTC / DDS)        │
                            │  ├── DataChannel  → topics       │
                            │  ├── Video        → frame ring   │
                            │  ├── Audio in     → STT pipe     │
                            │  └── Audio out    → TTS pipe     │
                            │                                  │
                            │  Skill Runtime · Voice loop      │
                            │  MCP / WS server                 │
                            └──────────────────────────────────┘
                                  │             │           │
                       Topic JSON │      Video  │      Audio│
                                  ▼             ▼           ▼
                    apps/back (commands)   supervisor UI   robot speakers / mic
                                          (browser, relay
                                           via Elysia)
```

The bridge then exposes per-modality APIs to the rest of the system: typed skill calls (existing), `GET /camera/frame.jpg` (planned), `POST /tts` (planned), `WS /audio/in` (planned).
