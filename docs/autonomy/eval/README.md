# Eval harness (PLAN Phase 0.5)

`tasks.yaml` is the Spanish task suite: 32 tasks, 3 phrasings each (96 trials),
tiers T0/T1/T2, with an automatic success rule per task (field reference at the
top of the file). `apps/back/scripts/eval-agent.ts` runs it and scores it.

## Run it

```sh
# 1. No LLM, no bridge, no DB. Checks the suite is self-consistent and the
#    harness works end to end. Every trial should pass.
bun apps/back/scripts/eval-agent.ts --dry-run

# 2. The T0 run (18 tasks x 3 = 54 trials, nothing moves): the real agent and
#    LLM against a stub bridge. Port 8011, not the robot tunnel's 8001, and a
#    temp C3PO_RUN_DIR so t0-frena's stop_everything latch stays out of
#    ~/.c3po/run. Another terminal:
cd apps/bridge && BRIDGE_PORT=8011 C3PO_RUN_DIR=$(mktemp -d) bun run serve:http
# then, from apps/back. --expect-env stub (in eval:t0) exits 1 before any LLM
# call unless get_state says env "stub", so a BRIDGE_URL at the robot is refused.
BRIDGE_URL=http://127.0.0.1:8011/mcp bun run eval:t0 -- --out ~/.c3po/eval/$(date +%F)

# 3. Robot: same command, BRIDGE_URL pointing at the robot (tunnel), with an
#    operator setting up `start_state` and judging `success.human` per trial.
```

Flags: `--tier T0,T1`, `--ids t0-saludo,t0-que-ves`, `--voice` (the voice
tool set: no `say`/`listen`, no high-danger tools), `--out <dir>`,
`--suite <file>`, `--expect-env <stub|isaac|real>`. Each trial has a 180 s
timeout. Outside `--dry-run` the runner also exits 1 if the bridge catalogue
has a tool above T0 that `motion_tools` does not list (`unlistedMovers` in
`scripts/eval/suite.ts`), so `forbid_motion` cannot miss a new mover.

When: run `eval:t0` on demand before each robot session (the TIC AI gateway
answers only on campus, so CI and cloud routines cannot reach it). On a
machine that stays on the campus network, a nightly crontab line would be:

```sh
0 3 * * * cd ~/c3po/apps/back && BRIDGE_URL=http://127.0.0.1:8011/mcp bun run eval:t0 -- --out ~/.c3po/eval/$(date +\%F)
```

with the 8011 stub kept running. Every trial still writes to the
`tool_call_log` of `apps/back/.env`'s `DATABASE_URL`; point it at a separate DB
to keep stub calls out of the operator audit log.

Output goes to `docs/autonomy/eval/runs/<timestamp>-<mode>/`:

- `runs.jsonl`: one line per trial with `task_id`, `tier`, `phrase`, `model`
  (`AGENT_MODEL`, `dry-run` for the oracle), `success`, `failures`, `tool_calls` (name + args), `reply`, `steps` (LLM
  steps), `duration_ms`, `tools_missing` (tools in `needs` that the bridge
  does not offer yet).
- `summary.md`: success rate per tier, for "available now", and overall, with
  Wilson 95 % intervals; then one row per task.

Commit a run only when it backs a claim (a planner choice, a phase exit).

Runs from before 2026-11-04 are not comparable with later ones: that change
added `go_to_object`, `navigate_to_landmark` and `release_arm_control` to
`motion_tools` and made the landmark and object tasks score
`navigate_to_landmark` / `go_to_object` (no `recall_*` + `walk_to` detour), so
the same model now scores lower on T0 forbids and differently on T1.

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
