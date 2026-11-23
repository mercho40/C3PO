/**
 * Every agent turn in flight, typed or voice. PARAR aborts all of them
 * (`abortAll`, from the stop_everything dispatch, the voice stop phrase,
 * and stop-watch.ts for a stop latched on the bridge directly): stopping the
 * robot without stopping the model left its next step free to move it again.
 * The bridge refuses that step anyway while the stop stands; this also ends the
 * planning, so nobody is answered by a model still at work. It also drops every
 * confirmation card asked before the stop: the bridge mints a fresh grant on
 * «Confirmar», so a card from before PARAR would still run after «Reanudar».
 *
 * One instance per process (`createTurns`, built by createApp), handed the
 * confirmation store it clears, so a PARAR cannot forget the cards. Its only
 * dependency is that store's `clear`, so voice/loop.ts stays dependency-free.
 */
import type { Confirmations } from "./confirmations";

export function createTurns(confirmations: Pick<Confirmations, "clear">) {
  const activeTurns = new Set<AbortController>();
  // Called when the first turn begins (none → one): stop-watch.ts starts
  // polling. A Set, so a second watcher (a test's) does not replace the first.
  const firstTurnListeners = new Set<() => void>();
  let voiceHolds = 0;

  /** Hear none → one. Returns the unsubscribe. */
  function onFirstTurn(fn: () => void): () => void {
    firstTurnListeners.add(fn);
    return () => void firstTurnListeners.delete(fn);
  }

  /** Register a turn. Call `done` when it ends; `signal` aborts on PARAR. */
  function beginTurn(outer?: AbortSignal): {
    signal: AbortSignal;
    done: () => void;
  } {
    const turn = new AbortController();
    activeTurns.add(turn);
    if (activeTurns.size === 1) for (const fn of firstTurnListeners) fn();
    return {
      signal: outer ? AbortSignal.any([outer, turn.signal]) : turn.signal,
      done: () => void activeTurns.delete(turn),
    };
  }

  function abortAll(reason = "PARAR"): number {
    const n = activeTurns.size;
    for (const turn of activeTurns) turn.abort(new Error(reason));
    activeTurns.clear();
    confirmations.clear();
    return n;
  }

  function activeCount(): number {
    return activeTurns.size;
  }

  /**
   * An operator's /agent turn holds the voice loop from its start until the
   * robot calls it sent have settled: meanwhile the loop answers no overheard
   * speech (2026-11-16, mid-pick, it answered a passer-by over the pick's own
   * speech). Not a turn: PARAR does not end it, and the loop's stop phrase
   * ignores it. Returns the release; calling it twice is harmless.
   */
  function holdVoiceLoop(): () => void {
    voiceHolds += 1;
    let held = true;
    return () => {
      if (held) voiceHolds -= 1;
      held = false;
    };
  }

  function voiceLoopHeld(): boolean {
    return voiceHolds > 0;
  }

  return {
    onFirstTurn,
    beginTurn,
    abortAll,
    activeCount,
    holdVoiceLoop,
    voiceLoopHeld,
  };
}

export type Turns = ReturnType<typeof createTurns>;
