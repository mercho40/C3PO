import { getCookieCache } from "better-auth/cookies";
import { BETTER_AUTH_SECRET } from "$env/static/private";
import { authClient } from "$lib/auth-client";
import type { Handle } from "@sveltejs/kit";

export const handle: Handle = async ({ event, resolve }) => {
  // Fast path: Better Auth's signed cookie cache (5-min TTL) — no backend call.
  //
  // The secret is passed explicitly rather than left to `process.env`. Vite
  // does not copy `.env` into `process.env`, so a library reading it directly
  // sees nothing under Node — this worked only while the dev script forced
  // Bun's runtime, which auto-loads `.env`, and broke the moment that flag was
  // removed. `$env/static/private` is resolved at build time and behaves the
  // same under every runtime and in production.
  let session = await getCookieCache(event.request, {
    secret: BETTER_AUTH_SECRET,
  });
  let refreshedCookies: string[] = [];
  event.locals.backendDown = false;

  const cookieHeader = event.request.headers.get("cookie") ?? "";
  // When the cache lapses, `getCookieCache` returns null — but the user isn't
  // logged out, the real session token is still valid. Revalidate via Better
  // Auth's `getSession` (which calls the backend, re-checks the session in the
  // DB, and re-issues a fresh cache cookie); relay that Set-Cookie so the fast
  // path resumes instead of bouncing to /login every cache cycle. Skip it when
  // there's no session token at all (a genuinely logged-out visitor).
  if (!session && cookieHeader.includes("better-auth.session_token")) {
    // GUARDED, because this used to take the whole site down with the backend.
    // better-fetch only swallows network errors under `catchAllError`, which
    // Better Auth's client does not set — so with apps/back stopped, a returning
    // operator (whose 5-minute cache has almost always lapsed) got SvelteKit's
    // English "500 Internal Error" on every page, /login included, and could not
    // even find out that the backend was the problem.
    //
    // On failure: no session for this request, cookies UNTOUCHED (the session
    // may be perfectly valid once back returns — clearing it would log the
    // operator out over an outage), and a flag so /login can say why.
    // The timeout keeps a stalled backend from hanging every page load.
    try {
      const { data, error } = await authClient.getSession({
        fetchOptions: {
          headers: { cookie: cookieHeader },
          timeout: 3000,
          onResponse(context) {
            refreshedCookies = context.response.headers.getSetCookie();
          },
        },
      });
      session = (data ?? null) as typeof session;
      // A 5xx is back up but broken (e.g. its database) — same outcome for the
      // operator as unreachable. A 401 is just a session that ended.
      if (error && (error.status ?? 0) >= 500) event.locals.backendDown = true;
    } catch {
      session = null;
      event.locals.backendDown = true;
    }
  }

  event.locals.user = session?.user ?? null;
  event.locals.session = session?.session ?? null;

  const response = await resolve(event, {
    preload: ({ type }) => type === "font" || type === "js" || type === "css",
  });

  // Relay the refreshed cache cookie (dev/localhost: same host, so it applies;
  // for cross-subdomain prod, enable advanced.crossSubDomainCookies on the backend).
  for (const cookie of refreshedCookies) {
    response.headers.append("set-cookie", cookie);
  }
  return response;
};
