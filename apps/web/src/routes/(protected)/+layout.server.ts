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
  url,
  untrack,
}) => {
  // Back to where they were after signing in — a Quest operator whose session
  // lapsed mid-teleop should land on /vr-control, not the dashboard.
  //
  // `untrack`: reading `url` would otherwise make SvelteKit re-run this load —
  // and its /state round trip — on every client-side navigation.
  const here = untrack(() => url.pathname + url.search);
  const loginUrl = (reason?: string) => {
    const params = new URLSearchParams({ redirectTo: here });
    if (reason) params.set("reason", reason);
    return `/login?${params}`;
  };
  if (!locals.user) redirect(303, loginUrl());

  const api = createApi(fetch, request.headers.get("cookie"));
  const startedAt = Date.now();
  // Bounded: /state reaches the bridge over MCP, whose default timeout is 60 s.
  // A stalled bridge must make the console render "offline", not hang the
  // whole page load (and every sidebar click) for a minute.
  const { data, error } = await api.state
    .get({ fetch: { signal: AbortSignal.timeout(3000) } })
    .catch(() => ({ data: null, error: { status: 504, value: null } }));

  // /state is session-guarded, so an expired or revoked session returns 401 —
  // send the operator to /login rather than rendering a misleading "offline"
  // console. Other errors (e.g. 502 bridge_unavailable) legitimately mean
  // offline.
  if (error && (error.status as number) === 401)
    redirect(303, loginUrl("expired"));

  return {
    // Persist the sidebar's expanded/collapsed state across reloads (the
    // SidebarProvider writes this cookie; we read it back here for SSR).
    sidebarOpen: cookies.get(SIDEBAR_COOKIE_NAME) !== "false",
    // From the session cookie cache, so up to 5 minutes behind a role change
    // (see /settings). Direct skill invocation is admin-only server-side; this
    // only decides what the console offers.
    isAdmin: locals.user.role === "admin",
    state: error ? null : data,
    online: !error,
    latencyMs: Date.now() - startedAt,
  };
};
