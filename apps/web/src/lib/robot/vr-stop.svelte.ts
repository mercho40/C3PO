/**
 * PARAR on /vr-control, in one place: the watch on the console's shared e-stop,
 * what a press halts on this page and in what order, and the in-headset chord
 * (both grips held) that presses it.
 *
 * Moved out of `vr-control.svelte.ts` intact. The halt itself reaches into the
 * walk loop and the teleop socket, which own their state; `StopTargets` is the
 * list of what a stop touches, in the order it touches it.
 */

import { untrack } from "svelte";
import { estop } from "./estop.svelte";
import type { EstopPhase } from "./estop";
import { estopHalts, stopInFlight } from "./vr-logic";

export const STOP_CHORD_MS = 300; // both grips this long = PARAR; a relaxed hold is shorter-lived

/** What a PARAR halts, called in this order by `VrStop`. */
export type StopTargets = {
  /** Straight down the teleop socket, so PARAR works with apps/back down. */
  stopStream: () => void;
  /** Latch the walk loop and stop it (`WalkController.latchForEstop`). */
  latchWalk: () => void;
  /** Release the arm request. */
  dropArms: () => void;
  /** The stop is no longer in progress: the loop may run again, latched. */
  resume: () => void;
};

export class VrStop {
  #targets: StopTargets;
  // The PARAR chord's hold (see `applyChord`).
  #chordSince: number | null = null;
  #chordFired = false;

  constructor(targets: StopTargets) {
    this.#targets = targets;
  }

  /**
   * Follow the shared e-stop store. Returns the teardown.
   *
   * Nothing on this page used to read it. PARAR cancels the walk task running
   * on the bridge — and that was all: the head-yaw loop kept re-sending
   * walk_velocity(0, vyaw) every 900 ms while the head stayed turned, and the
   * cancelled task's answer (`status: "cancelled"`) came back as a refusal with
   * a 4 s hold-off, after which steering simply resumed. The bridge does not
   * block walk_velocity after a stop, so the page has to stop itself.
   */
  watch(): () => void {
    let prev: EstopPhase | null = null;
    return $effect.root(() => {
      $effect(() => {
        const next = estop.phase;
        // Only the phase is a dependency; everything else is read and written
        // inside.
        untrack(() => {
          const was = prev;
          prev = next;
          if (estopHalts(was, next)) this.#halt();
          // Once the stop is no longer in progress the loop may run again —
          // LATCHED, so nothing moves until the operator re-arms. Without this
          // the in-headset re-arm (let go, head to centre) could never happen:
          // the loop that checks for it would stay stopped until a walk press.
          if (!stopInFlight(next)) this.#targets.resume();
        });
      });
    });
  }

  /**
   * Halt everything this page commands, and latch it.
   *
   * Not `releaseAllControls()`: that lets go of a held button by sending this
   * page's own 0.5 s stop pulse, which here would be a second, slower stop
   * racing PARAR — on real hardware it lands on a robot PARAR has just damped,
   * gets refused, and reads as "La parada no se confirmó".
   *
   * The latch is the dead-man's, so its re-arm is the same deliberate human
   * action: let go and bring the head back to centre, or a fresh press.
   * Releasing the arm request too: once the bridge lifts its own e-stop latch,
   * frames still saying `arms: true` would put the robot's arms back under
   * power with nobody having asked again.
   */
  #halt(): void {
    // Straight down the teleop socket too, so PARAR works with apps/back down.
    this.#targets.stopStream();
    this.#targets.latchWalk();
    this.#targets.dropArms();
  }

  /**
   * IN-HEADSET PARAR: both grips held for STOP_CHORD_MS. Called FIRST in
   * `onSample`, before anything that could throw, and before the observer
   * guard: a stop is never anyone's to withhold. Same `estop.press()` as the
   * page's button (it also ends agent turns). The hold is what keeps a relaxed
   * grip on both controllers from stopping the robot; one hand never counts.
   */
  applyChord(grips: number, now: number): void {
    if (grips < 2) {
      this.#chordSince = null;
      this.#chordFired = false;
      return;
    }
    this.#chordSince ??= now;
    if (!this.#chordFired && now - this.#chordSince >= STOP_CHORD_MS) {
      this.#chordFired = true;
      void estop.press();
    }
  }
}
