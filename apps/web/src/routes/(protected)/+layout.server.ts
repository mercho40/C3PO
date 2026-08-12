import { redirect } from "@sveltejs/kit";
import { SIDEBAR_COOKIE_NAME } from "$lib/components/ui/sidebar/constants.js";
import { createApi } from "$lib/api";
import type { LayoutServerLoad } from "./$types";

/**
 * Console shell data.
 *
 * The robot state seed lives here rather than in each page's load: the topbar
 * shows connection status on every console route, and the layout owns the one
 * `RobotLive` poller all pages share (see `$lib/robot/context`). Fetching it
 * once at the layout means a page transition doesn't re-seed — or reset — that
 * shared state.
 *
 * If the bridge/backend is unreachable, `state` is null and `online` is false;
 * the console degrades to an "offline" view rather than erroring.
 */
export const load: LayoutServerLoad = async ({
  locals,
  cookies,
  fetch,
  request,
}) => {
  if (!locals.user) redirect(303, "/login");

  const api = createApi(fetch, request.headers.get("cookie"));
  const startedAt = Date.now();
  const { data, error } = await api.state.get();

  // /state is session-guarded, so an expired or revoked session returns 401 —
  // send the operator to /login rather than rendering a misleading "offline"
  // console. Other errors (e.g. 502 bridge_unavailable) legitimately mean
  // offline.
  if (error && (error.status as number) === 401) redirect(303, "/login");

  return {
    // Persist the sidebar's expanded/collapsed state across reloads (the
    // SidebarProvider writes this cookie; we read it back here for SSR).
    sidebarOpen: cookies.get(SIDEBAR_COOKIE_NAME) !== "false",
    state: error ? null : data,
    online: !error,
    latencyMs: Date.now() - startedAt,
  };
};
