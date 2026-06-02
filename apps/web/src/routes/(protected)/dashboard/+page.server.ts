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
  return {
    state: error ? null : data,
    online: !error,
    latencyMs: Date.now() - startedAt,
  };
};
