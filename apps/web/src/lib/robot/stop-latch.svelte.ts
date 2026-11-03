/**
 * Polls the PARAR latch for the shell's «Reanudar» chip. See stop-latch.ts.
 */
import { PUBLIC_API_URL } from "$app/env/public";
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
    try {
      const res = await fetch(`${PUBLIC_API_URL}/safety/status`, {
        credentials: "include",
        signal,
      });
      if (res.status === 401) return "unauthorized";
      if (!res.ok) return "error"; // keep the last reading: a latch we cannot read is not cleared
      this.status = (await res.json()) as SafetyStatus;
      return "ok";
    } catch {
      return "error";
    }
  }

  async resume(): Promise<void> {
    if (this.resuming) return;
    this.resuming = true;
    this.error = null;
    try {
      const res = await fetch(`${PUBLIC_API_URL}/safety/ack`, {
        method: "POST",
        credentials: "include",
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) this.error = resumeError(res.status, body);
      await this.tick();
    } catch {
      this.error = resumeError(502, null);
    } finally {
      this.resuming = false;
    }
  }
}
