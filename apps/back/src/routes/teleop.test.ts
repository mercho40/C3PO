/**
 * /teleop/token: admins only, and the token is the one the bridge checks.
 * Auth is mocked as in safety.test.ts.
 */
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  mock,
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

const { teleopRoutes, mintTeleopToken, TELEOP_TOKEN_TTL_S } =
  await import("./teleop");

const get = () =>
  teleopRoutes.handle(new Request("http://localhost/teleop/token"));

const savedSecret = process.env.TELEOP_SECRET;
const realFetch = globalThis.fetch;
/** What the bridge answers back's clock probe: a Date header, or nothing. */
let bridgeDate: string | null = null;
let probed: string[] = [];

describe("/teleop/token", () => {
  beforeEach(() => {
    sessionUser = null;
    process.env.TELEOP_SECRET = "s3cret";
    bridgeDate = null;
    probed = [];
    globalThis.fetch = (async (url: string) => {
      probed.push(String(url));
      if (bridgeDate === null) throw new TypeError("fetch failed");
      return new Response("{}", { headers: { date: bridgeDate } });
    }) as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  afterAll(() => {
    if (savedSecret === undefined) delete process.env.TELEOP_SECRET;
    else process.env.TELEOP_SECRET = savedSecret;
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

  // The Jetson has no RTC: its clock, which checks the expiry, is not ours.
  test("the expiry is minted on the robot's clock, read off the bridge", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const robotNow = Math.floor(Date.now() / 1000) - 2 * 3600;
    bridgeDate = new Date(robotNow * 1000).toUTCString();
    const { token } = (await (await get()).json()) as { token: string };
    expect(Number(token.split(".")[0])).toBe(robotNow + TELEOP_TOKEN_TTL_S);
    expect(probed.map((u) => new URL(u).pathname)).toEqual(["/safety/status"]);
  });

  test("no secret: a null token, so the bridge's no-secret mode still works", async () => {
    sessionUser = { id: "u1", role: "admin" };
    delete process.env.TELEOP_SECRET;
    expect(await (await get()).json()).toEqual({ token: null });
  });

  test("matches the vector pinned in the bridge's test_teleop_auth.py", () => {
    const nowMs = (2_000_000_000 - TELEOP_TOKEN_TTL_S) * 1000;
    expect(
      mintTeleopToken("s3cret", nowMs, "00112233445566778899aabbccddeeff"),
    ).toBe(
      "2000000000.00112233445566778899aabbccddeeff.7d584c9233bce66c16e6bebbf5e8875eb04b0f0f9f6593f9fea1c313677920c1",
    );
  });

  test("two tokens minted in the same second differ: the bridge spends each once", () => {
    expect(mintTeleopToken("s3cret", 0)).not.toBe(mintTeleopToken("s3cret", 0));
  });
});
