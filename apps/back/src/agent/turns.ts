/**
 * Every agent turn in flight, typed or voice. PARAR aborts all of them
 * (`abortAllTurns`, from the stop_everything dispatch, the voice stop phrase,
 * and stop-watch.ts for a stop latched on the bridge directly): stopping the robot without stopping the model left its next step
 * free to move it again. The bridge refuses that step anyway while the stop
 * stands; this also ends the planning, so nobody is answered by a model still
 * at work. No imports on purpose: voice/loop.ts stays dependency-free.
 */
const activeTurns = new Set<AbortController>();
let onFirstTurn: (() => void) | null = null;

/** Called when the first turn begins (none → one): stop-watch.ts starts polling. */
export function setOnFirstTurn(fn: (() => void) | null): void {
  onFirstTurn = fn;
}

/** Register a turn. Call `done` when it ends; `signal` aborts on PARAR. */
export function beginTurn(outer?: AbortSignal): {
  signal: AbortSignal;
  done: () => void;
} {
  const turn = new AbortController();
  activeTurns.add(turn);
  if (activeTurns.size === 1) onFirstTurn?.();
  return {
    signal: outer ? AbortSignal.any([outer, turn.signal]) : turn.signal,
    done: () => void activeTurns.delete(turn),
  };
}

export function abortAllTurns(reason = "PARAR"): number {
  const n = activeTurns.size;
  for (const turn of activeTurns) turn.abort(new Error(reason));
  activeTurns.clear();
  return n;
}

export function activeTurnCount(): number {
  return activeTurns.size;
}
