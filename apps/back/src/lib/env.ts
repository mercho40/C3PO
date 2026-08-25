/**
 * Startup-validated environment config.
 *
 * Several modules used to read `process.env.X!` / `process.env.X as string`
 * directly -- if the var was unset, that silently produced `undefined` (or
 * the literal string `"undefined"`) as the runtime value instead of failing
 * (e.g. CORS locked to origin `"undefined"`, or DB connecting to
 * `undefined`). Fail fast at boot instead, with a message that names what's
 * missing.
 *
 * GITHUB_/GOOGLE_ client creds are intentionally NOT required here: unset
 * just means that OAuth provider is disabled, which is a legitimate config,
 * not a misconfiguration.
 */

const REQUIRED = [
  "DATABASE_URL",
  "WEB_URL",
  "BETTER_AUTH_URL",
  "BETTER_AUTH_SECRET",
] as const;

const missing = REQUIRED.filter((key) => !process.env[key]);
if (missing.length > 0) {
  throw new Error(
    `Missing required environment variable(s): ${missing.join(", ")}. Check apps/back/.env against .env.example.`,
  );
}

export const env = {
  DATABASE_URL: process.env.DATABASE_URL as string,
  WEB_URL: process.env.WEB_URL as string,
  BETTER_AUTH_URL: process.env.BETTER_AUTH_URL as string,
  BETTER_AUTH_SECRET: process.env.BETTER_AUTH_SECRET as string,
  GITHUB_CLIENT_ID: process.env.GITHUB_CLIENT_ID ?? "",
  GITHUB_CLIENT_SECRET: process.env.GITHUB_CLIENT_SECRET ?? "",
  GOOGLE_CLIENT_ID: process.env.GOOGLE_CLIENT_ID ?? "",
  GOOGLE_CLIENT_SECRET: process.env.GOOGLE_CLIENT_SECRET ?? "",
  PORT: Number(process.env.PORT) || 3000,
  //: Comma-separated emails granted `role: "admin"` at boot. Empty means
  //: nobody — see admin-bootstrap.ts for why this exists at all.
  ADMIN_EMAILS: process.env.ADMIN_EMAILS ?? "",
};
