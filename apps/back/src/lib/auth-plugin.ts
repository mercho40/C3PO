/**
 * The Better Auth Elysia plugin: mounts the auth handler and defines the
 * `auth: true` macro that resolves `user` / `session` onto the handler context.
 *
 * Lives in its own module so route files that actually read `user` can
 * `.use(betterAuth)` and get the macro *typed*. Applying it only via the
 * `.guard({ auth: true }, ...)` at the composition root enforces auth at
 * runtime but leaves `user` untyped inside the route module, because the
 * module's own Elysia instance never saw the macro.
 *
 * Using it in several places is free: the `name` makes Elysia deduplicate, so
 * the handler is mounted once no matter how many routes pull it in.
 */

import { Elysia } from "elysia";

import { auth } from "@back/lib/auth";

export const betterAuth = new Elysia({ name: "better-auth" })
  .mount(auth.handler)
  .macro({
    auth: {
      async resolve({ status, request: { headers } }) {
        const session = await auth.api.getSession({ headers });

        if (!session) return status(401);

        return {
          user: session.user,
          session: session.session,
        };
      },
    },
  });
