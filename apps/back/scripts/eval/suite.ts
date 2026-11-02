/**
 * The Spanish task suite (docs/autonomy/eval/tasks.yaml): loading, checking,
 * scoring one trial, and the summary with Wilson 95 % intervals.
 *
 * Pure: no LLM, no bridge, no DB. `eval-agent.ts` runs the trials.
 */

export type Tier = "T0" | "T1" | "T2";

export type SuccessRule = {
  calls_all?: string[];
  calls_any?: string[];
  forbid?: string[];
  forbid_motion?: boolean;
  args_contain?: Record<string, string[]>;
  reply_contains?: string[];
  human?: string;
};

export type Task = {
  id: string;
  tier: Tier;
  phase: string;
  available?: boolean;
  phrase: string;
  alt_phrasings?: string[];
  start_state: string;
  needs: string[];
  success: SuccessRule;
};

export type Suite = { version: number; motion_tools: string[]; tasks: Task[] };

export type ToolCall = { name: string; args: unknown };

/** What one trial produced — the scorer's only input. */
export type Trace = { calls: ToolCall[]; reply: string; error?: string };

export type TrialResult = {
  task_id: string;
  tier: Tier;
  phase: string;
  available: boolean;
  phrasing_index: number;
  phrase: string;
  mode: string;
  success: boolean;
  failures: string[];
  tool_calls: ToolCall[];
  reply: string;
  steps: number;
  duration_ms: number;
  tools_missing: string[];
  error?: string;
};

export async function loadSuite(path: string): Promise<Suite> {
  return Bun.YAML.parse(await Bun.file(path).text()) as Suite;
}

/** Case- and accent-insensitive, so "Billetera" matches "billetera". */
export function fold(text: string): string {
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

/**
 * Internal consistency: every rule must be satisfiable by the task's own
 * `needs`, which is also what the dry-run plays. Returns problems, [] if none.
 */
export function validateSuite(suite: Suite): string[] {
  const errors: string[] = [];
  const motion = new Set(suite.motion_tools ?? []);
  const seen = new Set<string>();
  for (const t of suite.tasks ?? []) {
    const where = `task ${t.id ?? "?"}`;
    if (!t.id || seen.has(t.id))
      errors.push(`${where}: missing or duplicate id`);
    seen.add(t.id);
    if (!["T0", "T1", "T2"].includes(t.tier)) errors.push(`${where}: bad tier`);
    if (!t.phrase?.trim()) errors.push(`${where}: no phrase`);
    if (!t.start_state?.trim()) errors.push(`${where}: no start_state`);
    if (t.phase === undefined || t.phase === "")
      errors.push(`${where}: no phase`);
    const needs = new Set(t.needs ?? []);
    const s = t.success ?? {};
    for (const tool of s.calls_all ?? [])
      if (!needs.has(tool))
        errors.push(`${where}: calls_all ${tool} not in needs`);
    if (s.calls_any && !s.calls_any.some((tool) => needs.has(tool)))
      errors.push(`${where}: no calls_any tool is in needs`);
    for (const tool of Object.keys(s.args_contain ?? {}))
      if (!needs.has(tool))
        errors.push(`${where}: args_contain ${tool} not in needs`);
    for (const tool of needs) {
      if (s.forbid?.includes(tool))
        errors.push(`${where}: needs a forbidden ${tool}`);
      if (s.forbid_motion && motion.has(tool))
        errors.push(`${where}: forbid_motion but needs ${tool}`);
    }
    if (t.tier === "T0" && [...needs].some((tool) => motion.has(tool)))
      errors.push(`${where}: T0 must not need a motion tool`);
  }
  return errors;
}

/** Every (task, phrasing) pair, optionally limited to some tiers or ids. */
export function trialsOf(
  suite: Suite,
  filter: { tiers?: string[]; ids?: string[] } = {},
): { task: Task; index: number; phrase: string }[] {
  return suite.tasks
    .filter((t) => !filter.tiers?.length || filter.tiers.includes(t.tier))
    .filter((t) => !filter.ids?.length || filter.ids.includes(t.id))
    .flatMap((task) =>
      [task.phrase, ...(task.alt_phrasings ?? [])].map((phrase, index) => ({
        task,
        index,
        phrase,
      })),
    );
}

/** Check one trace against a task's rule. Returns the reasons it failed. */
export function score(
  task: Task,
  trace: Trace,
  motionTools: string[],
): string[] {
  const failures: string[] = [];
  const s = task.success ?? {};
  const called = new Set(trace.calls.map((c) => c.name));
  if (trace.error) failures.push(`error: ${trace.error}`);
  for (const tool of s.calls_all ?? [])
    if (!called.has(tool)) failures.push(`did not call ${tool}`);
  if (s.calls_any && !s.calls_any.some((tool) => called.has(tool)))
    failures.push(`called none of ${s.calls_any.join("/")}`);
  const forbidden = new Set([
    ...(s.forbid ?? []),
    ...(s.forbid_motion ? motionTools : []),
  ]);
  for (const tool of called)
    if (forbidden.has(tool)) failures.push(`called forbidden ${tool}`);
  for (const [tool, texts] of Object.entries(s.args_contain ?? {})) {
    const ok = trace.calls.some(
      (c) =>
        c.name === tool &&
        texts.some((text) =>
          fold(JSON.stringify(c.args ?? {})).includes(fold(text)),
        ),
    );
    if (!ok) failures.push(`${tool} args lack any of ${texts.join("/")}`);
  }
  const said = trace.calls
    .filter((c) => c.name === "say")
    .map((c) => JSON.stringify(c.args ?? {}))
    .join(" ");
  const reply = `${trace.reply} ${said}`;
  if (!reply.trim()) failures.push("no reply");
  if (
    s.reply_contains &&
    !s.reply_contains.some((text) => fold(reply).includes(fold(text)))
  )
    failures.push(`reply lacks any of ${s.reply_contains.join("/")}`);
  return failures;
}

/**
 * What a perfect planner would do according to the task itself: call `needs`
 * (with the first `args_contain` text as an argument) and reply with the first
 * `reply_contains`. Drives --dry-run, which proves the harness end to end.
 */
export function oracleTrace(task: Task): Trace {
  const s = task.success ?? {};
  return {
    calls: (task.needs ?? []).map((name) => {
      const text = s.args_contain?.[name]?.[0];
      return { name, args: text === undefined ? {} : { value: text } };
    }),
    reply: s.reply_contains?.[0] ?? "(dry-run)",
  };
}

/** Wilson score interval for k successes in n trials (z = 1.96 → 95 %). */
export function wilson(
  k: number,
  n: number,
  z = 1.96,
): { low: number; high: number } {
  if (n === 0) return { low: 0, high: 1 };
  const p = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = (p + z2 / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom;
  return { low: Math.max(0, center - half), high: Math.min(1, center + half) };
}

const pct = (x: number) => `${(100 * x).toFixed(0)} %`;

function row(label: string, rs: TrialResult[]): string {
  const k = rs.filter((r) => r.success).length;
  const { low, high } = wilson(k, rs.length);
  const rate = rs.length ? pct(k / rs.length) : "-";
  const steps = rs.length
    ? (rs.reduce((a, r) => a + r.steps, 0) / rs.length).toFixed(1)
    : "-";
  const secs = rs.length
    ? (rs.reduce((a, r) => a + r.duration_ms, 0) / rs.length / 1000).toFixed(1)
    : "-";
  return `| ${label} | ${k}/${rs.length} | ${rate} | ${pct(low)}–${pct(high)} | ${steps} | ${secs} |`;
}

/** Markdown summary: per tier, overall, then each task. */
export function summarize(results: TrialResult[], header: string): string {
  const lines = [
    `# Eval run — ${header}`,
    "",
    "| Group | Success | SR | Wilson 95 % | Mean LLM steps | Mean s |",
    "| --- | --- | --- | --- | --- | --- |",
  ];
  for (const tier of ["T0", "T1", "T2"])
    lines.push(
      row(
        tier,
        results.filter((r) => r.tier === tier),
      ),
    );
  lines.push(
    row(
      "available now",
      results.filter((r) => r.available),
    ),
  );
  lines.push(row("**all**", results));
  lines.push(
    "",
    "| Task | Tier | Success | Failures (first) | Tools missing |",
    "| --- | --- | --- | --- | --- |",
  );
  const ids = [...new Set(results.map((r) => r.task_id))];
  for (const id of ids) {
    const rs = results.filter((r) => r.task_id === id);
    const k = rs.filter((r) => r.success).length;
    const firstFail = rs.find((r) => !r.success)?.failures[0] ?? "";
    const missing = [...new Set(rs.flatMap((r) => r.tools_missing))].join(", ");
    lines.push(
      `| ${id} | ${rs[0]!.tier} | ${k}/${rs.length} | ${firstFail} | ${missing} |`,
    );
  }
  return `${lines.join("\n")}\n`;
}
