/**
 * Spoken confirmation end to end through /voice/transcribe: the admin's own
 * push-to-talk text is decided HERE, before any model sees it. «confirmo» with
 * the one refused call on screen grants and runs exactly that call; anything
 * else comes back as plain text for the agent.
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

const { voiceRoutes } = await import("./voice");
const confirmations = await import("#back/agent/confirmations.js");
const bridgeClient = await import("#back/bridge/client.js");
const skillsIndex = await import("#back/skills/index.js");

const pick = { label: "Dixie cup", shape: "power", side: "auto" };
const realFetch = globalThis.fetch;
let heard = "confirmo";
let grants: unknown[] = [];
let dispatched: unknown[][] = [];
let restore: (() => void)[] = [];

function say(query: string) {
  return voiceRoutes.handle(
    new Request(`http://localhost/voice/transcribe${query}`, {
      method: "POST",
      headers: { "content-type": "audio/wav" },
      body: new Uint8Array(64),
    }),
  );
}
const refuse = (id: string, tool = "pick") =>
  confirmations.notePending({
    id,
    tool,
    args: pick,
    ownerId: "u1",
    chatId: "chat_1",
    loop: false,
  });

describe("spoken confirmation on /voice/transcribe", () => {
  beforeEach(() => {
    confirmations.clearPending();
    sessionUser = { id: "u1", role: "admin" };
    process.env.AGENT_API_KEY = "k";
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    heard = "confirmo";
    grants = [];
    dispatched = [];
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      if (String(url).endsWith("/audio/transcriptions"))
        return Response.json({ text: heard });
      grants.push(JSON.parse(String(init.body)));
      return Response.json({ granted: true });
    }) as unknown as typeof fetch;
    const call = spyOn(bridgeClient, "callTool").mockImplementation(
      async (name, args) => {
        dispatched.push([name, args]);
        return { status: "completed", phase: "lifted" };
      },
    );
    const skill = spyOn(skillsIndex, "getSkill").mockImplementation(
      async (name) =>
        ({
          name,
          description: "",
          parameters: { type: "object", additionalProperties: true },
          classification: "manipulation",
          dangerLevel: "medium",
          tier: "T2",
        }) as never,
    );
    restore = [() => call.mockRestore(), () => skill.mockRestore()];
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    for (const r of restore) r();
    confirmations.clearPending();
  });

  test("«Sí, confirmo» with the one call on screen runs exactly it, once", async () => {
    heard = "Sí, confirmo.";
    refuse("c1");
    const res = await say("?chatId=chat_1&pending=c1");
    const body = (await res.json()) as {
      confirmation: { action: string; message: string; result: unknown };
    };
    expect(body.confirmation.action).toBe("confirmed");
    expect(body.confirmation.message).toStartWith(
      "El operador confirmó y se ejecutó pick",
    );
    expect(grants).toEqual([{ tool: "pick", args: pick }]);
    expect(dispatched).toEqual([["pick", pick]]);
    // Said again: nothing left to run.
    const again = (await (await say("?chatId=chat_1&pending=c1")).json()) as {
      confirmation?: unknown;
    };
    expect(again.confirmation).toBeUndefined();
    expect(dispatched.length).toBe(1);
  });

  test("with nothing pending, «confirmo» is just text for the agent", async () => {
    const body = await (await say("?chatId=chat_1&pending=c1")).json();
    expect(body).toEqual({ text: "confirmo" });
    expect(dispatched).toEqual([]);
  });

  test("with two pending, it asks which and runs neither", async () => {
    refuse("c1");
    refuse("c2", "place");
    const body = (await (await say("?chatId=chat_1&pending=c2")).json()) as {
      confirmation: unknown;
    };
    expect(body.confirmation).toEqual({ action: "ambiguous", count: 2 });
    expect(dispatched).toEqual([]);
    expect(grants).toEqual([]);
  });

  test("a long sentence containing «confirmo» goes to the agent, not the arm", async () => {
    heard = "confirmo que el vaso está a la izquierda, agarralo";
    refuse("c1");
    const body = await (await say("?chatId=chat_1&pending=c1")).json();
    expect(body).toEqual({ text: heard });
    expect(dispatched).toEqual([]);
    expect(confirmations.listPending("u1", "chat_1").length).toBe(1);
  });

  test("«cancelar» clears it and runs nothing", async () => {
    heard = "Cancelar.";
    refuse("c1");
    const body = (await (await say("?chatId=chat_1&pending=c1")).json()) as {
      confirmation: { action: string; message: string };
    };
    expect(body.confirmation.action).toBe("cancelled");
    expect(body.confirmation.message).toContain("canceló pick");
    expect(confirmations.listPending("u1", "chat_1")).toEqual([]);
    expect(dispatched).toEqual([]);
  });

  test("a client that names no call on screen gets no spoken confirmation", async () => {
    refuse("c1");
    expect(await (await say("?chatId=chat_1")).json()).toEqual({
      text: "confirmo",
    });
    expect(dispatched).toEqual([]);
  });
});
