/**
 * /safety: status for any operator, «Reanudar» for admins only, and the
 * bridge secret never leaves this server. Auth and the DB are mocked, as in
 * skills.test.ts; the bridge is a stubbed global fetch.
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
// The DB layer mocked exactly as skills.test.ts does (same surface: bun's
// module mocks are process-wide), so the real logToolCall runs.
const logged: Record<string, unknown>[] = [];
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
    insert: () => ({
      values: (row: Record<string, unknown>) => {
        logged.push(row);
        return thenable([]);
      },
    }),
    select: () => thenable([]),
    update: () => thenable([]),
    delete: () => thenable([]),
    query: { chat: { findFirst: async () => undefined } },
  },
}));

const { safetyRoutes } = await import("./safety");
const realFetch = globalThis.fetch;
let seen: { url: string; init?: RequestInit }[] = [];

function call(method: "GET" | "POST", path: string) {
  return safetyRoutes.handle(
    new Request(`http://localhost/safety${path}`, { method }),
  );
}

describe("/safety", () => {
  beforeEach(() => {
    sessionUser = null;
    seen = [];
    logged.length = 0;
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      seen.push({ url: String(url), init });
      return new Response(
        JSON.stringify({ acknowledged: true, stop_standing: false }),
        {
          status: 200,
        },
      );
    }) as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  test("401 without a session", async () => {
    expect((await call("GET", "/status")).status).toBe(401);
    expect((await call("POST", "/ack")).status).toBe(401);
  });

  test("status is readable by any operator", async () => {
    sessionUser = { id: "u1", role: null };
    const res = await call("GET", "/status");
    expect(res.status).toBe(200);
    expect(seen[0]?.url).toContain("/safety/status");
  });

  test("resuming is admin-only and never reaches the bridge otherwise", async () => {
    sessionUser = { id: "u1", role: null };
    const res = await call("POST", "/ack");
    expect(res.status).toBe(403);
    expect(seen).toHaveLength(0);
  });

  test("an admin's ack forwards with the secret and is audited", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await call("POST", "/ack");
    expect(res.status).toBe(200);
    expect(seen[0]?.url).toContain("/safety/ack");
    const headers = seen[0]?.init?.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    expect(logged[0]?.skillName).toBe("safety_ack");
  });

  test("503 when no secret is configured", async () => {
    sessionUser = { id: "u1", role: "admin" };
    delete process.env.SAFETY_CONFIRM_SECRET;
    expect((await call("POST", "/ack")).status).toBe(503);
  });
});
