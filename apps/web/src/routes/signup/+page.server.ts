import { redirect } from "@sveltejs/kit";
import { safeRedirectTo } from "../login/auth-params";
import { loadAuthPage } from "../login/auth-options.server";
import type { PageServerLoad } from "./$types";

// Same contract as /login: a signed-in visitor goes where they were headed, and
// the page gets the sanitised redirect, the configured providers and whether
// back is reachable.
export const load: PageServerLoad = async ({ locals, url, fetch }) => {
  if (locals.user)
    redirect(303, safeRedirectTo(url.searchParams.get("redirectTo")));
  return loadAuthPage(fetch, url, locals.backendDown);
};
