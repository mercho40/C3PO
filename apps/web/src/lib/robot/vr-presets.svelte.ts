/**
 * The preset tiles on /vr-control — bring-up steps and gestures — and the
 * headset menu's trigger, which dispatches through the same `run`.
 *
 * `/invoke` is admin-only for every non-safety skill (`apps/back/src/routes/
 * skills.ts`), so every preset needs `role: "admin"`.
 *
 * Moved out of `vr-control.svelte.ts` intact.
 */

import { createApi } from "#lib/api.js";
import { redirectToLogin } from "./poller.svelte";
import {
  classifyHttpError,
  classifyInvoke,
  type SkillOutcome,
} from "./skill-outcome";
import {
  SAFE_STEP,
  pruneOutcome,
  pruneOutcomes,
  stampOutcome,
  type StampedOutcome,
} from "./vr-logic";
import type { XrSession } from "./vr-xr.svelte";

/** What the presets read from the rest of the controller. */
export type PresetsHost = {
  /** The live posture token, or null while offline. Outcomes key off it. */
  posture: () => string | null;
  /** Observe-only: no tile runs (see `VrControl.viewOnly`). */
  viewOnly: () => boolean;
  /** The page has been torn down. */
  destroyed: () => boolean;
  /** The live headset session, if any: busy and results are mirrored into it. */
  headset: () => XrSession | null;
  /** The polite live region. */
  announce: (text: string) => void;
};

export class VrPresets {
  /** The preset holding the dispatch lock, and its label for "Esperando a «…»". */
  lock = $state.raw<{ key: string; label: string } | null>(null);
  /** Tiles with a request in flight (the lock holder, plus damp if it bypassed). */
  inFlight = $state.raw<Record<string, boolean>>({});
  outcomes = $state.raw<Record<string, StampedOutcome>>({});
  /** Wall clock, ticked once a second, so outcome ages ("hace 12 s") move. */
  now = $state(Date.now());

  #host: PresetsHost;

  constructor(host: PresetsHost) {
    this.#host = host;
  }

  /** Drop outcomes that no longer describe the robot (see `pruneOutcome`). */
  prune(posture: string | null): void {
    const next = pruneOutcomes(this.outcomes, this.now, posture);
    if (next !== this.outcomes) this.outcomes = next;
  }

  /** Whether a tile must refuse a tap right now, because another one holds the lock. */
  lockedOut(key: string): boolean {
    // An observer runs nothing — the gesture and bring-up tiles are reachable
    // in passthrough, where the overlay composites. PARAR is not a tile.
    if (this.#host.viewOnly()) return true;
    return this.lock !== null && this.lock.key !== key && key !== SAFE_STEP;
  }

  /**
   * The tile's last result, if it still describes the robot.
   *
   * A pure read: it applies the same rule `prune()` does against the CURRENT
   * posture, so a PARAR that drops the robot to damp hides "Listo" on the next
   * render instead of on the next clock tick. Only the 1 s clock writes state
   * (persisting the settle-window re-stamp, well inside its 5 s).
   */
  outcomeFor(key: string): StampedOutcome | null {
    const o = this.outcomes[key];
    return o ? pruneOutcome(o, this.now, this.#host.posture()) : null;
  }

  /**
   * Dispatch one preset through `/skills/:name/invoke`.
   *
   * ONE LOCK, VISIBLY. Gestures block until the motion completes (up to ~15 s
   * plus a 2 s hold before the auto-release), and this used to bail out early
   * when *any* preset was busy while only disabling the busy tile itself — so a
   * tap on any other tile did nothing at all: no disabled state, no message,
   * no outcome. Tiles now read `lockedOut()` and say which preset they are
   * waiting for. The exception is damp, the safe-state step: a running gesture
   * must never be the reason the operator cannot reach it.
   *
   * The outcome is classified from the BODY (see `classifyInvoke`): the bridge
   * never raises, so a firmware refusal is an HTTP 200 carrying `status:
   * "failed"`, and a bring-up step the firmware acked but did not honour
   * (`acked_no_transition`) is a 200 too. Reading the status code alone is how
   * a refused gesture used to render nothing and a step that provably did
   * nothing rendered "Enviado" in cyan.
   */
  async runSkill(
    key: string,
    label: string,
    skill: string,
    body: Record<string, unknown> = {},
  ): Promise<void> {
    // `lockedOut` covers observe-only too: "no mueve nada" includes the tiles.
    if (this.inFlight[key] || this.lockedOut(key)) return;
    const tookLock = this.lock === null;
    if (tookLock) this.lock = { key, label };
    this.inFlight = { ...this.inFlight, [key]: true };
    // Mirror into the headset. Without this the operator in VR presses the
    // trigger and sees nothing change — no spinner, no result, no way to tell
    // a dispatched gesture from a swallowed press.
    this.#host.headset()?.setMenuBusy(key);
    this.#host.headset()?.setMenuStatus(null);
    if (this.outcomes[key]) {
      const { [key]: _drop, ...rest } = this.outcomes;
      this.outcomes = rest;
    }

    let outcome: SkillOutcome;
    try {
      const res = await createApi(fetch)
        .skills({ name: skill })
        .invoke.post(body);
      if (res.error && (res.error.status as number) === 401) {
        redirectToLogin("expired");
        return;
      }
      outcome = classifyInvoke(res);
    } catch (err) {
      outcome = {
        ...classifyHttpError(null, null),
        detail: err instanceof Error ? err.message : null,
      };
    } finally {
      const { [key]: _done, ...rest } = this.inFlight;
      this.inFlight = rest;
      if (tookLock && this.lock?.key === key) this.lock = null;
      // Damp bypasses the lock, so it can finish while a gesture is still in
      // flight: show that gesture as busy again rather than clearing it.
      this.#host.headset()?.setMenuBusy(this.lock?.key ?? null);
    }
    if (this.#host.destroyed()) return;
    // Whatever the result was goes to the headset verbatim: "Enviado" and
    // "Enviado (sin confirmar)" mean very different things about a robot the
    // wearer cannot see.
    this.#host.headset()?.setMenuStatus({
      text: outcome.text,
      kind: outcome.kind === "unverified" ? "warn" : outcome.kind,
    });
    this.outcomes = {
      ...this.outcomes,
      [key]: stampOutcome(outcome, Date.now(), this.#host.posture()),
    };
    this.now = Date.now();
    this.#host.announce(`${label}: ${outcome.text}`);
  }
}
