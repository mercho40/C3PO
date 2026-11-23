/**
 * /teleop/token: admins only, and the token is the one the bridge checks.
 * In process through the real app (`testApp`).
 */
import { beforeEach, describe, expect, test } from "bun:test";

import { testApp, type TestApp } from "../testing/fakes";
import { mintTeleopToken, TELEOP_TOKEN_TTL_S } from "./teleop";

let h: TestApp;
const get = () => h.request("/teleop/token");

/** What the bridge answers back's clock probe: a Date header, or nothing. */
let bridgeDate: string | null = null;
const bridge = () => {
  if (bridgeDate === null) throw new TypeError("fetch failed");
  return new Response("{}", { headers: { date: bridgeDate } });
};

describe("/teleop/token", () => {
  beforeEach(() => {
    bridgeDate = null;
    h = testApp({ env: { TELEOP_SECRET: "s3cret" }, fetch: bridge });
  });

  test("401 without a session, 403 for a non-admin", async () => {
    expect((await get()).status).toBe(401);
    h.signIn({ id: "u1", role: null });
    expect((await get()).status).toBe(403);
  });

  test("an admin gets a token that expires soon", async () => {
    h.signIn({ id: "u1", role: "admin" });
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
    h.signIn({ id: "u1", role: "admin" });
    const robotNow = Math.floor(Date.now() / 1000) - 2 * 3600;
    bridgeDate = new Date(robotNow * 1000).toUTCString();
    const { token } = (await (await get()).json()) as { token: string };
    expect(Number(token.split(".")[0])).toBe(robotNow + TELEOP_TOKEN_TTL_S);
    expect(h.fetched.map((f) => new URL(f.url).pathname)).toEqual([
      "/safety/status",
    ]);
  });

  test("no secret: a null token, so the bridge's no-secret mode still works", async () => {
    h = testApp({ env: { TELEOP_SECRET: "" }, fetch: bridge });
    h.signIn({ id: "u1", role: "admin" });
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
