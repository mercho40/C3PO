import type { getCookieCache } from "better-auth/cookies";

type CookieSession = NonNullable<Awaited<ReturnType<typeof getCookieCache>>>;

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
