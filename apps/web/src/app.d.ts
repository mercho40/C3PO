import type { authClient } from "#lib/auth-client.js";

// The client's inferred session, not `getCookieCache`'s return type: since
// better-auth 1.7 that is the plugin-agnostic payload, where `user.role`
// (admin plugin) is `unknown`. hooks.server.ts reads the cookie with it.
type CookieSession = typeof authClient.$Infer.Session;

declare global {
  namespace App {
    interface Locals {
      user: CookieSession["user"] | null;
      session: CookieSession["session"] | null;
      /**
       * The session revalidation in hooks.server.ts could not reach apps/back
       * (or got a 5xx). `user` is null for this request, but the session cookie
       * was left alone — it may still be valid once back returns.
       */
      backendDown: boolean;
    }
    interface PageData {
      user: CookieSession["user"] | null;
    }
  }
}

export {};
