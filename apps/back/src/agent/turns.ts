/**
 * Every agent turn in flight, typed or voice. PARAR aborts all of them
 * (`abortAllTurns`, from the stop_everything dispatch and the voice stop
 * phrase): stopping the robot without stopping the model left its next step
 * free to move it again. The bridge refuses that step anyway while the stop
 * stands; this also ends the planning, so nobody is answered by a model still
 * at work. No imports on purpose: voice/loop.ts stays dependency-free.
 */
const activeTurns = new Set<AbortController>();

/** Register a turn. Call `done` when it ends; `signal` aborts on PARAR. */
export function beginTurn(outer?: AbortSignal): {
  signal: AbortSignal;
  done: () => void;
} {
  const turn = new AbortController();
  activeTurns.add(turn);
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
