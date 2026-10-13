import { createApi } from "$lib/api";
import {
  oauthErrorMessage,
  readProviders,
  safeRedirectTo,
  type SocialProvider,
} from "./auth-params";

export type AuthPageData = {
  /** Sanitised: a same-origin path, never the auth pages themselves. */
  redirectTo: string;
  /** The protected layout or a poller sent the operator here on a 401. */
  expired: boolean;
  /** Only providers back has credentials for — empty means no social buttons. */
  providers: SocialProvider[];
  /** Spanish text for an OAuth failure Better Auth bounced back here. */
  oauthError: string | null;
  /** back could not be reached (by the session hook or by /health just now). */
  backendDown: boolean;
};

/**
 * Everything /login and /signup need before rendering.
 *
 * `/health` is public and cheap; it also doubles as the "is back up at all"
 * probe for a visitor with no session cookie, whom the session hook never
 * checks. Bounded at 1.5 s: a slow backend must not make the sign-in page slow
 * to say that it is slow.
 */
export async function loadAuthPage(
  fetch: typeof globalThis.fetch,
  url: URL,
  backendDown: boolean,
): Promise<AuthPageData> {
  const { data, error } = await createApi(fetch)
    .health.get({ fetch: { signal: AbortSignal.timeout(1500) } })
    // Eden turns a thrown fetch into an error object already; this is belt and
    // braces so a sign-in page can never 500 over a monitoring endpoint.
    .catch(() => ({ data: null, error: { status: 503 } }));
  return {
    redirectTo: safeRedirectTo(url.searchParams.get("redirectTo")),
    expired: url.searchParams.get("reason") === "expired",
    providers: error ? [] : readProviders(data),
    oauthError: oauthErrorMessage(url.searchParams.get("error")),
    backendDown: backendDown || Boolean(error),
  };
}
