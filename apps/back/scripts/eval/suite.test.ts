import { describe, expect, test } from "bun:test";
import { join, resolve } from "node:path";

import {
  envMismatch,
  fold,
  loadSuite,
  oracleTrace,
  score,
  summarize,
  trialsOf,
  unlistedMovers,
  validateSuite,
  wilson,
  type Task,
  type TrialResult,
} from "./suite";

const SUITE = join(
  resolve(import.meta.dir, "../../../.."),
  "docs/autonomy/eval/tasks.yaml",
);
const MOTION = ["walk_to", "turn"];

const task = (over: Partial<Task> = {}): Task => ({
  id: "t",
  tier: "T0",
  phase: "1",
  phrase: "¿Dónde está la billetera?",
  start_state: "x",
  needs: ["recall_object"],
  success: {},
  ...over,
});

describe("wilson", () => {
  test("known values", () => {
    const half = wilson(10, 20);
    expect(half.low).toBeCloseTo(0.2993, 3);
    expect(half.high).toBeCloseTo(0.7007, 3);
    const all = wilson(20, 20);
    expect(all.low).toBeCloseTo(0.8389, 3);
    expect(all.high).toBe(1);
    const none = wilson(0, 10);
    expect(none.low).toBe(0);
    expect(none.high).toBeCloseTo(0.2775, 3);
  });

  test("no trials says nothing", () => {
    expect(wilson(0, 0)).toEqual({ low: 0, high: 1 });
  });
});

describe("score", () => {
  test("calls, forbids, args and reply", () => {
    const t = task({
      success: {
        calls_all: ["recall_object"],
        forbid_motion: true,
        args_contain: { recall_object: ["billetera", "wallet"] },
      },
    });
    const good = {
      calls: [{ name: "recall_object", args: { query: "Billetera" } }],
      reply: "En la mesa.",
    };
    expect(score(t, good, MOTION)).toEqual([]);

    const moved = {
      ...good,
      calls: [...good.calls, { name: "turn", args: {} }],
    };
    expect(score(t, moved, MOTION)).toEqual(["called forbidden turn"]);

    const wrongArg = {
      calls: [{ name: "recall_object", args: { query: "llaves" } }],
      reply: "no",
    };
    expect(score(t, wrongArg, MOTION)[0]).toContain("args lack");

    expect(score(t, { calls: [], reply: "" }, MOTION)).toContain("no reply");
  });

  test("calls_any and reply_contains are accent-insensitive", () => {
    const t = task({
      success: {
        calls_any: ["look", "describe_surroundings"],
        reply_contains: ["?"],
      },
    });
    expect(
      score(
        t,
        { calls: [{ name: "look", args: {} }], reply: "¿Cuál?" },
        MOTION,
      ),
    ).toEqual([]);
    expect(score(t, { calls: [], reply: "listo" }, MOTION)).toHaveLength(2);
    expect(fold("Televisión")).toBe("television");
  });

  test("a say call counts as a reply, an error always fails", () => {
    const t = task();
    expect(
      score(
        t,
        { calls: [{ name: "say", args: { text: "hola" } }], reply: "" },
        MOTION,
      ),
    ).toEqual([]);
    expect(
      score(t, { calls: [], reply: "x", error: "Bad Gateway" }, MOTION),
    ).toEqual(["error: Bad Gateway"]);
  });
});

describe("the real suite", async () => {
  const suite = await loadSuite(SUITE);

  test("is self-consistent and about 30 tasks across all tiers", () => {
    expect(validateSuite(suite)).toEqual([]);
    expect(suite.tasks.length).toBeGreaterThanOrEqual(28);
    for (const tier of ["T0", "T1", "T2"])
      expect(suite.tasks.some((t) => t.tier === tier)).toBe(true);
  });

  test("the dry-run oracle passes every trial", () => {
    for (const { task: t } of trialsOf(suite))
      expect(score(t, oracleTrace(t), suite.motion_tools)).toEqual([]);
  });

  test("filters by tier and id; every phrasing is a trial", () => {
    const t0 = trialsOf(suite, { tiers: ["T0"] });
    expect(t0.every((x) => x.task.tier === "T0")).toBe(true);
    const one = trialsOf(suite, { ids: ["t0-saludo"] });
    expect(one.map((x) => x.index)).toEqual([0, 1, 2]);
  });
});

test("validateSuite catches an unsatisfiable rule", () => {
  const bad = {
    version: 1,
    motion_tools: MOTION,
    tasks: [
      task({
        needs: ["turn"],
        success: { calls_all: ["x"], forbid_motion: true },
      }),
    ],
  };
  const errors = validateSuite(bad);
  expect(errors.some((e) => e.includes("calls_all x"))).toBe(true);
  expect(errors.some((e) => e.includes("forbid_motion but needs turn"))).toBe(
    true,
  );
  expect(errors.some((e) => e.includes("T0 must not need"))).toBe(true);
});

test("unlistedMovers flags a mover missing from motion_tools", () => {
  const catalogue = [
    { name: "walk_to", tier: "T1" },
    { name: "go_to_object", tier: "T1" },
    { name: "pick", tier: "T2" },
    { name: "untiered" },
    { name: "get_state", tier: "T0" },
    { name: "cancel_navigation", tier: "T1" },
    { name: "disarm_navigation", tier: "T1" },
  ];
  expect(unlistedMovers(["walk_to", "pick"], catalogue)).toEqual([
    "go_to_object",
    "untiered",
  ]);
});

test("envMismatch only passes the expected env", () => {
  expect(envMismatch("stub", { env: "stub", stub: true })).toBeNull();
  expect(envMismatch("stub", { env: "real" })).toContain('"real"');
  expect(envMismatch("stub", {})).toContain("no env");
  expect(envMismatch("stub", null)).toContain("no env");
});

test("summarize reports per-tier rates with intervals", () => {
  const r = (ok: boolean, tier: "T0" | "T1" = "T0"): TrialResult => ({
    task_id: `t-${tier}`,
    tier,
    phase: "1",
    available: true,
    phrasing_index: 0,
    phrase: "x",
    mode: "dry-run",
    model: "dry-run",
    success: ok,
    failures: ok ? [] : ["nope"],
    tool_calls: [],
    reply: "",
    steps: 2,
    duration_ms: 1000,
    tools_missing: [],
  });
  const md = summarize([r(true), r(false), r(true, "T1")], "test");
  expect(md).toContain("| T0 | 1/2 | 50 % | 9 %–91 % | 2.0 | 1.0 |");
  expect(md).toContain("| t-T0 | T0 | 1/2 | nope |  |");
});
