import { betterAuth } from "better-auth/minimal";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db } from "@back/db/drizzle";
import { admin, organization } from "better-auth/plugins";
import { env } from "@back/lib/env";

/**
 * Which social providers actually have credentials.
 *
 * env.ts says "unset just means that OAuth provider is disabled", but Better
 * Auth only WARNS about a provider with an empty clientId and keeps it
 * registered — so the console's Google/GitHub buttons sent operators to the
 * provider's own "missing client_id" error page. `enabled` makes the comment
 * true, and the list is published on /health so the login page can render only
 * the buttons that work.
 */
export const enabledSocialProviders = (
  [
    ["github", env.GITHUB_CLIENT_ID, env.GITHUB_CLIENT_SECRET],
    ["google", env.GOOGLE_CLIENT_ID, env.GOOGLE_CLIENT_SECRET],
  ] as const
)
  .filter(([, id, secret]) => Boolean(id && secret))
  .map(([name]) => name);

export const auth = betterAuth({
  database: drizzleAdapter(db, {
    provider: "pg",
  }),
  plugins: [admin(), organization()],
  emailAndPassword: {
    enabled: true,
  },
  baseURL: env.BETTER_AUTH_URL,
  socialProviders: {
    github: {
      enabled: enabledSocialProviders.includes("github"),
      clientId: env.GITHUB_CLIENT_ID,
      clientSecret: env.GITHUB_CLIENT_SECRET,
    },
    google: {
      enabled: enabledSocialProviders.includes("google"),
      clientId: env.GOOGLE_CLIENT_ID,
      clientSecret: env.GOOGLE_CLIENT_SECRET,
    },
  },
  trustedOrigins: [env.WEB_URL],
  // Joined session reads — one query instead of N on getSession.
  experimental: { joins: true },
  session: {
    cookieCache: {
      enabled: true,
      maxAge: 5 * 60, // Cache duration in seconds
    },
  },
});
