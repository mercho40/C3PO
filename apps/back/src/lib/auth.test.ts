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
  const proc = Bun.spawnSync(["bun", "--no-env-file", "-e", PROBE], {
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
