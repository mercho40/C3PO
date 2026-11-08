/**
 * 1.4: /agent, /voice/start and /voice/transcribe are admin-only; /voice/stop
 * stays open to any session (stopping is the safe direction, like PARAR).
 * Auth and the DB are mocked as in safety.test.ts; every network call goes
 * through a stubbed global fetch so a refusal can be shown to reach nothing.
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  mock,
  spyOn,
  test,
} from "bun:test";

let sessionUser: { id: string; role: string | null } | null = null;
mock.module("#back/lib/auth.js", () => ({
  auth: {
    handler: async () => new Response("not mocked", { status: 404 }),
    api: {
      getSession: async () =>
        sessionUser ? { user: sessionUser, session: { id: "sess_1" } } : null,
    },
  },
}));
function thenable(result: unknown): unknown {
  const self: Record<string, unknown> = {
    then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) =>
      Promise.resolve(result).then(resolve, reject),
  };
  return new Proxy(self, {
    get: (obj, prop) => (prop === "then" ? obj.then : () => thenable(result)),
  });
}
mock.module("#back/db/drizzle.js", () => ({
  db: {
    insert: () => ({ values: () => thenable([]) }),
    select: () => thenable([]),
    update: () => thenable([]),
    delete: () => thenable([]),
    query: { chat: { findFirst: async () => undefined } },
  },
}));

const { agentRoutes } = await import("./agent");
const { voiceRoutes } = await import("./voice");
const realFetch = globalThis.fetch;
let fetched: string[] = [];

function post(
  app: { handle: (r: Request) => Promise<Response> },
  path: string,
  init: RequestInit = {},
) {
  return app.handle(
    new Request(`http://localhost${path}`, { method: "POST", ...init }),
  );
}

const agentBody = {
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    messages: [
      { id: "m1", role: "user", parts: [{ type: "text", text: "camina" }] },
    ],
  }),
};

describe("admin role on /agent and /voice", () => {
  beforeEach(() => {
    fetched = [];
    sessionUser = { id: "u2", role: "user" };
    globalThis.fetch = (async (url: string) => {
      fetched.push(String(url));
      return Response.json({});
    }) as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  for (const [name, app, path, init] of [
    ["/agent", agentRoutes, "/agent", agentBody],
    ["/voice/start", voiceRoutes, "/voice/start", {}],
    [
      "/voice/transcribe",
      voiceRoutes,
      "/voice/transcribe",
      { body: new Uint8Array(8) },
    ],
  ] as const) {
    test(`a non-admin gets 403 on ${name} and nothing is reached`, async () => {
      const res = await post(app, path, init);
      expect(res.status).toBe(403);
      expect(((await res.json()) as { error: string }).error).toBe(
        "admin_required",
      );
      expect(fetched).toEqual([]);
    });
  }

  test("a non-admin can still stop the voice conversation", async () => {
    const res = await post(voiceRoutes, "/voice/stop");
    expect(res.status).toBe(200);
  });

  test("an admin passes the gate: an oversize upload is a 413, with no network call", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await post(voiceRoutes, "/voice/transcribe", {
      headers: { "content-length": String(50 * 1024 * 1024) },
      body: new Uint8Array(8),
    });
    expect(res.status).toBe(413);
    expect(fetched).toEqual([]);
  });
});

// B8. Imported after the mocks above, like the routes: a static import would
// load the real database client first.
const runtime = await import("#back/agent/runtime.js");
const audited = await import("#back/bridge/audited.js");
const { abortAllTurns, activeTurnCount } = await import("#back/agent/turns.js");
const { streamText } = await import("ai");
const { MockLanguageModelV4 } = await import("ai/test");

describe("the robot says a headset turn's reply (B8)", () => {
  const said: { text: unknown; userId: unknown; source: unknown }[] = [];
  let gate: Promise<void> = Promise.resolve();
  let spies: { mockRestore: () => void }[] = [];
  const savedFlag = process.env.HEADSET_SPEAK_REPLY;

  const replyParts = (text: string) => [
    { type: "text-start" as const, id: "t1" },
    { type: "text-delta" as const, id: "t1", delta: text },
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

  beforeEach(() => {
    abortAllTurns();
    said.length = 0;
    gate = Promise.resolve();
    sessionUser = { id: "u1", role: "admin" };
    process.env.HEADSET_SPEAK_REPLY = "1";
    spies = [
      // The model says its first words at once and finishes after `gate`, so
      // an abort in between leaves a partial reply that must not be said.
      spyOn(runtime, "runAgentChat").mockImplementation((async (
        _messages: unknown,
        opts: { abortSignal?: AbortSignal },
      ) =>
        streamText({
          model: new MockLanguageModelV4({
            doStream: async () => {
              const [first, ...rest] = replyParts("Hola, soy C3PO.");
              return {
                stream: new ReadableStream({
                  async start(controller) {
                    controller.enqueue(first);
                    controller.enqueue(rest.shift());
                    await gate;
                    for (const part of rest) controller.enqueue(part);
                    controller.close();
                  },
                }),
              };
            },
          }),
          prompt: "hola",
          abortSignal: opts.abortSignal,
        })) as never),
      spyOn(audited, "auditedCall").mockImplementation((async (
        name: string,
        args: Record<string, unknown>,
        who: { userId: unknown; source: unknown },
      ) => {
        if (name === "say")
          said.push({
            text: args.text,
            userId: who.userId,
            source: who.source,
          });
        return {};
      }) as never),
      spyOn(console, "warn").mockImplementation(() => {}),
    ];
  });
  afterEach(() => {
    for (const spy of spies) spy.mockRestore();
    abortAllTurns();
    if (savedFlag === undefined) delete process.env.HEADSET_SPEAK_REPLY;
    else process.env.HEADSET_SPEAK_REPLY = savedFlag;
  });

  const voiceTurn = (mode = "voice", signal?: AbortSignal) =>
    agentRoutes.handle(
      new Request("http://localhost/agent", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          mode,
          messages: [
            { id: "m1", role: "user", parts: [{ type: "text", text: "hola" }] },
          ],
        }),
        signal,
      }),
    );
  /** Speech is not awaited by the route; wait for its turn to be released. */
  const settled = async () => {
    for (let i = 0; i < 100 && activeTurnCount() > 0; i += 1)
      await new Promise((r) => setTimeout(r, 2));
    expect(activeTurnCount()).toBe(0);
  };

  test("a completed voice turn is said through say, attributed to the operator", async () => {
    const res = await voiceTurn();
    expect(res.status).toBe(200);
    await res.text();
    await settled();
    expect(said).toEqual([
      { text: "Hola, soy C3PO.", userId: "u1", source: "voice" },
    ]);
  });

  test("PARAR before the reply ends: nothing is said, even with the race to the first say", async () => {
    let release!: () => void;
    gate = new Promise((r) => (release = r));
    const res = await voiceTurn();
    // The reply's turn is registered while the model is still thinking...
    expect(activeTurnCount()).toBe(1);
    abortAllTurns("PARAR");
    // ...so a model that still completes normally is not spoken.
    release();
    await res.text();
    await settled();
    expect(said).toEqual([]);
  });

  test("a turn that aborts after its first words (the headset went away) says nothing", async () => {
    let release!: () => void;
    gate = new Promise((r) => (release = r));
    const browser = new AbortController();
    const res = await voiceTurn("voice", browser.signal);
    // Read until the partial reply has streamed, then go away.
    const reader = res.body!.getReader();
    let seen = "";
    while (!seen.includes("Hola, soy C3PO."))
      seen += new TextDecoder().decode((await reader.read()).value);
    browser.abort();
    release();
    while (!(await reader.read().catch(() => ({ done: true }))).done);
    await settled();
    expect(said).toEqual([]);
  });

  test("a headset that stops reading mid-reply says nothing (outcome unknown, not aborted)", async () => {
    let release!: () => void;
    gate = new Promise((r) => (release = r));
    const res = await voiceTurn();
    const reader = res.body!.getReader();
    let seen = "";
    while (!seen.includes("Hola, soy C3PO."))
      seen += new TextDecoder().decode((await reader.read()).value);
    await reader.cancel();
    release();
    await settled();
    expect(said).toEqual([]);
  });

  test("an operator chat, or the flag off, says nothing and registers nothing", async () => {
    const typed = await voiceTurn("operator");
    expect(activeTurnCount()).toBe(0);
    await typed.text();
    delete process.env.HEADSET_SPEAK_REPLY;
    const off = await voiceTurn();
    expect(activeTurnCount()).toBe(0);
    await off.text();
    await settled();
    expect(said).toEqual([]);
  });
});
