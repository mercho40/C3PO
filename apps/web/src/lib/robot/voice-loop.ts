/**
 * Talking to `back` about the voice loop. No runes — see the note below.
 *
 *   GET  /voice/status  ->  is it running, and what has it done
 *   POST /voice/start   ->  heard speech starts reaching the agent
 *   POST /voice/stop    ->  the robot keeps listening; nothing acts
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

/** The loop's own snapshot. Mirrors `VoiceLoopState` in apps/back. */
export type VoiceLoopState = {
  running: boolean;
  utterancesHeard: number;
  agentRuns: number;
  stopsTriggered: number;
  micEverOpen: boolean;
  alwaysListening: boolean;
  lastError: string | null;
  lastHeard: string | null;
};

/** Either the loop's state, or why there isn't one — never both, never neither. */
export type VoiceResult =
  | { ok: true; state: VoiceLoopState }
  | { ok: false; reason: string };

async function ask(
  url: string,
  init: RequestInit,
  what: string,
): Promise<VoiceResult> {
  let res: Response;
  try {
    res = await fetch(url, { credentials: "include", ...init });
  } catch {
    // A dead `back` and a refusing `back` are different problems with different
    // fixes, and the operator should not have to guess which they have.
    return { ok: false, reason: "back is unreachable" };
  }
  if (res.ok) {
    try {
      return { ok: true, state: (await res.json()) as VoiceLoopState };
    } catch {
      return { ok: false, reason: "back sent something that is not a status" };
    }
  }
  if (res.status === 401) return { ok: false, reason: "not signed in" };
  return { ok: false, reason: `could not ${what} the loop (${res.status})` };
}

export function voiceStatus(base: string): Promise<VoiceResult> {
  return ask(`${base}/voice/status`, {}, "read");
}

export function voiceCommand(
  base: string,
  which: "start" | "stop",
): Promise<VoiceResult> {
  return ask(`${base}/voice/${which}`, { method: "POST" }, which);
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
