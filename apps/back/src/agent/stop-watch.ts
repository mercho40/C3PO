/**
 * A PARAR that never passes through this server still ends its agent turns.
 *
 * The headset socket and the bridge's own stop phrase latch the stop on the
 * bridge directly; only stops sent through apps/back called `abortAllTurns`.
 * The bridge refuses the turn's next motion step either way, but the model kept
 * planning and answering. While any turn is in flight this polls the bridge's
 * latch and aborts every turn when its `stopped_at` moves.
 *
 * Change, not comparison: `stopped_at` is the robot's wall clock and apps/back
 * may run on another machine, so a newer stamp than the one seen when the turns
 * began is the signal, whatever the two clocks say.
 */
import { bridgeSiblingUrl } from "../bridge/url";
import { abortAllTurns, activeTurnCount, setOnFirstTurn } from "./turns";

export const POLL_MS = 500;

/** The latch's stamp (0 when the bridge never stopped), or null if unreadable. */
export async function readStoppedAt(): Promise<number | null> {
  try {
    const res = await fetch(bridgeSiblingUrl("/safety/status"), {
      signal: AbortSignal.timeout(1000),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { stopped_at?: unknown };
    return typeof body.stopped_at === "number" ? body.stopped_at : 0;
  } catch {
    return null;
  }
}

/** One watch: runs while any turn is active, then returns. */
export async function watchWhileTurnsRun(
  read: () => Promise<number | null> = readStoppedAt,
  intervalMs = POLL_MS,
): Promise<void> {
  // An unreadable bridge gives no baseline yet; the first good read sets it.
  let seen = await read();
  while (activeTurnCount() > 0) {
    await new Promise((r) => setTimeout(r, intervalMs));
    if (activeTurnCount() === 0) break;
    const now = await read();
    if (now === null) continue;
    if (seen !== null && now > seen) {
      const n = abortAllTurns("PARAR (bridge latch)");
      console.warn(`[stop-watch] bridge stop at ${now}: aborted ${n} turn(s)`);
    }
    seen = now;
  }
}

let running = false;

function run(read?: () => Promise<number | null>, intervalMs?: number): void {
  if (running) return;
  running = true;
  void watchWhileTurnsRun(read, intervalMs).finally(() => {
    running = false;
    // A turn that began while the last poll was finishing still gets a watch.
    if (activeTurnCount() > 0) run(read, intervalMs);
  });
}

/** Install the hook: the first turn of an idle server starts one watch. */
export function startStopWatch(
  read?: () => Promise<number | null>,
  intervalMs?: number,
): void {
  setOnFirstTurn(() => run(read, intervalMs));
}
