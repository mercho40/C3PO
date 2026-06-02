import { treaty } from "@elysiajs/eden";
import type { App } from "@back/index";
import { PUBLIC_API_URL } from "$env/static/public";

/**
 * Eden Treaty client — end-to-end typed against the backend's `App` type.
 *
 * Always pass the framework `fetch` from a `load` function or hook so SvelteKit
 * can dedupe requests and inline the response into the SSR payload. In the
 * browser the session cookie rides along via `credentials: "include"`.
 *
 * From a **server** `load` hitting a **protected** route, also pass the incoming
 * `cookie` header: SvelteKit's server-side `fetch` does not forward cookies
 * cross-origin to the API (web :3001 → back :3000), so Better Auth wouldn't see
 * the session otherwise.
 *
 * @example Universal / browser load
 *   const api = createApi(fetch);
 *   const { data, error } = await api.skills.get();
 *
 * @example Server load — protected route (+page.server.ts / +layout.server.ts)
 *   export const load = async ({ fetch, request }) => {
 *     const api = createApi(fetch, request.headers.get("cookie"));
 *     const { data } = await api.tasks.get();
 *     return { tasks: data };
 *   };
 */
export const createApi = (
  fetch: typeof globalThis.fetch,
  cookie?: string | null,
) =>
  treaty<App>(PUBLIC_API_URL, {
    fetcher: fetch,
    fetch: { credentials: "include" },
    headers: cookie ? { cookie } : undefined,
  });
