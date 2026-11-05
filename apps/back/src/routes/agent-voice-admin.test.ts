/**
 * 1.4: /agent, /voice/start and /voice/transcribe are admin-only; /voice/stop
 * stays open to any session (stopping is the safe direction, like PARAR).
 * Auth and the DB are mocked as in safety.test.ts; every network call goes
 * through a stubbed global fetch so a refusal can be shown to reach nothing.
 */
import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";

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
