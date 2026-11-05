import { betterAuth } from "better-auth/minimal";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db } from "#back/db/drizzle.js";
import { admin, organization } from "better-auth/plugins";
import { env } from "#back/lib/env.js";

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
  // Sign-up is closed: every account is made at a terminal with
  // scripts/create-user.ts. A model with a robot behind it should not be one
  // self-registration away from anyone on the network. Better Auth checks the
  // flag server-side (sign-up.mjs), so no client can opt back in; admin
  // createUser called from the server is not affected.
  emailAndPassword: {
    enabled: true,
    disableSignUp: true,
  },
  baseURL: env.BETTER_AUTH_URL,
  // disableSignUp on each provider too: without it an OAuth callback for an
  // unknown address creates the user implicitly, which is sign-up by another
  // door (callback.mjs reads `provider.options.disableSignUp`). Existing
  // accounts still sign in with their linked provider.
  socialProviders: {
    github: {
      enabled: enabledSocialProviders.includes("github"),
      disableSignUp: true,
      clientId: env.GITHUB_CLIENT_ID,
      clientSecret: env.GITHUB_CLIENT_SECRET,
    },
    google: {
      enabled: enabledSocialProviders.includes("google"),
      disableSignUp: true,
      clientId: env.GOOGLE_CLIENT_ID,
      clientSecret: env.GOOGLE_CLIENT_SECRET,
    },
  },
  trustedOrigins: [env.WEB_URL],
  // Joined session reads — one query instead of N on getSession.
  // Lives under advanced.database; better-auth 1.7 dropped `experimental.joins`.
  advanced: { database: { joins: true } },
  session: {
    cookieCache: {
      enabled: true,
      maxAge: 5 * 60, // Cache duration in seconds
    },
  },
});
