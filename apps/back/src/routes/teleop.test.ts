/**
 * /teleop/token: admins only, and the token is the one the bridge checks.
 * Auth is mocked as in safety.test.ts.
 */
import { beforeEach, describe, expect, mock, test } from "bun:test";

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

const { teleopRoutes, mintTeleopToken, TELEOP_TOKEN_TTL_S } =
  await import("./teleop");

const get = () =>
  teleopRoutes.handle(new Request("http://localhost/teleop/token"));

describe("/teleop/token", () => {
  beforeEach(() => {
    sessionUser = null;
    process.env.TELEOP_SECRET = "s3cret";
  });

  test("401 without a session, 403 for a non-admin", async () => {
    expect((await get()).status).toBe(401);
    sessionUser = { id: "u1", role: null };
    expect((await get()).status).toBe(403);
  });

  test("an admin gets a token that expires soon", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await get();
    expect(res.status).toBe(200);
    const { token } = (await res.json()) as { token: string };
    const exp = Number(token.split(".")[0]);
    const now = Date.now() / 1000;
    expect(exp).toBeGreaterThan(now);
    expect(exp).toBeLessThanOrEqual(now + TELEOP_TOKEN_TTL_S + 1);
  });

  test("no secret: a null token, so the bridge's no-secret mode still works", async () => {
    sessionUser = { id: "u1", role: "admin" };
    delete process.env.TELEOP_SECRET;
    expect(await (await get()).json()).toEqual({ token: null });
  });

  test("matches the vector pinned in the bridge's test_teleop_auth.py", () => {
    const nowMs = (2_000_000_000 - TELEOP_TOKEN_TTL_S) * 1000;
    expect(mintTeleopToken("s3cret", nowMs)).toBe(
      "2000000000.ed699e91ba1f8c4d77c5c127987656732ed9ebea0ab638c9ce2937485964ff63",
    );
  });
});
