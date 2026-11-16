/**
 * Public sign-up is closed, by every door.
 *
 * Asserted on the built config, not through `auth.handler`: the sign-up route
 * opens a transaction before it reads `disableSignUp` (sign-up.mjs wraps the
 * check in runWithTransaction), so without Postgres a request would hang or
 * 500 instead of answering 400. The live 400 is checked by hand
 * (SESSION-CHECKLIST §3c).
 *
 * Read in a child process. Other suites mock `#back/lib/auth.js` and the DB
 * with bun's process-wide module mocks, so an import here could get their stub
 * depending on file order. The child gets dummy env: importing auth.ts builds
 * the config and the lazy DB handle, and connects to nothing.
 */

import { describe, expect, test } from "bun:test";
import { fileURLToPath } from "node:url";

const BACK = fileURLToPath(new URL("../..", import.meta.url));

const PROBE = `
const { auth } = await import("./src/lib/auth.ts");
const o = auth.options;
console.log(JSON.stringify({
  email: o.emailAndPassword,
  github: o.socialProviders.github.disableSignUp,
  google: o.socialProviders.google.disableSignUp,
}));
`;

function readConfig(): {
  email: { enabled?: boolean; disableSignUp?: boolean };
  github?: boolean;
  google?: boolean;
} {
  return runProbe(PROBE) as ReturnType<typeof readConfig>;
}

function runProbe(probe: string): unknown {
  const proc = Bun.spawnSync(["bun", "--no-env-file", "-e", probe], {
    cwd: BACK,
    env: {
      PATH: process.env.PATH ?? "",
      HOME: process.env.HOME ?? "",
      DATABASE_URL: "postgres://probe:probe@127.0.0.1:1/probe",
      WEB_URL: "http://localhost:3001",
      BETTER_AUTH_URL: "http://localhost:3000",
      BETTER_AUTH_SECRET: "Zq8vR2mK7xT4nB9pL3wY6cF1hJ5sD0gA",
      // With credentials set the providers are enabled, the case that matters.
      GITHUB_CLIENT_ID: "probe",
      GITHUB_CLIENT_SECRET: "probe",
      GOOGLE_CLIENT_ID: "probe",
      GOOGLE_CLIENT_SECRET: "probe",
    },
    timeout: 30_000,
  });
  const lines = proc.stdout.toString().trim().split("\n");
  const out = lines[lines.length - 1] ?? "";
  if (proc.exitCode !== 0 || !out.startsWith("{")) {
    throw new Error(
      `auth.ts probe failed (${proc.exitCode}): ${proc.stderr.toString()}`,
    );
  }
  return JSON.parse(out);
}

describe("auth sign-up policy", () => {
  const config = readConfig();

  test("email and password sign-in stays on, sign-up is off", () => {
    expect(config.email.enabled).toBe(true);
    expect(config.email.disableSignUp).toBe(true);
  });

  test("OAuth cannot create a user implicitly either", () => {
    expect(config.github).toBe(true);
    expect(config.google).toBe(true);
  });
});

/**
 * A real socket on loopback, NODE_ENV unset as under `bun run dev` and the
 * compiled binary: Better Auth leaves rate limiting off there by default, and
 * keys it on X-Forwarded-For, which any client writes.
 */
const LIMIT_PROBE = `
const { Elysia } = await import("elysia");
const { betterAuth } = await import("./src/lib/auth-plugin.ts");
const app = new Elysia().use(betterAuth).listen({ port: 0, hostname: "127.0.0.1" });
const base = "http://127.0.0.1:" + app.server.port + "/api/auth";
const signIn = [];
for (let i = 0; i < 4; i++) {
  const res = await fetch(base + "/sign-in/email", {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-for": "203.0.113." + i },
    body: "{}",
  });
  signIn.push(res.status);
}
let sessionLimited = 0;
for (let i = 0; i < 120; i++) {
  if ((await fetch(base + "/get-session")).status === 429) sessionLimited++;
}
console.log(JSON.stringify({ signIn, sessionLimited }));
process.exit(0);
`;

describe("login rate limiting", () => {
  const { signIn, sessionLimited } = runProbe(LIMIT_PROBE) as {
    signIn: number[];
    sessionLimited: number;
  };

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
