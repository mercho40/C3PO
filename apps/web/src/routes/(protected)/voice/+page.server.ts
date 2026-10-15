import { redirect } from "@sveltejs/kit";
import { createApi } from "#lib/api.js";
import type { PageServerLoad } from "./$types";

/**
 * /voice — past voice transcripts, and (for admins) the `say` skill's schema.
 *
 * Both server-side so the first paint already has them, with the cookie
 * forwarded explicitly: SvelteKit's server `fetch` does not send cookies
 * cross-origin (web :3001 → back :3000), so Better Auth would not see the
 * session otherwise (see `#lib/api`).
 *
 * Failures are reported, never flattened into an empty list: "you have no
 * voice transcripts" and "the history could not be read" are different facts.
 *
 * EDEN NEVER THROWS FOR A FAILED FETCH. A refused connection or the timeout
 * below resolves as `{error: {status: 503, value: <the thrown Error>}}`, so the
 * `.catch()` these calls used to rely on never fired: a dead back was reported
 * as "HTTP 503", which names a server that never answered. An Error value is
 * the network; a JSON body is back. (`.catch` stays as a backstop for a body
 * Eden could not decode.)
 *
 * Bounded at 3 s each. GET /skills/say reaches the bridge for the catalogue,
 * and a stalled bridge must not hang the page — every sidebar click to /voice
 * would wait on it.
 */

export type VoiceTranscript = {
  id: string;
  title: string | null;
  createdAt: string;
  updatedAt: string;
};

/**
 * How many voice transcripts are listed. GET /chats filters by channel
 * server-side, so an empty answer really is "none"; a full one means older
 * transcripts exist beyond this list, and the page says so.
 */
const SCAN = 50;

const iso = (d: string | Date) => new Date(d).toISOString();

type EdenError = { status: unknown; value: unknown } | null | undefined;

/** Eden's network shape: an Error where a body would be. */
function networkFailure(error: EdenError): "timeout" | "network" | null {
  if (!error || !(error.value instanceof Error)) return null;
  return error.value.name === "TimeoutError" ? "timeout" : "network";
}

const NO_API = "No se pudo contactar al servidor (apps/back).";
const TOO_SLOW = "El servidor no contestó a tiempo (3 s).";

export const load: PageServerLoad = async ({ fetch, request, locals, url }) => {
  const api = createApi(fetch, request.headers.get("cookie"));
  const isAdmin = locals.user?.role === "admin";

  const [chatsRes, sayRes] = await Promise.all([
    api.chats
      .get({
        query: { limit: SCAN, channel: "voice" },
        fetch: { signal: AbortSignal.timeout(3000) },
      })
      .catch(() => null),
    isAdmin
      ? api
          .skills({ name: "say" })
          .get({ fetch: { signal: AbortSignal.timeout(3000) } })
          .catch(() => null)
      : Promise.resolve(null),
  ]);

  // A 401 from either read is the session, never the robot or the history.
  if (
    (chatsRes?.error?.status as number | undefined) === 401 ||
    (sayRes?.error?.status as number | undefined) === 401
  ) {
    const params = new URLSearchParams({
      redirectTo: url.pathname + url.search,
      reason: "expired",
    });
    redirect(303, `/login?${params}`);
  }

  let transcripts: VoiceTranscript[] | null = null;
  let transcriptsError: string | null = null;
  /** True when the scan hit its cap: older voice chats may exist unseen. */
  let transcriptsCapped = false;
  const chatsNet = networkFailure(chatsRes?.error);
  if (!chatsRes || chatsNet) {
    transcriptsError = chatsNet === "timeout" ? TOO_SLOW : NO_API;
  } else if (chatsRes.error || !chatsRes.data) {
    transcriptsError = `El servidor no devolvió el historial (HTTP ${chatsRes.error?.status ?? "?"}).`;
  } else {
    const chats = chatsRes.data.chats;
    transcriptsCapped = chats.length >= SCAN;
    // Back lists the caller's chats most recently updated first; keep that
    // order — a transcript still being written is the one worth seeing first.
    transcripts = chats.map((c) => ({
      id: c.id,
      title: c.title,
      createdAt: iso(c.createdAt),
      updatedAt: iso(c.updatedAt),
    }));
  }

  // The say skill's parameter schema, so the "Decir" box enforces the bridge's
  // own limits. `sayError` distinguishes "not in the catalogue" from "could
  // not ask".
  let sayParameters: unknown = null;
  let sayError: string | null = null;
  if (isAdmin) {
    const sayNet = networkFailure(sayRes?.error);
    if (!sayRes || sayNet) {
      sayError =
        sayNet === "timeout"
          ? "No se pudo leer el catálogo: el servidor no contestó a tiempo (3 s)."
          : `No se pudo leer el catálogo. ${NO_API}`;
    } else if (sayRes.error) {
      sayError =
        (sayRes.error.status as number) === 404
          ? "El catálogo del puente no tiene la habilidad «say» (¿puente caído sin catálogo guardado?)."
          : `No se pudo leer la habilidad «say» (HTTP ${sayRes.error.status}).`;
    } else {
      sayParameters =
        (sayRes.data as { parameters?: unknown }).parameters ?? null;
    }
  }

  return {
    transcripts,
    transcriptsError,
    transcriptsCapped,
    transcriptsScanned: SCAN,
    sayParameters,
    sayError,
  };
};
