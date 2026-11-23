import { PUBLIC_API_URL } from "$app/env/public";
import { apiAt } from "./api-client";

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
 * Plain modules that `bun test` loads take back's address instead and use
 * `apiAt(base, fetch)` (api-client.ts) — the same client.
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
) => apiAt(PUBLIC_API_URL, fetch, cookie);
