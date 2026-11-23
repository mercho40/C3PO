import { treaty } from "@elysiajs/eden";
import type { App } from "@repo/back";

/**
 * The Eden Treaty client for `back` at `base` — what `createApi` (api.ts) builds
 * for PUBLIC_API_URL, for the plain modules that take back's address as an
 * argument instead (voice-loop.ts, admin/faces.ts, chat/confirm.ts) so `bun
 * test` can load them without `$app/env/public` and point them at a fake.
 *
 * `fetcher` is read when the client is built: a test that swaps
 * `globalThis.fetch` is seen by the next call.
 *
 * What Eden does that a plain `fetch` did not, worth knowing at every call site:
 *  - it never throws for a failed request: no answer at all (refused, aborted,
 *    timed out) comes back as `{status: 503, error: {value: <the Error>}}` —
 *    `unreached()` below — so an `Error` value means "could not reach back",
 *    never a bridge 503, which carries a JSON body;
 *  - it DOES throw when a JSON body cannot be parsed;
 *  - strings that look like dates arrive as `Date`s.
 */
export const apiAt = (
  base: string,
  fetcher: typeof globalThis.fetch = globalThis.fetch,
  cookie?: string | null,
) =>
  treaty<App>(base, {
    fetcher,
    fetch: { credentials: "include" },
    headers: cookie ? { cookie } : undefined,
  });

export type Api = ReturnType<typeof apiAt>;

/** True when Eden got no answer at all (see `apiAt`). */
export function unreached(error: { value: unknown } | null | undefined) {
  return error?.value instanceof Error;
}

/**
 * The status of an Eden error as a plain number. Eden types only the statuses
 * a route declares, and the session guard's 401 is not declared per route —
 * comparing the typed status with 401 does not compile.
 */
export function httpStatus(
  error: { status: unknown } | null | undefined,
): number | null {
  return typeof error?.status === "number" ? error.status : null;
}
