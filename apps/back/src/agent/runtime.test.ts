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
  buildSystemPrompt,
  offeredInMode,
  runAgentChat,
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
        offeredInMode({ name: "zero_torque", dangerLevel: "low" }, mode),
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
    // A high-danger T1 (arm_navigation) has no such gate and stays off voice.
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
    const result = await runAgentChat(hello, { model });
    expect(activeTurnCount()).toBe(1);
    answer();
    expect((await drain(result.textStream)).join("")).toBe("hola");
    await result.steps;
    expect(activeTurnCount()).toBe(0);
  });

  test("a first model call that throws (the gateway's 400) releases the turn", async () => {
    const model = new MockLanguageModelV4({
      doStream: async () => {
        throw new Error("Bad Request");
      },
    });
    // streamText's default onError logs the failure; expected here.
    const quiet = spyOn(console, "error").mockImplementation(() => {});
    try {
      const result = await runAgentChat(hello, { model });
      await drain(result.textStream);
      await result.steps.then(undefined, () => {});
    } finally {
      quiet.mockRestore();
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
    await expect(runAgentChat(hello, { model: hanging() })).rejects.toThrow(
      "bridge_unavailable",
    );
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
