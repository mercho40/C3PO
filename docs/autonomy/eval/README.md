# Eval harness (PLAN Phase 0.5)

`tasks.yaml` is the Spanish task suite: 32 tasks, 3 phrasings each (96 trials),
tiers T0/T1/T2, with an automatic success rule per task (field reference at the
top of the file). `apps/back/scripts/eval-agent.ts` runs it and scores it.

## Run it

```sh
# 1. No LLM, no bridge, no DB. Checks the suite is self-consistent and the
#    harness works end to end. Every trial should pass.
bun apps/back/scripts/eval-agent.ts --dry-run

# 2. Mock skills: the real agent and LLM against a stub bridge.
cd apps/bridge && bun run serve:http          # stub bridge on :8001 (another terminal)
bun apps/back/scripts/eval-agent.ts --tier T0  # BRIDGE_URL from apps/back/.env

# 3. Robot: same command, BRIDGE_URL pointing at the robot (tunnel), with an
#    operator setting up `start_state` and judging `success.human` per trial.
```

Flags: `--tier T0,T1`, `--ids t0-saludo,t0-que-ves`, `--voice` (the voice
tool set: no `say`/`listen`, no high-danger tools), `--out <dir>`,
`--suite <file>`. Each trial has a 180 s timeout.

Output goes to `docs/autonomy/eval/runs/<timestamp>-<mode>/`:

- `runs.jsonl`: one line per trial with `task_id`, `tier`, `phrase`,
  `success`, `failures`, `tool_calls` (name + args), `reply`, `steps` (LLM
  steps), `duration_ms`, `tools_missing` (tools in `needs` that the bridge
  does not offer yet).
- `summary.md`: success rate per tier, for "available now", and overall, with
  Wilson 95 % intervals; then one row per task.

Commit a run only when it backs a claim (a planner choice, a phase exit).

## How it calls the agent

In-process. The runner imports `runAgentChat` from
`apps/back/src/agent/runtime.ts`, the function `POST /agent` calls, so it uses
the same system prompt, tool catalogue (from `BRIDGE_URL`), step cap
(`AGENT_MAX_STEPS`) and model (`AGENT_BASE_URL`/`AGENT_MODEL`/`AGENT_API_KEY`).
Each phrasing is a fresh one-message chat.

So no login is needed. It still needs a full `apps/back/.env`: `runtime.ts`
imports the audit log, and `src/lib/env.ts` refuses to load without
`DATABASE_URL`, `WEB_URL`, `BETTER_AUTH_URL` and `BETTER_AUTH_SECRET`. Every
tool call is written to `tool_call_log` with `chat_id` NULL, `user_id` NULL and
source `agent` (or `voice`).

`POST /agent` itself is behind Better Auth (`{ auth: true }`) and only takes a
session cookie:

```sh
curl -c jar -H "Origin: $WEB_URL" -H 'content-type: application/json' \
  -d '{"email":"…","password":"…"}' http://localhost:3000/api/auth/sign-in/email
curl -b jar -H 'content-type: application/json' \
  -d '{"messages":[{"id":"m1","role":"user","parts":[{"type":"text","text":"¿Qué ves?"}]}]}' \
  http://localhost:3000/agent      # streams a UI message stream (SSE)
```

Leave out `id` so no chat row is written. The runner does not use this path.
Parsing the stream gains nothing over the in-process call, which runs the same
code.

## What the automatic rule does not see

It checks which tools were called, with what arguments, and the reply text.
It cannot see whether the robot ended within 1.0 m of the object, so robot
trials of T1 tasks need the operator's verdict (`success.human`, the Habitat
ObjectNav rule in research/03 §7). Record that verdict next to the JSONL line.
The automatic rule is the floor: a trial that fails it has failed.
