/**
 * The e-stop's state — ONE press, shared by every StopButton in the console.
 *
 * Module-level on purpose. /vr-control keeps its own StopButton inside the
 * WebXR dom-overlay, because while the headset is on that overlay is the only
 * part of the page the operator can see (5c8270b) — so there are two buttons on
 * one screen, and they used to keep two `outcome`s: pressing one left the other
 * at "Parar". A press is a fact about the robot, not about a component.
 *
 * Module state is normally a hazard under SSR (it is shared between requests),
 * which is why nothing here moves on the server: the phase only changes from a
 * click or the hotkey, and telemetry is connected from the protected layout's
 * `onMount`. During SSR every reader sees `idle`.
 *
 * The decisions are in `estop.ts` (plain, tested). This file holds the reactive
 * fields, the request, and the timers.
 */

import { untrack } from "svelte";
import { createApi } from "$lib/api";
import type { SkillOutcome } from "./skill-outcome";
import {
  advance,
  classifyStopResponse,
  STILL_MOVING_MS,
  STOP_REQUEST_TIMEOUT_MS,
  STOPPED_CLEAR_MS,
  type EstopPhase,
  type Telemetry,
} from "./estop";

class EStop {
  phase = $state<EstopPhase>("idle");
  /** The classified answer to the last press, for the bridge's own words. */
  outcome = $state.raw<SkillOutcome | null>(null);

  #acceptedAt: number | null = null;
  /** Bumped per press, so a late answer to an old press cannot overwrite a new one. */
  #seq = 0;
  #source: (() => Telemetry) | null = null;
  #refresh: (() => void) | null = null;
  #clearTimer: ReturnType<typeof setTimeout> | null = null;
  #recheckTimer: ReturnType<typeof setTimeout> | null = null;

  get busy(): boolean {
    return this.phase === "sending";
  }

  /**
   * Feed the store live telemetry. Called once, from the protected layout's
   * `onMount`; returns the teardown.
   *
   * `refresh` asks for a reading now: after a 200 the next scheduled poll can be
   * up to 2 s away, and "Parando…" should not wait on a timer to become
   * "Detenido".
   */
  connect(source: () => Telemetry, refresh?: () => void): () => void {
    this.#source = source;
    this.#refresh = refresh ?? null;
    const stop = $effect.root(() => {
      $effect(() => {
        const t = source();
        // Only telemetry is a dependency; the phase is read and written inside.
        untrack(() => this.#observe(t));
      });
    });
    return () => {
      stop();
      if (this.#source === source) {
        this.#source = null;
        this.#refresh = null;
      }
    };
  }

  /** Send the stop. Idempotent on the bridge, so a press while one is in flight is dropped. */
  async press(): Promise<void> {
    if (this.busy) return;
    const seq = ++this.#seq;
    this.#clearTimers();
    this.#acceptedAt = null;
    this.outcome = null;
    this.phase = "sending";

    const signal = AbortSignal.timeout(STOP_REQUEST_TIMEOUT_MS);
    let result: ReturnType<typeof classifyStopResponse>;
    try {
      const res = await createApi(fetch)
        .skills({ name: "stop_everything" })
        .invoke.post({}, { fetch: { signal } });
      result = classifyStopResponse(res);
    } catch {
      result = classifyStopResponse(
        signal.aborted ? { timedOut: true } : { threw: true },
      );
    }
    // Eden reports a fetch that the timeout aborted as an error object rather
    // than a throw on some paths; either way it is "no answer", not "no bridge".
    if (signal.aborted && result.phase !== "accepted") {
      result = classifyStopResponse({ timedOut: true });
    }
    if (seq !== this.#seq) return;

    this.outcome = result.outcome;
    this.phase = result.phase;
    if (result.phase === "accepted") {
      this.#acceptedAt = Date.now();
      this.#refresh?.();
      // Re-judge once the grace period is over even if no poll lands exactly then.
      this.#recheckTimer = setTimeout(
        () => this.#observeNow(),
        STILL_MOVING_MS + 50,
      );
      this.#observeNow();
    }
    // A 401 is a session problem. The poller sends the console to /login on its
    // own next 401; this store survives that client-side navigation, so the login
    // page can still say that the last stop did not go out.
  }

  /** Put the button back to rest. For the banner's "Descartar". */
  dismiss(): void {
    this.#seq++;
    this.#clearTimers();
    this.#acceptedAt = null;
    this.outcome = null;
    this.phase = "idle";
  }

  #observeNow(): void {
    if (this.#source) this.#observe(this.#source());
  }

  #observe(t: Telemetry): void {
    const next = advance(this.phase, this.#acceptedAt, t, Date.now());
    if (next === this.phase) return;
    this.phase = next;
    if (next === "stopped") {
      // The only state that clears itself: a stop telemetry has confirmed.
      // Everything else waits for the operator — a timeout is not an answer.
      const seq = this.#seq;
      if (this.#clearTimer) clearTimeout(this.#clearTimer);
      this.#clearTimer = setTimeout(() => {
        if (seq === this.#seq && this.phase === "stopped") this.dismiss();
      }, STOPPED_CLEAR_MS);
    }
  }

  #clearTimers(): void {
    if (this.#clearTimer) clearTimeout(this.#clearTimer);
    if (this.#recheckTimer) clearTimeout(this.#recheckTimer);
    this.#clearTimer = null;
    this.#recheckTimer = null;
  }
}

export const estop = new EStop();
