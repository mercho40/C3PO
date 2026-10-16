/**
 * One polling policy for every live panel in the console.
 *
 * There used to be six pollers with six policies. /state had no in-flight guard
 * and no timeout, so a bridge that STALLED (rather than refused) kept the topbar
 * saying "En línea" over a frozen state for up to the MCP SDK's 60 s default,
 * while ~30 overlapping requests resolved out of order. Each poller also mapped a
 * 401 differently — a redirect, "Offline", "bridge unreachable", "not signed in",
 * "map unavailable (401)" — so an expired session told three different stories
 * on one screen, two of them about a robot that was fine.
 *
 * The rules, once:
 *  - never two requests in flight; a slow answer is a stale answer, so the next
 *    tick is skipped rather than queued;
 *  - every request carries a timeout, so "no answer" becomes a failure within
 *    a bounded time instead of an indefinitely-live reading;
 *  - a 401 is a SESSION problem, never a robot problem: the console goes to the
 *    login page and comes back to where it was;
 *  - optionally pause while the tab is hidden, and refresh on return.
 */

import { browser } from "$app/env";
import { goto } from "$app/navigation";

/** What one poll concluded. Subclasses map their response onto this. */
export type PollResult = "ok" | "error" | "unauthorized";

export type PollerOptions = {
  /** Abort a request after this long. Default 3 s. */
  timeoutMs?: number;
  /** Stop polling while the document is hidden. Default false. */
  pauseWhenHidden?: boolean;
};

let redirecting = false;

/**
 * Send the operator to log in again, and back here afterwards.
 *
 * Shared so that five pollers seeing the same 401 produce one navigation.
 */
export function redirectToLogin(reason: "expired" | null = "expired"): void {
  if (!browser || redirecting) return;
  redirecting = true;
  const here = window.location.pathname + window.location.search;
  const params = new URLSearchParams({ redirectTo: here });
  if (reason) params.set("reason", reason);
  void goto(`/login?${params}`, { refreshAll: true }).finally(() => {
    redirecting = false;
  });
}

export abstract class Poller {
  /** At least one poll has completed, successfully or not. */
  loaded = $state(false);
  /** Wall-clock ms of the last successful poll. */
  lastOkAt = $state<number | null>(null);
  /** Consecutive failed polls. 0 after any success. */
  failures = $state(0);
  /** True when the last failure was a 401. */
  unauthorized = $state(false);

  #timer: ReturnType<typeof setInterval> | null = null;
  #inFlight = false;
  /** Aborts the in-flight request on stop(): a late 401 from a page the
   *  operator already left must not redirect them, and a late body must not
   *  be written into a stopped store. */
  #abort: AbortController | null = null;
  #intervalMs = 2000;
  #timeoutMs: number;
  #pauseWhenHidden: boolean;
  #onVisibility = () => {
    if (document.visibilityState === "hidden") {
      this.#clear();
    } else if (this.#running) {
      this.#arm();
      void this.tick();
    }
  };
  #running = false;

  constructor(opts: PollerOptions = {}) {
    this.#timeoutMs = opts.timeoutMs ?? 3000;
    this.#pauseWhenHidden = opts.pauseWhenHidden ?? false;
  }

  /** One request. Use `signal` on every fetch so the timeout can abort it. */
  protected abstract poll(signal: AbortSignal): Promise<PollResult>;

  /** Called once on a 401. Default: go to the login page. */
  protected onUnauthorized(): void {
    this.stop();
    redirectToLogin("expired");
  }

  /** Run one poll now (skipped if one is already in flight). */
  async tick(): Promise<void> {
    if (this.#inFlight) return;
    this.#inFlight = true;
    const abort = new AbortController();
    this.#abort = abort;
    try {
      const result = await this.poll(
        AbortSignal.any([abort.signal, AbortSignal.timeout(this.#timeoutMs)]),
      );
      if (abort.signal.aborted) return; // stopped while in flight
      this.#record(result);
    } catch {
      if (abort.signal.aborted) return;
      // A thrown poll is a network error or the timeout firing. Both mean the
      // same thing to an operator: there is no current answer.
      this.#record("error");
    } finally {
      if (this.#abort === abort) this.#abort = null;
      if (!abort.signal.aborted) this.loaded = true;
      this.#inFlight = false;
    }
  }

  #record(result: PollResult): void {
    if (result === "ok") {
      this.lastOkAt = Date.now();
      this.failures = 0;
      this.unauthorized = false;
      return;
    }
    this.failures += 1;
    if (result === "unauthorized") {
      this.unauthorized = true;
      this.onUnauthorized();
    }
  }

  #arm(): void {
    if (this.#timer) return;
    this.#timer = setInterval(() => void this.tick(), this.#intervalMs);
  }

  #clear(): void {
    if (this.#timer) clearInterval(this.#timer);
    this.#timer = null;
  }

  /** Begin polling. No-op on the server or if already running. */
  start(intervalMs = this.#intervalMs): void {
    if (!browser || this.#running) return;
    this.#running = true;
    this.#intervalMs = intervalMs;
    if (this.#pauseWhenHidden) {
      document.addEventListener("visibilitychange", this.#onVisibility);
      if (document.visibilityState === "hidden") return;
    }
    void this.tick();
    this.#arm();
  }

  stop(): void {
    this.#running = false;
    this.#clear();
    this.#abort?.abort();
    this.#abort = null;
    if (browser && this.#pauseWhenHidden) {
      document.removeEventListener("visibilitychange", this.#onVisibility);
    }
  }
}
