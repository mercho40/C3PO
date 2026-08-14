/**
 * Shared `auth` macro plugin — resolves the session into `user`/`session`
 * context for any route that opts in with `{ auth: true }`.
 *
 * Named (`name: "better-auth"`) so Elysia dedupes it: multiple `.use()`
 * calls across `index.ts` and individual route files (e.g. `routes/skills.ts`,
 * which needs `user.role` for its admin-only invoke route) all resolve to
 * the same plugin instance instead of re-registering `auth.handler`.
 */

import { Elysia } from "elysia";
import { auth } from "@back/lib/auth";

export const betterAuthPlugin = new Elysia({ name: "better-auth" })
  .mount(auth.handler)
  .macro({
    auth: {
      async resolve({ status, request: { headers } }) {
        const session = await auth.api.getSession({
          headers,
        });

        if (!session) return status(401);

        return {
          user: session.user,
          session: session.session,
        };
      },
    },
  });
