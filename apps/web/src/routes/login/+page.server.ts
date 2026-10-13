import { redirect } from "@sveltejs/kit";
import { safeRedirectTo } from "./auth-params";
import { loadAuthPage } from "./auth-options.server";
import type { PageServerLoad } from "./$types";

// Already signed in: go where the operator was headed, not always /dashboard.
// This server redirect is the only one — /login's old client-side `$effect`
// redirect is gone now that logout navigates with `invalidateAll` (it was what
// produced the /login → /dashboard → /login bounce after signing out).
export const load: PageServerLoad = async ({ locals, url, fetch }) => {
  if (locals.user)
    redirect(303, safeRedirectTo(url.searchParams.get("redirectTo")));
  return loadAuthPage(fetch, url, locals.backendDown);
};
