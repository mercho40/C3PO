/**
 * Polls the PARAR latch for the shell's «Reanudar» chip. See stop-latch.ts.
 *
 * Eden on both: /safety/status is typed by back's contract-derived schema, and
 * /safety/ack by the contract's StopAcknowledged | StopAckRefused (unchecked
 * on back, it is on the stop path). The e-stop's PRESS is not here: that is
 * estop.svelte.ts, untouched.
 */
import { createApi } from "#lib/api.js";
import { httpStatus, unreached } from "#lib/api-client.js";
import { Poller, type PollResult } from "./poller.svelte";
import { resumeError, stopStanding, type SafetyStatus } from "./stop-latch";

export class StopLatch extends Poller {
  status = $state<SafetyStatus | null>(null);
  resuming = $state(false);
  error = $state<string | null>(null);

  constructor() {
    super({ timeoutMs: 3000, pauseWhenHidden: true });
  }

  get standing(): boolean {
    return stopStanding(this.status);
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const { data, error } = await createApi(fetch).safety.status.get({
      fetch: { signal },
    });
    if (httpStatus(error) === 401) return "unauthorized";
    // Keep the last reading: a latch we cannot read is not cleared. That
    // includes back's 502 `bridge_bad_payload`, a status it could not vouch for.
    if (error || !data) return "error";
    this.status = data;
    return "ok";
  }

  async resume(): Promise<void> {
    if (this.resuming) return;
    this.resuming = true;
    this.error = null;
    try {
      // The stop we showed: the bridge refuses (409) if a newer one landed since.
      const { error } = await createApi(fetch).safety.ack.post({
        stopped_at: this.status?.stopped_at ?? null,
      });
      if (error)
        this.error = unreached(error)
          ? resumeError(502, null)
          : resumeError(error.status, error.value);
      await this.tick();
    } catch {
      this.error = resumeError(502, null);
    } finally {
      this.resuming = false;
    }
  }
}
