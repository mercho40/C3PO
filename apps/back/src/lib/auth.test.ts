/**
 * Public sign-up is closed, by every door.
 *
 * Asserted on the built config, not through `auth.handler`: the sign-up route
 * opens a transaction before it reads `disableSignUp` (sign-up.mjs wraps the
 * check in runWithTransaction), so without Postgres a request would hang or
 * 500 instead of answering 400. The live 400 is checked by hand
 * (SESSION-CHECKLIST §3c).
 *
 * In process: `createAuth(config, db)` builds Better Auth from a config the
 * test makes, over a lazy database handle that connects to nothing. (It used
 * to be built at auth.ts's import from the process environment, while other
 * suites replaced the module with `mock.module`, so this ran in a child.)
 */

import { beforeAll, describe, expect, test } from "bun:test";
import { Elysia } from "elysia";

import { createDb } from "#back/db/drizzle.js";
import { testConfig } from "#back/testing/fakes.js";
import { createAuth } from "./auth";
import { betterAuthSessions, createAuthPlugin } from "./auth-plugin";

function auth() {
  const config = testConfig({
    // With credentials set the providers are enabled, the case that matters.
    GITHUB_CLIENT_ID: "probe",
    GITHUB_CLIENT_SECRET: "probe",
    GOOGLE_CLIENT_ID: "probe",
    GOOGLE_CLIENT_SECRET: "probe",
  });
  return createAuth(config, createDb(config.databaseUrl));
}

describe("auth sign-up policy", () => {
  const o = auth().options;

  test("email and password sign-in stays on, sign-up is off", () => {
    expect(o.emailAndPassword.enabled).toBe(true);
    expect(o.emailAndPassword.disableSignUp).toBe(true);
  });

  test("OAuth cannot create a user implicitly either", () => {
    expect(o.socialProviders.github.disableSignUp).toBe(true);
    expect(o.socialProviders.google.disableSignUp).toBe(true);
  });
});

/**
 * A real socket on loopback, NODE_ENV unset as under `bun run dev` and the
 * compiled binary: Better Auth leaves rate limiting off there by default, and
 * keys it on X-Forwarded-For, which any client writes.
 */
describe("login rate limiting", () => {
  const signIn: number[] = [];
  let sessionLimited = 0;
  beforeAll(async () => {
    const app = new Elysia()
      .use(createAuthPlugin(betterAuthSessions(auth())))
      .listen({ port: 0, hostname: "127.0.0.1" });
    const base = `http://127.0.0.1:${app.server!.port}/api/auth`;
    for (let i = 0; i < 4; i++) {
      const res = await fetch(`${base}/sign-in/email`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-forwarded-for": `203.0.113.${i}`,
        },
        body: "{}",
      });
      signIn.push(res.status);
    }
    for (let i = 0; i < 120; i++) {
      if ((await fetch(`${base}/get-session`)).status === 429) sessionLimited++;
    }
    await app.stop(true);
  });

  // A new X-Forwarded-For per attempt used to be a fresh bucket per attempt.
  test("a fourth sign-in in 10 s is refused, whatever X-Forwarded-For says", () => {
    expect(signIn.slice(0, 3)).not.toContain(429);
    expect(signIn[3]).toBe(429);
  });

  // The console's server hooks call it for every operator from one address.
  test("the session check is never limited", () => {
    expect(sessionLimited).toBe(0);
  });
});
