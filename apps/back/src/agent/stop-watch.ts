/**
 * A PARAR that never passes through this server still ends its agent turns.
 *
 * The headset socket and the bridge's own stop phrase latch the stop on the
 * bridge directly; only stops sent through apps/back called `abortAll`. The
 * bridge refuses the turn's next motion step either way, but the model kept
 * planning and answering. While any turn is in flight this polls the bridge's
 * latch and aborts every turn when its `stopped_at` moves.
 *
 * Change, not comparison: `stopped_at` is the robot's wall clock and apps/back
 * may run on another machine, so a newer stamp than the one seen when the turns
 * began is the signal, whatever the two clocks say.
 */
import type { BridgeHttp } from "#back/bridge/http.js";
import type { Turns } from "./turns";

export const POLL_MS = 500;

/** The latch's stamp (0 when the bridge never stopped), or null if unreadable. */
export async function readStoppedAt(
  http: Pick<BridgeHttp, "safetyStatus">,
): Promise<number | null> {
  try {
    const res = await http.safetyStatus(1000);
    if (!res.ok || res.body === undefined) return null;
    return typeof res.body.stopped_at === "number" ? res.body.stopped_at : 0;
  } catch {
    return null;
  }
}

/** One watch: runs while any turn is active, then returns. */
export async function watchWhileTurnsRun(
  turns: Pick<Turns, "activeCount" | "abortAll">,
  read: () => Promise<number | null>,
  intervalMs = POLL_MS,
): Promise<void> {
  // An unreadable bridge gives no baseline yet; the first good read sets it.
  let seen = await read();
  while (turns.activeCount() > 0) {
    await new Promise((r) => setTimeout(r, intervalMs));
    if (turns.activeCount() === 0) break;
    const now = await read();
    if (now === null) continue;
    if (seen !== null && now > seen) {
      const n = turns.abortAll("PARAR (bridge latch)");
      console.warn(`[stop-watch] bridge stop at ${now}: aborted ${n} turn(s)`);
    }
    seen = now;
  }
}

/**
 * Install the hook: the first turn of an idle server starts one watch.
 * Returns the unsubscribe. index.ts starts it; createApp does not, so a test
 * app polls nothing unless it asks to.
 */
export function startStopWatch(
  turns: Pick<Turns, "activeCount" | "abortAll" | "onFirstTurn">,
  read: () => Promise<number | null>,
  intervalMs?: number,
): () => void {
  let running = false;
  function run(): void {
    if (running) return;
    running = true;
    void watchWhileTurnsRun(turns, read, intervalMs).finally(() => {
      running = false;
      // A turn that began while the last poll was finishing still gets a watch.
      if (turns.activeCount() > 0) run();
    });
  }
  return turns.onFirstTurn(run);
}
