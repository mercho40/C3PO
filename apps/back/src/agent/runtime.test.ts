/**
 * Regression test for a real bug: the system prompt used to hardcode
 * "the robot is currently the Isaac Sim emulation" and claim "walk_to,
 * turn... are real" — both false once the bridge runs against real
 * hardware (where walk_to/turn actually fail with no_pose). An agent
 * given that prompt would confidently assert things it can't back up.
 *
 * `listSkills` now fetches the catalogue live from the bridge (see
 * `../skills/catalogue.ts`), so it's mocked here with a small fixed
 * catalogue rather than needing a real bridge connection for a prompt-text
 * test.
 */
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  test,
  mock,
  spyOn,
} from "bun:test";
import type { UIMessage } from "ai";
import { MockLanguageModelV4, convertArrayToReadableStream } from "ai/test";
import type { SkillCatalogueEntry } from "#back/skills/index.js";
import * as bridgeClient from "#back/bridge/client.js";
import * as chats from "#back/db/chats.js";
import { abortAllTurns, activeTurnCount } from "./turns";

const FIXTURE_SKILLS: SkillCatalogueEntry[] = [
  {
    name: "walk_to",
    description: "Walk to a world-frame position.",
    parameters: {},
    preconditions: [],
    expectedDurationSeconds: 15,
    cancellable: true,
    typicalFailureModes: [],
    classification: "locomotion",
    dangerLevel: "medium",
    status: "real",
    works: { sim: true, real: false },
  },
  {
    name: "walk_velocity",
    description: "Open-loop body-frame velocity command.",
    parameters: {},
    preconditions: [],
    expectedDurationSeconds: 3,
    cancellable: true,
    typicalFailureModes: [],
    classification: "locomotion",
    dangerLevel: "medium",
    status: "real",
    works: { sim: false, real: true },
  },
];

FIXTURE_SKILLS.push({
  ...FIXTURE_SKILLS[0]!,
  name: "stop_everything",
  description: "Stop all motion.",
  classification: "safety",
  dangerLevel: "low",
  works: { sim: true, real: true },
});

const listSkills = mock(async () => FIXTURE_SKILLS);

mock.module("#back/skills/index.js", () => ({
  listSkills,
  // `bun test` shares one module registry across every test file in the
  // run, and `#back/skills` resolves to the same file other test files
  // import via relative (`../skills`) or self (`./index`) paths -- whichever
  // file's mock.module call "wins" serves every consumer, so a mock missing
  // an export the real module has throws at import time for whoever loses
  // the race. Keep this mock's surface complete even though runtime.ts only
  // uses listSkills.
  getSkill: mock(async (name: string) =>
    FIXTURE_SKILLS.find((s) => s.name === name),
  ),
  getCatalogue: mock(async () => ({
    skills: FIXTURE_SKILLS,
    source: "bridge",
    ageSeconds: 0,
  })),
}));

const {
  agentToolExclusions,
  boundHistory,
  buildSystemPrompt,
  offeredInMode,
  runAgentChat,
  turnSummary,
  worldSummary,
} = await import("./runtime");

describe("buildSystemPrompt", () => {
  test("does not hardcode a specific sim/real environment", async () => {
    const prompt = await buildSystemPrompt();
    expect(prompt).not.toContain("currently the Isaac Sim emulation");
    expect(prompt).not.toContain(
      "Locomotion\n(walk_to, turn) and get_state are real",
    );
  });

  test("tells the agent to check env at runtime via get_state", async () => {
    const prompt = await buildSystemPrompt();
    expect(prompt.toLowerCase()).toContain("get_state");
    expect(prompt).toContain("env");
  });

  test("voice mode is conversational and does not treat speech as a command", async () => {
    const prompt = await buildSystemPrompt("voice");
    expect(prompt).toContain("spoken conversation");
    expect(prompt).toContain("normally need no tool");
    expect(prompt).toContain("clearly asks");
    expect(prompt).toContain("exact words you want spoken");
    expect(prompt).toContain("call the matching tool in this same");
    expect(prompt).toContain("Do not invent");
    expect(prompt).toContain("garbled or incoherent");
  });

  test("the voice host exclusively owns listening and speaking", () => {
    const excluded = agentToolExclusions("voice");
    expect(excluded.has("listen")).toBe(true);
    expect(excluded.has("say")).toBe(true);
    // The operator's typed chat keeps say/listen; only the falls are withheld.
    expect([...agentToolExclusions("operator")]).toEqual([
      "zero_torque",
      "damp",
    ]);
  });

  test("the model is never handed a way to drop the robot", () => {
    for (const mode of ["operator", "voice"] as const) {
      expect(
        offeredInMode({ name: "zero_torque", dangerLevel: "high" }, mode),
      ).toBe(false);
    }
    // Not even typed: a "make it safe" after PARAR must not collapse the robot.
    for (const mode of ["operator", "voice"] as const) {
      expect(offeredInMode({ name: "damp", dangerLevel: "low" }, mode)).toBe(
        false,
      );
    }
  });

  test("overheard speech is never offered a high-danger skill", () => {
    const high = { name: "arm_navigation", dangerLevel: "high" };
    const low = { name: "wave", dangerLevel: "low" };
    expect(offeredInMode(high, "voice")).toBe(false);
    expect(offeredInMode(low, "voice")).toBe(true);
    expect(offeredInMode({ name: "say", dangerLevel: "low" }, "voice")).toBe(
      false,
    );
    // A typed operator turn keeps the full catalogue.
    expect(offeredInMode(high, "operator")).toBe(true);
  });

  test("voice is offered T2 skills: the bridge's console confirmation gates them", () => {
    const t2 = { name: "move_arm", dangerLevel: "high", tier: "T2" };
    expect(offeredInMode(t2, "voice")).toBe(true);
    expect(
      offeredInMode(
        { ...t2, name: "set_hand", dangerLevel: "medium" },
        "voice",
      ),
    ).toBe(true);
    // A high-danger T1 (arm_navigation) is gated too, but stays off voice until
    // Nav2 has driven the robot under the governor.
    expect(
      offeredInMode({ ...t2, name: "arm_navigation", tier: "T1" }, "voice"),
    ).toBe(false);
    expect(offeredInMode(t2, "operator")).toBe(true);
  });

  test("every skill is listed with its actual sim/real availability tag", async () => {
    const prompt = await buildSystemPrompt();
    // walk_to is sim-only (works.real: false) -- catalogue tag must say so,
    // not just leave it to the (removed) hardcoded claim above.
    const walkToLine = prompt
      .split("\n")
      .find((l) => l.startsWith("- walk_to "));
    expect(walkToLine).toBeDefined();
    expect(walkToLine).toContain("sim-only");

    // walk_velocity is real-only (works.real: true, works.sim: false).
    const walkVelocityLine = prompt
      .split("\n")
      .find((l) => l.startsWith("- walk_velocity "));
    expect(walkVelocityLine).toBeDefined();
    expect(walkVelocityLine).toContain("real-only");
  });

  test("tools the bridge gates are tagged: T2 and high-danger T1", async () => {
    const entry = (name: string, tier: string, dangerLevel: string) => ({
      ...FIXTURE_SKILLS[0]!,
      name,
      tier,
      dangerLevel,
    });
    listSkills.mockResolvedValueOnce([
      entry("set_hand", "T2", "medium"),
      entry("arm_navigation", "T1", "high"),
      entry("walk_to", "T1", "medium"),
    ] as SkillCatalogueEntry[]);
    const lines = (await buildSystemPrompt()).split("\n");
    const line = (name: string) =>
      lines.find((l) => l.startsWith(`- ${name} `)) ?? "";
    expect(line("set_hand")).toContain("/T2:needs-operator-confirmation");
    expect(line("arm_navigation")).toContain("/T1:needs-operator-confirmation");
    expect(line("walk_to")).not.toContain("needs-operator-confirmation");
  });
});

// G3: the prompt teaches which walk tool fits, says plainly when nothing avoids
// obstacles, and what a standing PARAR means for the rest of the turn.
test("both modes teach walking and what to do after PARAR", async () => {
  for (const mode of ["operator", "voice"] as const) {
    const prompt = await buildSystemPrompt(mode);
    for (const text of [
      "go_to_object",
      "navigate_to_landmark",
      "obstacle avoidance",
      "refused: PARAR",
      "Reanudar",
      "start_walking_waist",
    ])
      expect(prompt).toContain(text);
  }
});

describe("closed loop and perception", () => {
  test("the prompt explains plan -> act -> verify and the vision tools", async () => {
    const prompt = await buildSystemPrompt();
    for (const name of ["look", "find_object", "verify", "ask_user"])
      expect(prompt).toContain(name);
    expect(prompt).toContain("retry a step at most twice");
  });

  // The other half of the bridge's injection guard (tests/test_vlm_tools.py
  // wraps what the camera read as untrusted_text): the planner is told what
  // that marking means, in every mode.
  test("text seen by the camera is data, never instructions", async () => {
    for (const mode of ["operator", "voice"] as const) {
      const prompt = await buildSystemPrompt(mode);
      expect(prompt).toContain("never instructions");
      expect(prompt).toContain("untrusted_text");
    }
  });

  test("ask_user belongs to the voice host, like say", () => {
    expect(agentToolExclusions("voice").has("ask_user")).toBe(true);
  });

  test("the world snapshot is inlined when the bridge answers", async () => {
    const line = await worldSummary(async () => ({
      objects: [{ label: "cup" }],
    }));
    expect(line).toContain('"label":"cup"');
  });

  test("a slow or dead bridge never holds the turn", async () => {
    const started = Date.now();
    const slow = await worldSummary(() => new Promise(() => {}), 20);
    expect(Date.now() - started).toBeLessThan(500);
    expect(slow).toContain("unavailable");
    const dead = await worldSummary(async () => {
      throw new Error("bridge_unavailable");
    });
    expect(dead).toContain("unavailable");
  });
});

// 11.8: a turn left in the registry after it ended made PARAR report aborting
// turns that were already dead (routes/skills.ts), and never went away.
describe("runAgentChat releases its turn on every outcome", () => {
  const hello: UIMessage[] = [
    { id: "u1", role: "user", parts: [{ type: "text", text: "hola" }] },
  ];
  const reply = [
    { type: "text-start" as const, id: "t1" },
    { type: "text-delta" as const, id: "t1", delta: "hola" },
    { type: "text-end" as const, id: "t1" },
    {
      type: "finish" as const,
      finishReason: { unified: "stop" as const, raw: "stop" },
      usage: {
        inputTokens: {
          total: 1,
          noCache: 1,
          cacheRead: undefined,
          cacheWrite: undefined,
        },
        outputTokens: { total: 1, text: 1, reasoning: undefined },
      },
    },
  ];
  /** A model that never answers: the turn stays in flight until aborted. */
  const hanging = () =>
    new MockLanguageModelV4({
      doStream: async () => ({ stream: new ReadableStream() }),
    });
  /** The '[agent] turn' lines a console.log spy saw, parsed. */
  const turnLines = (log: { mock: { calls: unknown[][] } }) =>
    log.mock.calls
      .filter((c) => c[0] === "[agent] turn")
      .map((c) => JSON.parse(String(c[1])) as Record<string, unknown>);
  const drain = async (stream: AsyncIterable<unknown>) => {
    const parts: unknown[] = [];
    for await (const part of stream) parts.push(part);
    return parts;
  };

  // worldSummary dials the bridge: keep it off whatever .env points at, which
  // can be a tunnel to the robot.
  const savedBridgeUrl = process.env.BRIDGE_URL;
  beforeAll(() => {
    process.env.BRIDGE_URL = "http://127.0.0.1:39217/mcp";
  });
  afterAll(() => {
    if (savedBridgeUrl === undefined) delete process.env.BRIDGE_URL;
    else process.env.BRIDGE_URL = savedBridgeUrl;
  });
  // The registry is module-global and shared with every test file in the run.
  beforeEach(() => void abortAllTurns());

  test("a one-step reply is registered while it runs and released at the end", async () => {
    let answer!: () => void;
    const answered = new Promise<void>((resolve) => (answer = resolve));
    const model = new MockLanguageModelV4({
      doStream: async () => {
        await answered;
        return { stream: convertArrayToReadableStream(reply) };
      },
    });
    const log = spyOn(console, "log").mockImplementation(() => {});
    try {
      const result = await runAgentChat(hello, { model });
      expect(activeTurnCount()).toBe(1);
      answer();
      expect((await drain(result.textStream)).join("")).toBe("hola");
      await result.steps;
      expect(activeTurnCount()).toBe(0);
      // G7: exactly one turn line, the summary, with no reply text in it.
      const lines = turnLines(log);
      expect(lines).toHaveLength(1);
      expect(lines[0]).toMatchObject({ steps: 1, finishReason: "stop" });
      expect(JSON.stringify(lines[0])).not.toContain("hola");
    } finally {
      log.mockRestore();
    }
  });

  test("a first model call that throws (the gateway's 400) releases the turn", async () => {
    const model = new MockLanguageModelV4({
      doStream: async () => {
        throw new Error("Bad Request");
      },
    });
    // streamText's default onError logs the failure; expected here.
    const quiet = spyOn(console, "error").mockImplementation(() => {});
    const log = spyOn(console, "log").mockImplementation(() => {});
    try {
      const result = await runAgentChat(hello, { model });
      await drain(result.textStream);
      await result.steps.then(undefined, () => {});
      // onEnd never fires here, so the failure writes the turn's one line.
      const lines = turnLines(log);
      expect(lines).toHaveLength(1);
      expect(String(lines[0]?.error)).toContain("Bad Request");
    } finally {
      quiet.mockRestore();
      log.mockRestore();
    }
    expect(activeTurnCount()).toBe(0);
  });

  test("the browser's abort releases the turn", async () => {
    const browser = new AbortController();
    const result = await runAgentChat(hello, {
      model: hanging(),
      abortSignal: browser.signal,
    });
    expect(activeTurnCount()).toBe(1);
    browser.abort();
    await drain(result.textStream);
    await result.steps.then(undefined, () => {});
    expect(activeTurnCount()).toBe(0);
  });

  test("PARAR still aborts a turn in flight", async () => {
    const result = await runAgentChat(hello, { model: hanging() });
    expect(abortAllTurns("PARAR")).toBe(1);
    const parts = (await drain(result.fullStream)) as { type: string }[];
    expect(parts.some((p) => p.type === "abort")).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("a failure before the stream starts rejects and releases the turn", async () => {
    listSkills.mockImplementationOnce(async () => {
      throw new Error("bridge_unavailable");
    });
    const log = spyOn(console, "log").mockImplementation(() => {});
    try {
      await expect(runAgentChat(hello, { model: hanging() })).rejects.toThrow(
        "bridge_unavailable",
      );
      expect(turnLines(log)[0]?.error).toContain("bridge_unavailable");
    } finally {
      log.mockRestore();
    }
    expect(activeTurnCount()).toBe(0);
  });
});

// B2: an abort ends the reply's text at once (cancelOnAbort), but a walk_to the
// turn already sent keeps driving. The voice loop must not start its next turn
// beside it, so the turn exposes its bridge calls.
describe("runAgentChat toolsSettled", () => {
  const hello: UIMessage[] = [
    { id: "u1", role: "user", parts: [{ type: "text", text: "caminá" }] },
  ];
  const walk = [
    {
      type: "tool-call" as const,
      toolCallId: "c1",
      toolName: "walk_to",
      input: "{}",
    },
    {
      type: "finish" as const,
      finishReason: { unified: "tool-calls" as const, raw: "tool_calls" },
      usage: {
        inputTokens: {
          total: 1,
          noCache: 1,
          cacheRead: undefined,
          cacheWrite: undefined,
        },
        outputTokens: { total: 1, text: 1, reasoning: undefined },
      },
    },
  ];
  const drain = async (stream: AsyncIterable<unknown>) => {
    for await (const _ of stream);
  };
  const bridge = (walkTo: () => Promise<unknown>) => {
    const call = spyOn(bridgeClient, "callTool").mockImplementation(
      async (name) => (name === "walk_to" ? walkTo() : {}),
    );
    const log = spyOn(chats, "logToolCall").mockImplementation(async () => {});
    return () => {
      call.mockRestore();
      log.mockRestore();
    };
  };
  beforeEach(() => void abortAllTurns());

  test("stays pending after the abort until the sent walk_to answers", async () => {
    let sent!: () => void;
    const walkSent = new Promise<void>((r) => (sent = r));
    let answer!: (v: unknown) => void;
    const restore = bridge(() => {
      sent();
      return new Promise((r) => (answer = r));
    });
    try {
      const model = new MockLanguageModelV4({
        doStream: async () => ({ stream: convertArrayToReadableStream(walk) }),
      });
      const stop = new AbortController();
      const result = await runAgentChat(hello, {
        model,
        abortSignal: stop.signal,
      });
      const text = drain(result.textStream);
      await walkSent;
      stop.abort();
      await text;
      let settled = false;
      const settling = result.toolsSettled().then(() => (settled = true));
      await new Promise((r) => setTimeout(r, 20));
      expect(settled).toBe(false);
      answer({ status: "ok" });
      await settling;
      expect(settled).toBe(true);
    } finally {
      restore();
    }
  });

  const sentWith = async (toolName: string) => {
    let signal: AbortSignal | undefined;
    let sent!: () => void;
    const walkSent = new Promise<void>((r) => (sent = r));
    const call = spyOn(bridgeClient, "callTool").mockImplementation(
      async (name, _args, s) => {
        if (name !== toolName) return {};
        signal = s;
        sent();
        return { status: "ok" };
      },
    );
    const log = spyOn(chats, "logToolCall").mockImplementation(async () => {});
    try {
      const stop = new AbortController();
      const model = new MockLanguageModelV4({
        doStream: async () => ({
          stream: convertArrayToReadableStream([
            { ...walk[0]!, toolName },
            walk[1]!,
          ]),
        }),
      });
      const result = await runAgentChat(hello, {
        model,
        abortSignal: stop.signal,
      });
      const text = drain(result.textStream);
      await walkSent;
      stop.abort();
      await text;
      return signal;
    } finally {
      call.mockRestore();
      log.mockRestore();
    }
  };

  test("the turn's abort reaches the bridge call, so a walk ends with it", async () => {
    // Aborting cancels the call on the bridge, and walk_to stops on cancel.
    const signal = await sentWith("walk_to");
    expect(signal?.aborted).toBe(true);
  });

  test("a stop the model sent is never cancelled by the turn ending", async () => {
    expect(await sentWith("stop_everything")).toBeUndefined();
  });

  test("a tool the SDK starts after the abort sends the robot nothing", async () => {
    // ai 7.0.140 runs a tool call a few dozen microtasks after the model sends
    // it; abort at every point of that window.
    let aborted = false;
    let late = 0;
    const restore = bridge(async () => {
      if (aborted) late += 1;
      return { status: "ok" };
    });
    try {
      for (let ticks = 0; ticks < 120; ticks++) {
        aborted = false;
        const stop = new AbortController();
        const model = new MockLanguageModelV4({
          doStream: async () => ({
            stream: new ReadableStream({
              start(c) {
                for (const chunk of walk) c.enqueue(chunk);
                c.close();
                let n = ticks;
                const tick = (): void => {
                  if (n-- > 0) return queueMicrotask(tick);
                  aborted = true;
                  stop.abort();
                };
                tick();
              },
            }),
          }),
        });
        const result = await runAgentChat(hello, {
          model,
          abortSignal: stop.signal,
        });
        await drain(result.textStream);
        await result.toolsSettled();
        await new Promise((r) => setTimeout(r, 0));
      }
      expect(late).toBe(0);
    } finally {
      restore();
    }
  });
});

// B4: a long headset session must not send the gateway the whole chat.
describe("boundHistory", () => {
  const user = (i: number): UIMessage => ({
    id: `u${i}`,
    role: "user",
    parts: [{ type: "text", text: `m${i}` }],
  });
  const toolTurn = (i: number, tool = "walk_to"): UIMessage => ({
    id: `a${i}`,
    role: "assistant",
    parts: [
      { type: "text", text: `m${i}` },
      {
        type: `tool-${tool}`,
        toolCallId: `c${i}`,
        state: "output-available",
        input: { arg: i },
        output: { status: "ok" },
      } as UIMessage["parts"][number],
    ],
  });
  const textTurn = (i: number): UIMessage => ({
    id: `a${i}`,
    role: "assistant",
    parts: [{ type: "text", text: `m${i}` }],
  });
  const toolIds = (msgs: Awaited<ReturnType<typeof boundHistory>>) => {
    const calls: string[] = [];
    const results: string[] = [];
    for (const m of msgs)
      if (typeof m.content !== "string")
        for (const p of m.content) {
          if (p.type === "tool-call") calls.push(p.toolCallId);
          if (p.type === "tool-result") results.push(p.toolCallId);
        }
    return { calls, results };
  };

  test("100 messages give a window of 40, with tool parts only near the end", async () => {
    const history = Array.from({ length: 100 }, (_, i) =>
      i % 2 ? toolTurn(i) : user(i),
    );
    const out = await boundHistory(history);
    const text = JSON.stringify(out);
    expect(text).not.toContain('"m59"');
    expect(text).toContain('"m60"');
    const { calls, results } = toolIds(out);
    // Only the last turns' pairs survive, and never half a pair.
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.length).toBeLessThan(10);
    expect(calls).toEqual(results);
    expect(calls).toContain("c99");
    expect(out.length).toBeLessThan(60);
  });

  test("a refused T2 call exactly 10 model messages back keeps its arguments", async () => {
    // tool result at -10, its call at -11: the pair survives by id.
    const history = [user(0), toolTurn(1, "move_arm")];
    for (let i = 2; i < 10; i++) history.push(i % 2 ? textTurn(i) : user(i));
    history.push(user(10));
    const out = await boundHistory(history);
    expect(out[out.length - 10]?.role).toBe("tool");
    expect(toolIds(out)).toEqual({ calls: ["c1"], results: ["c1"] });
    expect(JSON.stringify(out)).toContain('{"arg":1}');
    // One message more and it is pruned: the window is what keeps it.
    history.push(textTurn(11));
    expect(toolIds(await boundHistory(history)).calls).toEqual([]);
  });
});

// G7: one stdout line per turn, with no text from the prompt, camera or speech.
describe("turn telemetry", () => {
  const turn = { mode: "operator" as const, chatId: "chat-1", startedAt: 0 };

  test("turnSummary counts steps, tokens and tools, and carries no text", () => {
    const event = {
      steps: [
        { toolCalls: [{ toolName: "walk_to", input: { secret: "silla" } }] },
        { toolCalls: [] },
      ],
      finishReason: "stop",
      usage: { inputTokens: 10, outputTokens: 5 },
      text: "la silla está a dos metros",
    };
    const s = turnSummary(event, turn);
    expect(s).toMatchObject({
      mode: "operator",
      chatId: "chat-1",
      steps: 2,
      finishReason: "stop",
      inputTokens: 10,
      outputTokens: 5,
      tools: ["walk_to"],
    });
    expect(typeof s.model).toBe("string");
    expect(JSON.stringify(s)).not.toContain("silla");
  });

  test("a gateway that sends no usage gives null tokens", () => {
    const s = turnSummary({ steps: [], finishReason: "stop" }, turn);
    expect(s.inputTokens).toBeNull();
    expect(s.outputTokens).toBeNull();
  });
});

/**
 * The confirmation invariant, behaviourally: whatever the model does, an agent
 * turn can only NOTE a refused gated call. It is offered no tool that confirms,
 * reaches no /safety/confirm, never re-dispatches the call, and is told not to
 * retry (the operator's Confirmar runs it, routes/skills.ts confirmPending).
 */
describe("the model cannot confirm a gated call", () => {
  const confirmations = () => import("./confirmations");
  const pickSkill: SkillCatalogueEntry = {
    ...FIXTURE_SKILLS[0],
    name: "pick",
    description: "Pick an object.",
    classification: "gesture",
    tier: "T2",
    dangerLevel: "medium",
  };
  const args = { label: "Dixie cup", shape: "power", side: "auto" };
  const usage = {
    inputTokens: {
      total: 1,
      noCache: 1,
      cacheRead: undefined,
      cacheWrite: undefined,
    },
    outputTokens: { total: 1, text: 1, reasoning: undefined },
  };
  // The model tries everything: pick, then pick again, then says it is done.
  const steps: unknown[][] = [
    [
      {
        type: "tool-call" as const,
        toolCallId: "c1",
        toolName: "pick",
        input: JSON.stringify(args),
      },
      {
        type: "finish" as const,
        finishReason: { unified: "tool-calls" as const, raw: "tool_calls" },
        usage,
      },
    ],
    [
      {
        type: "tool-call" as const,
        toolCallId: "c2",
        toolName: "pick",
        input: JSON.stringify(args),
      },
      {
        type: "finish" as const,
        finishReason: { unified: "tool-calls" as const, raw: "tool_calls" },
        usage,
      },
    ],
    [
      { type: "text-start" as const, id: "t1" },
      { type: "text-delta" as const, id: "t1", delta: "Confirmado." },
      { type: "text-end" as const, id: "t1" },
      {
        type: "finish" as const,
        finishReason: { unified: "stop" as const, raw: "stop" },
        usage,
      },
    ],
  ];
  const refusal =
    "refused: pick is a T2 tool and needs an operator's confirmation first. You cannot give it: a human operator presses «Confirmar» for these exact arguments in the console chat, then you retry once with exactly the same arguments within 30 s.";

  test("a refused pick is only noted; nothing grants or re-runs it", async () => {
    const { clearPending, listPending } = await confirmations();
    clearPending();
    listSkills.mockImplementation(async () => [...FIXTURE_SKILLS, pickSkill]);
    const realFetch = globalThis.fetch;
    const fetched: string[] = [];
    globalThis.fetch = (async (url: string) => {
      fetched.push(String(url));
      return Response.json({ granted: true });
    }) as unknown as typeof fetch;
    const dispatched: string[] = [];
    const call = spyOn(bridgeClient, "callTool").mockImplementation(
      async (name) => {
        if (name !== "pick") return {};
        dispatched.push(name);
        throw new bridgeClient.BridgeToolError("pick", refusal);
      },
    );
    const log = spyOn(chats, "logToolCall").mockImplementation(async () => {});
    const offered: string[][] = [];
    const prompts: string[] = [];
    let step = 0;
    try {
      const model = new MockLanguageModelV4({
        doStream: async (options) => {
          offered.push((options.tools ?? []).map((t) => t.name));
          prompts.push(JSON.stringify(options.prompt));
          return {
            stream: convertArrayToReadableStream(
              steps[step++] ?? steps[2],
            ) as never,
          };
        },
      });
      const result = await runAgentChat(
        [{ id: "u1", role: "user", parts: [{ type: "text", text: "agarrá" }] }],
        { model, chatId: "chat_1", userId: "u1" },
      );
      for await (const _ of result.textStream);
      await result.toolsSettled();

      // No tool the model holds can confirm.
      for (const names of offered)
        expect(names.some((n) => /confirm|grant/i.test(n))).toBe(false);
      // Nothing reached the bridge's grant, and each pick was refused, not run.
      expect(fetched.some((u) => u.includes("/safety/confirm"))).toBe(false);
      expect(dispatched).toEqual(["pick", "pick"]);
      // The refusal the model reads says the console runs it, not "retry".
      const seen = prompts[1];
      expect(seen).toContain("needs an operator's confirmation");
      expect(seen).toContain("do not call pick again");
      expect(seen).not.toContain("retry once");
      // Still waiting for a human: one card (the repeat replaced the first).
      const waiting = listPending("u1", "chat_1");
      expect(waiting.map((p) => [p.id, p.tool, p.args])).toEqual([
        ["c2", "pick", args],
      ]);
    } finally {
      globalThis.fetch = realFetch;
      call.mockRestore();
      log.mockRestore();
      listSkills.mockImplementation(async () => FIXTURE_SKILLS);
      clearPending();
    }
  });

  test("the agent runtime has no path to the grant or the confirm route", async () => {
    const src = await Bun.file(new URL("./runtime.ts", import.meta.url)).text();
    for (const forbidden of [
      "grantOnBridge",
      "confirmPending",
      "takePending",
      "/safety/confirm",
      "routes/skills",
    ])
      expect(src).not.toContain(forbidden);
  });

  test("the prompt says only refused gated tools need Confirmar, and to wait", async () => {
    const prompt = await buildSystemPrompt();
    expect(prompt).toContain("never ask the operator to");
    expect(prompt).toContain("do not call it again");
    expect(prompt).toContain("The operator's «Confirmar» runs that exact call");
    expect(prompt).not.toContain("confirm intent before");
  });
});
