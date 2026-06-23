import { redirect } from "@sveltejs/kit";
import type { PageServerLoad } from "./$types";
import { createApi } from "$lib/api";

/**
 * Load the live robot state from the backend (`GET /state`, which proxies the
 * bridge's `get_state`). Measures the round-trip so the dashboard can show real
 * latency. If the bridge/backend is unreachable, `state` is null and `online`
 * is false — the page degrades to an "offline" view.
 */
export const load: PageServerLoad = async ({ fetch, request }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const startedAt = Date.now();
  const { data, error } = await api.state.get();

  // Once /state is guarded, an expired/revoked session returns 401 — send the
  // operator to /login rather than rendering a misleading "offline" dashboard.
  // (Other errors, e.g. 502 bridge_unavailable, legitimately mean offline.)
  if (error && (error.status as number) === 401) redirect(303, "/login");

  return {
    state: error ? null : data,
    online: !error,
    latencyMs: Date.now() - startedAt,
  };
};
