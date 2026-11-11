/**
 * Run the Spanish task suite (docs/autonomy/eval/tasks.yaml) and score it.
 *
 *   bun apps/back/scripts/eval-agent.ts --dry-run            # nothing needed
 *   bun apps/back/scripts/eval-agent.ts --tier T0            # real LLM + BRIDGE_URL
 *   bun apps/back/scripts/eval-agent.ts --tier T0 --voice    # the voice tool set
 *   ... --ids t0-saludo,t0-que-ves   --out <dir>   --suite <tasks.yaml>
 *   ... --expect-env stub     # exit 1 before any LLM call unless get_state's env matches
 *
 * --dry-run plays each task's own `needs` (scripts/eval/suite.ts oracleTrace):
 * no LLM, no bridge, no DB. It proves the suite is self-consistent and the
 * scoring, JSONL and summary work — every trial should pass.
 *
 * Otherwise each phrasing is one fresh, single-turn chat through the real
 * `runAgentChat` — the same prompt, tools and step cap as POST /agent — run
 * in-process, so no Better Auth session is needed. Tools go to BRIDGE_URL:
 * a stub bridge (`bun run serve:http` in apps/bridge) for "mock skills", the
 * robot for real trials. Needs apps/back/.env (AGENT_*, and DATABASE_URL etc.,
 * because runtime.ts imports the audit log: every call lands in tool_call_log
 * with chat_id NULL).
 *
 * Writes <out>/runs.jsonl (one TrialResult per line) and <out>/summary.md.
 */

import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";

import {
  envMismatch,
  loadSuite,
  oracleTrace,
  score,
  summarize,
  trialsOf,
  unlistedMovers,
  validateSuite,
  type Task,
  type ToolCall,
  type Trace,
  type TrialResult,
} from "./eval/suite";

const REPO = resolve(import.meta.dir, "../../..");
// One trial must not hang the suite: an off-campus TIC AI gateway never answers.
const TRIAL_TIMEOUT_MS = 180_000;

const { values: opts } = parseArgs({
  options: {
    "dry-run": { type: "boolean", default: false },
    voice: { type: "boolean", default: false },
    tier: { type: "string" },
    ids: { type: "string" },
    suite: {
      type: "string",
      default: join(REPO, "docs/autonomy/eval/tasks.yaml"),
    },
    out: { type: "string" },
    "expect-env": { type: "string" },
  },
});

const dryRun = opts["dry-run"]!;
const mode = dryRun ? "dry-run" : opts.voice ? "voice" : "operator";
const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const outDir =
  opts.out ?? join(REPO, "docs/autonomy/eval/runs", `${stamp}-${mode}`);

const suite = await loadSuite(opts.suite!);
const problems = validateSuite(suite);
if (problems.length) {
  console.error(`✗ ${opts.suite} is inconsistent:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
const trials = trialsOf(suite, {
  tiers: opts.tier?.split(",").map((s) => s.trim()),
  ids: opts.ids?.split(",").map((s) => s.trim()),
});
if (!trials.length) {
  console.error("✗ no trials match --tier/--ids");
  process.exit(1);
}

// Same .env loading as smoke-agent.ts: Bun only reads .env from the cwd, and
// it must be in process.env BEFORE runtime.ts (and env.ts) are imported.
if (!dryRun) {
  const envFile = Bun.file(`${import.meta.dir}/../.env`);
  if (await envFile.exists()) {
    for (const line of (await envFile.text()).split("\n")) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (m && !process.env[m[1]!]) process.env[m[1]!] = m[2]!;
    }
  }
}

type Runner = (
  task: Task,
  phrase: string,
) => Promise<Trace & { steps: number }>;
let run: Runner;
let catalogue: Set<string> | null = null;
let model = "dry-run";

if (dryRun) {
  run = async (task) => {
    const trace = oracleTrace(task);
    return { ...trace, steps: trace.calls.length + 1 };
  };
} else {
  const { runAgentChat } = await import("../src/agent/runtime");
  const { listSkills } = await import("../src/skills/index");
  const { callTool } = await import("../src/bridge/client");
  if (opts["expect-env"]) {
    // Before the first trial, so a wrong BRIDGE_URL never reaches the robot.
    const wrong = envMismatch(opts["expect-env"], await callTool("get_state"));
    if (wrong) {
      console.error(`✗ ${wrong} (BRIDGE_URL=${process.env.BRIDGE_URL ?? ""})`);
      process.exit(1);
    }
  }
  const skills = await listSkills();
  const movers = unlistedMovers(suite.motion_tools, skills);
  if (movers.length) {
    console.error(
      `✗ ${opts.suite} motion_tools misses tools that move:\n  ${movers.join("\n  ")}`,
    );
    process.exit(1);
  }
  catalogue = new Set(skills.map((s) => s.name));
  model = process.env.AGENT_MODEL ?? "tic-chat";
  run = async (_task, phrase) => {
    let error: string | undefined;
    const result = await runAgentChat(
      [
        {
          id: crypto.randomUUID(),
          role: "user",
          parts: [{ type: "text", text: phrase }],
        },
      ],
      {
        mode: opts.voice ? "voice" : "operator",
        userId: null,
        abortSignal: AbortSignal.timeout(TRIAL_TIMEOUT_MS),
      },
    );
    for await (const part of result.fullStream)
      if (part.type === "error") error ??= String(part.error);
    // A failed stream rejects `steps` with a generic "No output generated";
    // the cause is the first error part the stream carried.
    let steps: Awaited<typeof result.steps>;
    try {
      steps = await result.steps;
    } catch (err) {
      throw new Error(error ?? String(err));
    }
    const calls: ToolCall[] = steps.flatMap((s) =>
      s.toolCalls.map((c) => ({ name: c.toolName, args: c.input })),
    );
    return { calls, reply: await result.text, steps: steps.length, error };
  };
}

await mkdir(outDir, { recursive: true });
const jsonl = Bun.file(join(outDir, "runs.jsonl")).writer();
const results: TrialResult[] = [];

console.log(`${mode}: ${trials.length} trials → ${outDir}`);
for (const { task, index, phrase } of trials) {
  const started = performance.now();
  let trace: Trace & { steps: number };
  try {
    trace = await run(task, phrase);
  } catch (err) {
    trace = {
      calls: [],
      reply: "",
      steps: 0,
      error: err instanceof Error ? err.message : String(err),
    };
  }
  const failures = score(task, trace, suite.motion_tools);
  const result: TrialResult = {
    task_id: task.id,
    tier: task.tier,
    phase: String(task.phase),
    available: task.available !== false,
    phrasing_index: index,
    phrase,
    mode,
    model,
    success: failures.length === 0,
    failures,
    tool_calls: trace.calls,
    reply: trace.reply,
    steps: trace.steps,
    duration_ms: Math.round(performance.now() - started),
    tools_missing: catalogue
      ? task.needs.filter((t) => !catalogue!.has(t))
      : [],
    ...(trace.error ? { error: trace.error } : {}),
  };
  results.push(result);
  jsonl.write(`${JSON.stringify(result)}\n`);
  console.log(
    `${result.success ? "✓" : "✗"} ${task.id}#${index} ${(result.duration_ms / 1000).toFixed(1)}s ` +
      `[${trace.calls.map((c) => c.name).join(", ")}]${failures.length ? `  — ${failures[0]}` : ""}`,
  );
}
await jsonl.end();

const summary = summarize(results, `${mode}, ${model}, ${stamp}`);
await Bun.write(join(outDir, "summary.md"), summary);
console.log(`\n${summary}`);
process.exit(0);
