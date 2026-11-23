/**
 * Talking to `back` about the voice loop. No runes — see the note below.
 *
 *   GET  /voice/status  ->  is it running, and what has it done
 *   POST /voice/start   ->  begin a spoken conversation session, on an engine
 *   POST /voice/stop    ->  the robot keeps listening; no turns are processed
 *
 * WHY THIS IS SPLIT FROM `voice-loop.svelte.ts`. A `.svelte.ts` module cannot be
 * loaded by `bun test` — runes are a compiler feature, so `$state` is simply not
 * defined outside a Svelte build. That is why `awareness.svelte.ts`, which has
 * the same polling and the same failure modes, has no tests at all. Everything
 * that decides what the operator is told lives here, in a plain module, and the
 * rune class is left holding reactive fields and a timer.
 *
 * THE PROPERTY THIS FILE EXISTS TO PROTECT: the console must never report the
 * loop as running because a button was pressed. `back`'s reply is the state.
 */

import type { VoiceStatus } from "@repo/back";
import { apiAt, unreached } from "#lib/api-client.js";
import { ADMIN_REQUIRED_HINT } from "#lib/chat/chat-error.js";

/**
 * The loop's own snapshot: back's `VoiceStatus` (voice/service.ts), not a
 * copy — but for the engine choice, optional here because a back that
 * predates it sends neither field, and voice-view.ts falls back on `engine`.
 */
export type VoiceLoopState = Omit<
  VoiceStatus,
  "defaultEngine" | "availableEngines"
> &
  Partial<Pick<VoiceStatus, "defaultEngine" | "availableEngines">>;

/** Engine names as `back` speaks them. The console's own are in `voice-view.ts`. */
export type WireEngine = VoiceStatus["engine"];

/**
 * Either the loop's state, or why there isn't one.
 *
 * A failure may still carry a `state`: a 409 from /voice/start means another
 * operator owns the session, and back's body IS the current status — dropping
 * it left the console unable to say who had the microphone.
 */
export type VoiceResult =
  | { ok: true; state: VoiceLoopState }
  | {
      ok: false;
      reason: string;
      status: number | null;
      state?: VoiceLoopState;
    };

function looksLikeState(body: unknown): body is VoiceLoopState {
  return (
    body !== null &&
    typeof body === "object" &&
    typeof (body as { running?: unknown }).running === "boolean"
  );
}

/** What every Eden call answers, as far as `ask` reads it. */
type Reply = {
  data: unknown;
  error: { status: unknown; value: unknown } | null;
  status: number;
};

async function ask(
  call: () => Promise<Reply>,
  signal: AbortSignal | undefined,
): Promise<VoiceResult> {
  let res: Reply;
  try {
    res = await call();
  } catch {
    // Eden throws only for a JSON body it could not parse.
    return {
      ok: false,
      status: null,
      reason: "El servidor respondió algo que no es un estado de voz.",
    };
  }
  if (unreached(res.error)) {
    // A dead `back` and a refusing `back` are different problems with different
    // fixes, and the operator should not have to guess which they have.
    if (signal?.aborted) {
      return {
        ok: false,
        status: null,
        reason:
          "El servidor no respondió a tiempo. Revisá el estado antes de volver a intentar.",
      };
    }
    return {
      ok: false,
      status: null,
      reason: "No se pudo contactar al servidor (apps/back).",
    };
  }
  const body: unknown = res.error ? res.error.value : res.data;
  if (!res.error) {
    if (looksLikeState(body)) return { ok: true, state: body };
    return {
      ok: false,
      status: res.status,
      reason: "El servidor respondió algo que no es un estado de voz.",
    };
  }
  if (res.status === 401) {
    return { ok: false, status: 401, reason: "Sesión vencida." };
  }
  if (res.status === 403) {
    return { ok: false, status: 403, reason: ADMIN_REQUIRED_HINT };
  }
  if (res.status === 409) {
    const otherEngine =
      body !== null &&
      typeof body === "object" &&
      (body as { error?: unknown }).error === "other_engine_running";
    return {
      ok: false,
      status: 409,
      reason: otherEngine
        ? "Ya hay una conversación con el otro motor. Terminala antes de cambiar de motor."
        : "Otro operador tiene la conversación de voz.",
      ...(looksLikeState(body) ? { state: body } : {}),
    };
  }
  const serverError =
    body &&
    typeof body === "object" &&
    typeof (body as { error?: unknown }).error === "string"
      ? (body as { error: string }).error
      : null;
  return {
    ok: false,
    status: res.status,
    reason: serverError
      ? `El servidor rechazó el pedido: ${serverError}`
      : `El servidor rechazó el pedido (HTTP ${res.status}).`,
    ...(looksLikeState(body) ? { state: body } : {}),
  };
}

export function voiceStatus(
  base: string,
  signal?: AbortSignal,
): Promise<VoiceResult> {
  return ask(() => apiAt(base).voice.status.get({ fetch: { signal } }), signal);
}

/**
 * Starting a realtime session waits on the provider's handshake, so the bound
 * is generous — but there is one: a hung back used to leave the switch on
 * "Iniciando…" with the button locked, indefinitely.
 */
export const VOICE_COMMAND_TIMEOUT_MS = 20_000;

/**
 * `engine` is the operator's choice for a start; omitted, back uses its
 * default. Stop needs none — it ends whichever engine is running.
 */
export function voiceCommand(
  base: string,
  which: "start" | "stop",
  opts: { engine?: WireEngine; timeoutMs?: number } = {},
): Promise<VoiceResult> {
  const signal = AbortSignal.timeout(
    opts.timeoutMs ?? VOICE_COMMAND_TIMEOUT_MS,
  );
  const voice = apiAt(base).voice;
  return ask(
    () =>
      which === "start"
        ? voice.start.post(opts.engine ? { engine: opts.engine } : undefined, {
            fetch: { signal },
          })
        : voice.stop.post(undefined, { fetch: { signal } }),
    signal,
  );
}

/**
 * Whether the robot can actually hear.
 *
 * "Running" is not "hearing": with a push-to-talk microphone, silence usually
 * means nobody held the button rather than that nobody spoke. A loop running
 * over a mic that has never opened has done nothing and will do nothing, and
 * saying so is more use to an operator than a green light.
 */
export function canHear(state: VoiceLoopState | null): boolean {
  return state?.micEverOpen === true;
}

export function isRunning(state: VoiceLoopState | null): boolean {
  return state?.running === true;
}

/**
 * Whether this operator may end the running session. A realtime session can
 * only be ended by its owner; the legacy loop by anyone signed in.
 */
export function canControl(state: VoiceLoopState | null): boolean {
  if (!state?.running) return true;
  return state.ownedByYou !== false;
}
