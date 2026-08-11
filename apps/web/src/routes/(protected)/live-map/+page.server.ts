import { redirect } from "@sveltejs/kit";
import type { PageServerLoad } from "./$types";
import { createApi } from "$lib/api";

/**
 * Seed the live map with the current robot state (`GET /state`, which proxies
 * the bridge's `get_state`) so the marker paints instantly; the page then polls
 * client-side to stay live. If the bridge/backend is unreachable, `state` is
 * null and `online` is false — the map degrades to an "offline" view.
 */
export const load: PageServerLoad = async ({ fetch, request }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const startedAt = Date.now();
  const { data, error } = await api.state.get();

  // Once /state is guarded, an expired/revoked session returns 401 — send the
  // operator to /login rather than rendering a misleading "offline" map.
  if (error && (error.status as number) === 401) redirect(303, "/login");

  return {
    state: error ? null : data,
    online: !error,
    latencyMs: Date.now() - startedAt,
  };
};
