/**
 * The Better Auth Elysia plugin: mounts the auth handler and defines the
 * `auth: true` macro that resolves `user` / `session` onto the handler context.
 *
 * Built once by `createApp` from a `SessionSource` (the real Better Auth in
 * index.ts, a fake in tests) and handed to every route factory that reads
 * `user`, so the macro is *typed* there. Applying it only via the
 * `.guard({ auth: true }, ...)` at the composition root enforces auth at
 * runtime but leaves `user` untyped inside the route module, because the
 * module's own Elysia instance never saw the macro.
 *
 * Using it in several places is free: the `name` makes Elysia deduplicate, so
 * the handler is mounted once no matter how many routes pull it in.
 */

import { Elysia } from "elysia";

import type { Auth } from "./auth";

/** The signed-in user as routes read it: Better Auth's, with the admin plugin's role. */
export type SessionUser = {
  id: string;
  email: string;
  name: string;
  role?: string | null;
};

export type AuthSession = {
  user: SessionUser;
  session: { id: string; activeOrganizationId?: string | null };
};

/** What the plugin needs from Better Auth: its handler and a session lookup. */
export type SessionSource = {
  handler: (request: Request) => Response | Promise<Response>;
  getSession: (headers: Headers) => Promise<AuthSession | null>;
};

/** The real `SessionSource`. */
export function betterAuthSessions(auth: Auth): SessionSource {
  return {
    handler: auth.handler,
    getSession: (headers) => auth.api.getSession({ headers }),
  };
}

export function createAuthPlugin(sessions: SessionSource) {
  return (
    new Elysia({ name: "better-auth" })
      // Better Auth keys its rate limit on X-Forwarded-For, which any client
      // can write: a new value per attempt was a new bucket. Nothing proxies
      // back (BACK_HOST is loopback; a LAN box takes browsers directly), so
      // the socket is the client. shortcut: a TLS proxy in front would put
      // every client in one bucket; then append instead and list it in
      // advanced.ipAddress.trustedProxies.
      .onRequest(({ request, server }) => {
        const ip = server?.requestIP(request)?.address;
        if (ip) request.headers.set("x-forwarded-for", ip);
        else request.headers.delete("x-forwarded-for");
      })
      .mount(sessions.handler)
      .macro({
        auth: {
          async resolve({ status, request: { headers } }) {
            const session = await sessions.getSession(headers);

            if (!session) return status(401);

            return {
              user: session.user,
              session: session.session,
            };
          },
        },
      })
  );
}

export type AuthPlugin = ReturnType<typeof createAuthPlugin>;
