import { redirect } from "@sveltejs/kit";
import type { PageServerLoad } from "./$types";
import { createApi } from "$lib/api";

/**
 * Fetch current `env` from `GET /state` before rendering. The sim-only
 * teleimager WebRTC viewer below (three fixed ports, one per camera) doesn't
 * apply to real hardware: the real G1 has one camera, not three, and reaches
 * it over a different transport entirely -- a passive ZeroMQ subscriber to
 * teleimager's own feed, relayed over WebSocket (`apps/bridge/src/bridge/
 * camera_relay.py`), consumed by `/vr-control` instead of this page. Without
 * this check, the page would silently try the sim ports against a real
 * robot and sit on "Sin señal" forever with no explanation — same class of
 * bug as the agent system prompt fix.
 */
export const load: PageServerLoad = async ({ fetch, request }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const { data, error } = await api.state.get();

  if (error && (error.status as number) === 401) redirect(303, "/login");

  // Unknown env (bridge unreachable, or an env value we don't recognize)
  // falls through to attempting the sim viewer as before -- only suppress it
  // when we positively know we're pointed at real hardware.
  return { env: error ? null : (data?.env ?? null) };
};
