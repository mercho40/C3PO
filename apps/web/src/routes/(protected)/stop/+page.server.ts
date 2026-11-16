import { createApi } from "#lib/api.js";
import {
  classifyStopResponse,
  STOP_REQUEST_TIMEOUT_MS,
} from "#lib/robot/estop.js";
import type { Actions } from "./$types";

/**
 * PARAR without JavaScript.
 *
 * StopButton sits in a `<form method="POST" action="/stop">`, so a press that
 * lands before the console hydrated (or with JS off) still sends the stop: here,
 * through the same /skills/stop_everything/invoke the e-stop store calls, and
 * classified by the same rules. A hydrated click calls preventDefault and never
 * reaches this action.
 *
 * Actions run BEFORE the protected layout's load, so its `locals.user` redirect
 * does not guard this. back's session guard is the authority: without a session
 * it answers 401, which classifies as "expired". The cookie is forwarded
 * explicitly, as in the layout load (see `#lib/api`).
 */
export const actions: Actions = {
  default: async ({ fetch, request }) => {
    const signal = AbortSignal.timeout(STOP_REQUEST_TIMEOUT_MS);
    const res = await createApi(fetch, request.headers.get("cookie"))
      .skills({ name: "stop_everything" })
      .invoke.post({}, { fetch: { signal } })
      .catch(() => (signal.aborted ? { timedOut: true } : { threw: true }));
    let result = classifyStopResponse(res);
    // As in the store: Eden reports a fetch the timeout aborted as an error
    // object, which would read "never left". The order may have landed.
    if (signal.aborted && result.phase !== "accepted") {
      result = classifyStopResponse({ timedOut: true });
    }
    return { phase: result.phase, code: result.outcome?.code ?? null };
  },
};
