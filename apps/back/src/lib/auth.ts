import { betterAuth } from "better-auth/minimal";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { admin, organization } from "better-auth/plugins";

import type { Config } from "#back/config.js";
import type { Db } from "#back/db/drizzle.js";

/**
 * Which social providers actually have credentials.
 *
 * config.ts says "unset means that OAuth provider is disabled", but Better
 * Auth only WARNS about a provider with an empty clientId and keeps it
 * registered — so the console's Google/GitHub buttons sent operators to the
 * provider's own "missing client_id" error page. `enabled` makes the comment
 * true, and the list is published on /health so the login page can render only
 * the buttons that work.
 */
export function enabledSocialProviders(
  auth: Config["auth"],
): ("github" | "google")[] {
  return (
    [
      ["github", auth.github.clientId, auth.github.clientSecret],
      ["google", auth.google.clientId, auth.google.clientSecret],
    ] as const
  )
    .filter(([, id, secret]) => Boolean(id && secret))
    .map(([name]) => name);
}

/** Better Auth over this database, configured from `config`. Connects to nothing until used. */
export function createAuth(config: Pick<Config, "auth" | "webUrl">, db: Db) {
  const enabled = enabledSocialProviders(config.auth);
  return betterAuth({
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
    baseURL: config.auth.url,
    secret: config.auth.secret,
    // disableSignUp on each provider too: without it an OAuth callback for an
    // unknown address creates the user implicitly, which is sign-up by another
    // door (callback.mjs reads `provider.options.disableSignUp`). Existing
    // accounts still sign in with their linked provider.
    socialProviders: {
      github: {
        enabled: enabled.includes("github"),
        disableSignUp: true,
        clientId: config.auth.github.clientId,
        clientSecret: config.auth.github.clientSecret,
      },
      google: {
        enabled: enabled.includes("google"),
        disableSignUp: true,
        clientId: config.auth.google.clientId,
        clientSecret: config.auth.google.clientSecret,
      },
    },
    trustedOrigins: [config.webUrl],
    // Better Auth only turns this on under NODE_ENV=production, which nothing
    // here sets. Keyed on the socket address (auth-plugin.ts): the browser signs
    // in straight to back. /get-session is exempt: the console's server hooks
    // ask it for every operator from one address, and a token is not guessable.
    rateLimit: { enabled: true, customRules: { "/get-session": false } },
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
}

export type Auth = ReturnType<typeof createAuth>;
