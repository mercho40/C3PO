/**
 * /safety: status for any operator, «Reanudar» for admins only, and the
 * bridge secret never leaves this server. In process through the real app
 * (`testApp`): a fake session, a recording database, a fake bridge.
 */
import { beforeEach, describe, expect, test } from "bun:test";

import { testApp, type TestApp } from "../testing/fakes";
import { SAFETY_STATUS } from "../testing/payloads";

let h: TestApp;

function call(method: "GET" | "POST", path: string) {
  return h.request(`/safety${path}`, { method });
}

const answer = (url: string) =>
  url.endsWith("/safety/status")
    ? Response.json(SAFETY_STATUS)
    : Response.json({ acknowledged: true, stop_standing: false });

describe("/safety", () => {
  beforeEach(() => {
    h = testApp({ fetch: answer });
  });

  test("401 without a session", async () => {
    expect((await call("GET", "/status")).status).toBe(401);
    expect((await call("POST", "/ack")).status).toBe(401);
  });

  test("status is readable by any operator", async () => {
    h.signIn({ id: "u1", role: null });
    const res = await call("GET", "/status");
    expect(res.status).toBe(200);
    expect(h.fetched[0]?.url).toContain("/safety/status");
  });

  test("status: a bridge that does not answer, or answers no JSON, is a 502", async () => {
    h.signIn({ id: "u1", role: null });
    h.onFetch(() => new Response("<html>", { status: 200 }));
    expect((await call("GET", "/status")).status).toBe(502);
    h.onFetch(() => {
      throw new TypeError("refused");
    });
    const res = await call("GET", "/status");
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "bridge_unavailable" });
  });

  test("resuming is admin-only and never reaches the bridge otherwise", async () => {
    h.signIn({ id: "u1", role: null });
    const res = await call("POST", "/ack");
    expect(res.status).toBe(403);
    expect(h.fetched).toHaveLength(0);
  });

  test("an admin's ack forwards with the secret and is audited", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await call("POST", "/ack");
    expect(res.status).toBe(200);
    expect(h.fetched[0]?.url).toContain("/safety/ack");
    const headers = h.fetched[0]?.init?.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    expect(JSON.parse(String(h.fetched[0]?.init.body))).toEqual({
      stopped_at: null,
    });
    expect(h.rows[0]?.skillName).toBe("safety_ack");
  });

  test("503 when no secret is configured", async () => {
    h = testApp({ fetch: answer, env: { SAFETY_CONFIRM_SECRET: "" } });
    h.signIn({ id: "u1", role: "admin" });
    expect((await call("POST", "/ack")).status).toBe(503);
    expect(h.fetched).toHaveLength(0);
  });
});
