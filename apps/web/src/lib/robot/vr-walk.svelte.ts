/**
 * Locomotion on /vr-control: hold-to-walk, the thumbstick, VR head-yaw turning
 * and the page's own `walk_velocity` loop, with its MAX_HOLD_S dead-man.
 *
 * Moved out of `vr-control.svelte.ts` intact; `VrControl` is still the only
 * thing the page talks to. What this needs from the rest of the controller
 * (whether a session is live, whether the teleop socket holds locomotion, the
 * observer flag) comes in through `WalkHost`, read at the moment it is needed,
 * never cached.
 */

import { createApi } from "#lib/api.js";
import { redirectToLogin } from "./poller.svelte";
import { estop } from "./estop.svelte";
import {
  classifyHttpError,
  classifyInvoke,
  type SkillOutcome,
} from "./skill-outcome";
import {
  WALK_REFUSAL_BACKOFF_MS,
  precautionaryStopNeeded,
  pruneOutcome,
  stampOutcome,
  stopInFlight,
  walkResultAction,
  yawNeedsCentre,
  type StampedOutcome,
  type WalkDispatch,
} from "./vr-logic";

// ---------------------------------------------------------------------------
// Combined locomotion dispatch.
//
// Both hold-to-walk and VR head-yaw turning ultimately drive the same
// `walk_velocity` call (open-loop, real-hardware-only, hard-capped by the
// bridge itself to 0.3 m/s / 0.3 rad/s / 3s per call — see
// apps/bridge/src/bridge/skills/walk_velocity.py), so they share one
// throttled loop rather than two independent timers that could each
// overwrite the other's vx/vyaw on a real call — visible as the robot
// zig-zagging between "walking straight" and "turning in place".
//
// NOT YET LIVE-TESTED on real hardware (bridge README, Phase 1b-workaround
// entry), so every constant below stays well under the bridge's own cap
// rather than pressing right up against it.
// ---------------------------------------------------------------------------
export const WALK_VX = 0.2; // m/s — below the bridge's 0.3 hard cap
const STEP_S = 1.0; // seconds sustained per dispatched call
const REPEAT_MS = STEP_S * 900; // re-issue slightly faster than STEP_S so
// consecutive calls overlap a little rather than leaving a gap where the
// firmware's own deadman (walk_velocity's duration) already zeroed it.
export const MAX_HOLD_S = 8; // dead-man ceiling for continuous combined motion.

export const YAW_DEADZONE_RAD = (6 * Math.PI) / 180; // ignore natural head wobble
const YAW_FULL_SCALE_RAD = (30 * Math.PI) / 180; // head turn that saturates vyaw
const YAW_MAX_RAD_S = 0.3; // below the bridge's 0.3 hard cap
export const VR_STALE_MS = 800; // no fresh pose sample in this long -> treat as 0

export type WalkDir = "forward" | "back";

/** A walk refusal, or a stop that did not land. */
export type WalkNotice = StampedOutcome & { stopFailed: boolean };

/** What the walk loop reads from the rest of the controller. */
export type WalkHost = {
  /** The live posture token, or null while offline. Outcomes key off it. */
  posture: () => string | null;
  /** Observe-only: command nothing (see `VrControl.viewOnly`). */
  viewOnly: () => boolean;
  /** A WebXR session is live. */
  vrActive: () => boolean;
  /** The teleop socket holds locomotion (`TeleopLink.bridgeHolds`). */
  bridgeHolds: () => boolean;
  /** The page has been torn down. */
  destroyed: () => boolean;
  /** An agent turn from the headset's voice is running. */
  voiceBusy: () => boolean;
  /** The polite live region. */
  announce: (text: string) => void;
  /** The readout snapshot every tick takes first (`VrControl`'s readouts). */
  refreshReadouts: () => void;
};

export class WalkController {
  walking = $state<WalkDir | null>(null);
  /** Why the last motion did not happen, as the bridge said it. */
  walkNotice = $state.raw<WalkNotice | null>(null);
  /**
   * Latched once the dead-man fires; blocks all motion until the operator
   * visibly lets go (head back to centre / fresh button press).
   *
   * Reactive now, because it trips SILENTLY otherwise. It used to be a plain
   * `let` nothing rendered: with a finger still on Adelante the button
   * un-highlighted and the readout reverted to "Mantené presionado para
   * caminar" — telling the operator to do what they were already doing — and
   * during head-yaw the robot just stopped turning with no message at all.
   */
  deadManTripped = $state(false);
  /**
   * What set the latch above, so the page can say the right thing: the
   * MAX_HOLD_S dead-man, or the console's PARAR (see `latchForEstop`). The
   * re-arm is the same either way — let go and bring the head to centre, or a
   * fresh press — but "más de 8 s de movimiento continuo" is a false
   * explanation for a stop the operator asked for.
   */
  latchCause = $state<"deadman" | "estop">("deadman");
  /**
   * The last accepted move was only pretended (stub bridge: `stub: true`).
   * The stub answers walk_velocity with an ordinary-looking success, so without
   * this the readout said "Manteniendo 0.2 m/s adelante…" for a robot that
   * does not exist.
   */
  lastMoveSimulated = $state(false);
  //: Head yaw is on hold until the head comes back to centre (see
  //: `yawNeedsCentre`) and the head is outside the deadzone: the headset says so.
  yawPaused = $state(false);

  // Raw pose, updated at XR frame rate (~72-120Hz) -- kept as plain fields
  // rather than $state so looking around doesn't force a Svelte re-render
  // every frame. The readout timer mirrors a rounded snapshot into
  // `VrControl.yawDisplayDeg` / `trackingStale` for the UI.
  yawErrorRadians = 0;
  lastYawSampleAt = 0;

  #host: WalkHost;
  #controlTimer: ReturnType<typeof setInterval> | null = null;
  // When the current continuous motion began, or null while stopped. Drives
  // the MAX_HOLD_S dead-man -- see `#tick()`.
  #motionStartedAt: number | null = null;
  // A refused motion is not re-sent before this (see WALK_REFUSAL_BACKOFF_MS).
  #refusedUntil = 0;
  // Ordering for walk results. Calls overlap by design (REPEAT_MS < STEP_S, and
  // a successful walk_velocity answers only after its duration), so answers can
  // arrive out of order; an older one must not overwrite a newer verdict.
  #walkSeq = 0;
  #walkSeqApplied = 0;
  // Whether a move has been sent since the last stop pulse. A stop that follows
  // nothing of ours is precautionary, and its refusal is not news.
  #movedSinceStop = false;
  // Bumped by every PARAR (`latchForEstop`). A walk_velocity answer from an
  // earlier epoch is about a motion the operator has already overridden — see
  // `#sendVelocity`.
  #estopEpoch = 0;
  //: Which direction the thumbstick is currently asking for, so a change is
  //: distinguishable from the same intent repeated at frame rate. `onSample`
  //: fires 72-120 times a second; without this, every frame of a held stick
  //: would look like a fresh press and keep re-arming the dead-man, which is
  //: exactly the latch that stops a runaway.
  #stickWalk: WalkDir | null = null;
  //: Set whenever the stick's state was reset without the stick having been
  //: SEEN at neutral (window blur, pointer events, stale tracking, session
  //: end). Until a sample reads neutral, a deflected stick starts nothing.
  //: Without it, a stick held through the 8 s latch was forgotten by any
  //: overlay tap and the very next frame read as a fresh push: startWalking,
  //: dead-man cleared, another 8 s — a held stick re-arming its own latch.
  #stickNeedsNeutral = false;
  // See `yawNeedsCentre`. The dead-man re-arm reads the real yaw, not this.
  #yawNeedsCentre = false;

  constructor(host: WalkHost) {
    this.#host = host;
  }

  /**
   * The console's PARAR is still going out ("Parando…"). Nothing here moves
   * meanwhile — reactive, because it reads the shared e-stop store, so the
   * walk buttons can say so.
   */
  get stopInFlight(): boolean {
    return stopInFlight(estop.phase);
  }

  /** The walk loop's interval is armed. */
  get loopActive(): boolean {
    return this.#controlTimer !== null;
  }

  /** The walk refusal, if it still describes the robot (pure read, like `outcomeFor`). */
  currentNotice(now: number): WalkNotice | null {
    const n = this.walkNotice;
    return n && pruneOutcome(n, now, this.#host.posture()) ? n : null;
  }

  /** Drop the walk refusal once it no longer describes the robot. */
  pruneNotice(now: number, posture: string | null): void {
    if (this.walkNotice) {
      const kept = pruneOutcome(this.walkNotice, now, posture);
      if (kept !== this.walkNotice) {
        this.walkNotice = kept
          ? { ...kept, stopFailed: this.walkNotice.stopFailed }
          : null;
      }
    }
  }

  /**
   * Whether a teardown or a handover must send one stop pulse: decided from
   * what this page has commanded, so leaving an idle page sends nothing. See
   * `precautionaryStopNeeded`.
   */
  precautionaryStopNeeded(): boolean {
    return precautionaryStopNeeded({
      movedSinceStop: this.#movedSinceStop,
      walking: this.walking !== null,
      loopActive: this.#controlTimer !== null,
      vrActive: this.#host.vrActive(),
    });
  }

  /**
   * The PARAR latch (`VrStop`): the walk-loop half of `#halt`.
   */
  latchForEstop(): void {
    this.#estopEpoch++;
    this.walking = null;
    this.deadManTripped = true;
    this.latchCause = "estop";
    this.#motionStartedAt = null;
    this.#refusedUntil = 0;
    // PARAR is the stop now; nothing of ours is outstanding after it.
    this.#movedSinceStop = false;
    this.#stopLoop();
  }

  /**
   * The stick half of `VrControl.releaseAllControls`.
   *
   * Forget the stick's walk, but do NOT treat whatever it reads next as a
   * fresh push: these events (session end, blur, visibility change, pointer
   * up/cancel) say nothing about where the operator's thumb is. The stick
   * has to be seen at neutral first — see `#stickNeedsNeutral`.
   */
  forgetStick(): void {
    this.#stickWalk = null;
    this.#stickNeedsNeutral = true;
  }

  /** The held-button half of `VrControl.releaseAllControls`. */
  releaseHeld(): void {
    if (this.walking !== null) {
      this.walking = null;
      if (!this.#host.bridgeHolds() && !this.#loopShouldRun()) {
        this.#stopLoop();
        void this.#sendVelocity(0, 0, 0.5);
      }
    }
  }

  /** False while head yaw is held for the headset's voice turn. Updates the hold. */
  yawSteers(): boolean {
    // null: this pose may be stale, so it can set the hold but never release it.
    this.#yawNeedsCentre = yawNeedsCentre(
      this.#yawNeedsCentre,
      this.#host.voiceBusy(),
      null,
      YAW_DEADZONE_RAD,
    );
    return !this.#yawNeedsCentre;
  }

  /** Coming back from an overlay is not a deliberate act: steer again only from centre. */
  holdYawUntilCentred(): void {
    this.#yawNeedsCentre = true;
  }

  /** The only place the centre hold is released: a fresh sample. XR loop rate. */
  noteYawHold(yaw: number): void {
    const busy = this.#host.voiceBusy();
    this.#yawNeedsCentre = yawNeedsCentre(
      this.#yawNeedsCentre,
      busy,
      yaw,
      YAW_DEADZONE_RAD,
    );
    // Guarded: $state written at frame rate re-renders at frame rate. Not
    // during the turn itself, where the voice panel already says what is going on.
    const paused =
      this.#yawNeedsCentre && !busy && Math.abs(yaw) >= YAW_DEADZONE_RAD;
    if (paused !== this.yawPaused) this.yawPaused = paused;
  }

  #computeVyaw(): number {
    const steers = this.yawSteers(); // first, so the hold sees every busy edge
    if (!steers || !this.#host.vrActive() || this.deadManTripped) return 0;
    if (Date.now() - this.lastYawSampleAt > VR_STALE_MS) return 0; // tracking stale
    const err = this.yawErrorRadians;
    const mag = Math.abs(err);
    if (mag < YAW_DEADZONE_RAD) return 0;
    const scaled =
      ((mag - YAW_DEADZONE_RAD) / (YAW_FULL_SCALE_RAD - YAW_DEADZONE_RAD)) *
      YAW_MAX_RAD_S;
    return Math.sign(err) * Math.min(YAW_MAX_RAD_S, scaled);
  }

  /** One explicit 0.5 s stop pulse (teardown: `VrControl.destroy`). */
  sendStop(): void {
    void this.#sendVelocity(0, 0, 0.5);
  }

  /**
   * One `walk_velocity` call, and what its answer means.
   *
   * THE BRIDGE NEVER RAISES — the same bug commit 575265e fixed for gestures
   * and never applied here. walk_velocity answers HTTP 200 with `status:
   * "failed"` when the firmware refuses (`phase: "rpc_error"`, e.g. not in a
   * walk program), when another operator's stream holds locomotion (`phase:
   * "teleop_active"`), or on an exception; and `status: "not_applicable"` in
   * Isaac mode. This read only the HTTP status, so in every one of those cases
   * the button stayed lit and the readout said "Manteniendo 0.2 m/s adelante…"
   * while the robot stood still. The body is classified now, by the same
   * `classifyInvoke` every other surface uses.
   */
  async #sendVelocity(
    vx: number,
    vyaw: number,
    duration_s: number,
  ): Promise<void> {
    const dispatch: WalkDispatch = vx !== 0 || vyaw !== 0 ? "move" : "stop";
    // The sequence guard: every walk_velocity passes through here, so this is
    // where "nothing moves during PARAR or under the latch" is enforced, not
    // just hoped for by each caller. Ticks and presses check both already;
    // a path added later that forgets to is stopped here. Stops always pass.
    if (dispatch === "move" && (this.stopInFlight || this.deadManTripped))
      return;
    const epoch = this.#estopEpoch;
    const movedSinceStop = this.#movedSinceStop;
    this.#movedSinceStop = dispatch === "move";
    const seq = ++this.#walkSeq;

    let outcome: SkillOutcome;
    let phase: string | null = null;
    try {
      const res = await createApi(fetch)
        .skills({ name: "walk_velocity" })
        .invoke.post({ vx, vy: 0, vyaw, duration_s });
      if (res.error && (res.error.status as number) === 401) {
        // A session problem, never a robot problem. `redirectToLogin` is a
        // no-op on the server, which is what `VrControl.destroy()`'s comment
        // is about.
        redirectToLogin("expired");
        return;
      }
      outcome = classifyInvoke(res);
      const data = res.data as { phase?: unknown } | null | undefined;
      phase = typeof data?.phase === "string" ? data.phase : null;
    } catch (err) {
      outcome = {
        ...classifyHttpError(null, null),
        detail: err instanceof Error ? err.message : null,
      };
    }
    if (this.#host.destroyed()) return;
    // Sent before a PARAR. Its answer is typically `cancelled` (the stop
    // killed the task), and treating that as a refusal put "No camina:
    // cancelled" on screen and armed a 4 s hold-off that, once it ran out,
    // let the head-yaw loop resume. It describes a motion the operator has
    // already overridden; nothing in it is news.
    if (epoch !== this.#estopEpoch) return;
    if (seq < this.#walkSeqApplied) return; // overtaken by a newer answer
    this.#walkSeqApplied = seq;

    const action = walkResultAction(outcome, {
      dispatch,
      phase,
      streamHolds: this.#host.bridgeHolds(),
      movedSinceStop,
    });

    if (action === "clear") {
      if (this.walkNotice) this.walkNotice = null;
      if (this.lastMoveSimulated !== outcome.simulated)
        this.lastMoveSimulated = outcome.simulated;
      return;
    }
    if (action === "ignore") return;

    this.walkNotice = {
      ...stampOutcome(outcome, Date.now(), this.#host.posture()),
      stopFailed: action === "stop_failed",
    };
    this.#host.announce(
      action === "stop_failed"
        ? `La parada no se confirmó: ${outcome.text}`
        : `No camina: ${outcome.text}`,
    );

    if (action === "refuse") {
      // The robot did not do it, so the button must stop claiming it is.
      this.walking = null;
      this.#movedSinceStop = false;
      this.#motionStartedAt = null;
      this.#refusedUntil = Date.now() + WALK_REFUSAL_BACKOFF_MS;
      // Without VR this loop exists only for the held button; with VR it is
      // also the head-yaw loop, which keeps running and is held off by
      // `#refusedUntil` instead — stopping it outright would leave steering
      // dead until something happened to call `ensureLoopRunning()`.
      if (!this.#loopShouldRun()) this.#stopLoop();
    }
  }

  #loopShouldRun(): boolean {
    // Observe-only commands nothing, and this is the guard that matters most:
    // the condition at the bottom is true whenever `vrActive` is, and an
    // observer's session sets `vrActive` like any other. Without this line,
    // entering "solo para ver" would start the velocity loop on the head pose.
    if (this.#host.viewOnly()) return false;
    // While the socket is up the bridge owns locomotion. Running this loop too
    // would put two writers on SetVelocity. `bridgeHolds`, not `streaming`:
    // a stalled socket is still transmitting.
    if (this.#host.bridgeHolds()) return false;
    // PARAR is going out. A tick that fires now stops the loop instead of
    // dispatching; `VrStop` restarts it, latched, once the stop is done.
    if (this.stopInFlight) return false;
    return this.walking !== null || this.#host.vrActive();
  }

  #stopLoop(): void {
    if (this.#controlTimer) clearInterval(this.#controlTimer);
    this.#controlTimer = null;
  }

  /** Stop the loop (teardown). The readout timer is not this one. */
  stopLoop(): void {
    this.#stopLoop();
  }

  /** Stop the loop with one stop pulse, unless something still needs it (session end). */
  stopUnlessNeeded(): void {
    if (!this.#loopShouldRun()) {
      this.#stopLoop();
      void this.#sendVelocity(0, 0, 0.5);
    }
  }

  /**
   * The teleop socket opened: the bridge is the commander now; make sure this
   * page is not also mid-flight with a walk_velocity call. Only when it could
   * be: the same rule as `VrControl.destroy()`'s stop, so connecting from an
   * idle page does not log a walk command nobody gave.
   */
  yieldToStream(): void {
    const outstanding = this.precautionaryStopNeeded();
    this.#stopLoop();
    if (outstanding) void this.#sendVelocity(0, 0, 0.5);
  }

  /**
   * A stick walk ends when the head pose goes stale. Called from the readout
   * snapshot with its `trackingStale`.
   *
   * A walk the THUMBSTICK started ends when the head pose goes stale. The
   * stick only ever releases a walk by being SAMPLED at neutral, and the
   * samples come from the XR frame loop — which stops when the headset comes
   * off, the system menu opens, or tracking is lost. Yaw and arms were
   * already gated on freshness; `walking` was not, so a stick held at that
   * moment kept walking — on the stream and on the HTTP loop alike — until
   * the 8 s hold latch, about 1.6 m with nobody looking. DOM-button walks
   * are left alone: their release comes from pointer events, which do not
   * depend on tracking. (`#tick` runs the snapshot too, so the loop catches
   * it even when the readout timer is not running.)
   */
  noteTracking(trackingStale: boolean): void {
    if (trackingStale && this.#stickWalk !== null) {
      if (this.walking === this.#stickWalk) this.stopWalking();
      this.#stickWalk = null;
      // No sample is arriving, so nothing says the thumb has let go.
      this.#stickNeedsNeutral = true;
    }
  }

  ensureLoopRunning(): void {
    if (this.#controlTimer || !this.#loopShouldRun()) return;
    this.#controlTimer = setInterval(() => this.#tick(), REPEAT_MS);
  }

  /**
   * One dispatch tick.
   *
   * The dead-man measures time since MOTION began, not since the loop or the
   * VR session began, and latches when it fires. An earlier version keyed off
   * loop start and re-armed itself every time it tripped, which broke it in
   * both directions: entering VR and then pressing walk 7s later gave a
   * one-second ceiling instead of eight, while a continuously-held turn just
   * got a fresh 8s window forever -- so it never actually stopped anything,
   * it only injected a stop pulse every 8s (and kept injecting them while
   * idle, with the head at centre and no button held).
   */
  #tick(): void {
    // Same snapshot the readout timer takes (it used to be repeated here line
    // for line); the readouts must not wait for a session to exist.
    this.#host.refreshReadouts();

    if (!this.#loopShouldRun()) {
      this.#stopLoop();
      return;
    }

    const wantVx =
      this.walking === "forward"
        ? WALK_VX
        : this.walking === "back"
          ? -WALK_VX
          : 0;
    const wantVyaw = this.#computeVyaw(); // already 0 while the dead-man is tripped

    // Re-arm once the operator has visibly let go: no walk button held AND
    // the head back near centre. A fresh button press re-arms too, via
    // startWalking -- both are deliberate human actions, which is the point.
    // A stick still deflected is still held, even though `walking` was cleared
    // when the latch fired.
    if (
      this.deadManTripped &&
      wantVx === 0 &&
      this.#stickWalk === null &&
      Math.abs(this.yawErrorRadians) < YAW_DEADZONE_RAD
    ) {
      this.deadManTripped = false;
    }
    if (this.deadManTripped) {
      this.#motionStartedAt = null;
      return;
    }

    // A refusal is on screen. Re-sending the same request every 900 ms would
    // only overwrite it with itself; the hold-off ends on its own, and a fresh
    // press ends it at once.
    if (Date.now() < this.#refusedUntil) {
      this.#motionStartedAt = null;
      return;
    }

    if (wantVx === 0 && wantVyaw === 0) {
      // Nothing requested. If we were moving, send one explicit stop rather
      // than relying solely on the firmware's own duration deadman.
      if (this.#motionStartedAt !== null) {
        this.#motionStartedAt = null;
        void this.#sendVelocity(0, 0, 0.5);
      }
      return;
    }

    if (this.#motionStartedAt === null) {
      this.#motionStartedAt = Date.now();
    } else if (Date.now() - this.#motionStartedAt > MAX_HOLD_S * 1000) {
      this.deadManTripped = true;
      this.latchCause = "deadman";
      this.#motionStartedAt = null;
      this.walking = null;
      void this.#sendVelocity(0, 0, 0.5);
      return;
    }

    void this.#sendVelocity(wantVx, wantVyaw, STEP_S);
  }

  startWalking(dir: WalkDir): void {
    if (this.walking || this.#host.destroyed()) return;
    // An observer does not walk. Blocked downstream as well (`#loopShouldRun`,
    // `buildFrame`), but not setting the state at all means the buttons do not
    // show a walk that is not happening either.
    if (this.#host.viewOnly()) return;
    // PARAR is still going out. A press now would race the stop the operator
    // just asked for — and a hand still on the button is exactly what a
    // reflexive stop leaves behind. The buttons are aria-disabled meanwhile;
    // this is the guard behind them (it covers the stream path too, where
    // `walking` alone drives the bridge).
    if (this.stopInFlight) {
      this.#host.announce(
        "PARAR en curso: la marcha espera a que termine la parada.",
      );
      return;
    }
    this.walking = dir;
    this.walkNotice = null;
    // A deliberate fresh press is the "release and re-press" gesture that
    // re-arms the dead-man, and starts a full new motion window. It is also a
    // deliberate retry of whatever was refused.
    this.deadManTripped = false;
    this.#motionStartedAt = null;
    this.#refusedUntil = 0;
    // `bridgeHolds`, not `streaming`: a stalled socket is still open and still
    // transmitting, so starting this loop would make two writers.
    if (this.#host.bridgeHolds()) return;
    this.ensureLoopRunning();
    this.#tick(); // immediate feedback, don't wait for the next interval tick
  }

  stopWalking(): void {
    if (!this.walking) return;
    this.walking = null;
    if (this.#host.bridgeHolds()) return;
    if (!this.#loopShouldRun()) {
      this.#stopLoop();
      void this.#sendVelocity(0, 0, 0.5);
    }
    // else: VR turning is still active -- the running loop already accounts
    // for walking being cleared on its next tick.
  }

  /**
   * Thumbstick -> walk, with the same rules as the on-screen buttons.
   *
   * Only ever drives walking it started itself. If the operator is holding a
   * DOM button (passthrough, where the overlay composites) that press owns
   * `walking`, and a stick reading 0 must not cancel it — two input paths
   * fighting over one piece of state is how a held button silently stops
   * working.
   */
  applyWalkAxis(axis: -1 | 0 | 1): void {
    if (this.#host.destroyed()) return;
    if (this.#host.viewOnly()) {
      this.#stickWalk = null;
      return;
    }
    const want: WalkDir | null =
      axis === 1 ? "forward" : axis === -1 ? "back" : null;
    if (this.#stickNeedsNeutral) {
      if (want !== null) return; // still deflected since the reset
      this.#stickNeedsNeutral = false;
    }
    if (want === this.#stickWalk) return; // unchanged; do not re-arm anything

    if (want === null) {
      // Released. Only stop what the stick started — see above.
      if (this.#stickWalk !== null && this.walking === this.#stickWalk)
        this.stopWalking();
      this.#stickWalk = null;
      return;
    }
    // Pushed, or pushed the other way. Reverse goes through a stop so the
    // dead-man re-arms and a new motion window starts, rather than sliding
    // from forward to back inside one window that never resets.
    if (this.walking !== null && this.walking !== want) this.stopWalking();
    this.#stickWalk = want;
    this.startWalking(want);
  }

  /**
   * "Recentrar": a deliberate re-arm. Recentring makes yawError ~0, which is
   * itself the re-arm condition -- clearing the latch here just avoids
   * waiting a tick for it.
   */
  rearm(): void {
    // Nothing re-arms while PARAR is still going out ("Parando…").
    if (this.stopInFlight) return;
    this.deadManTripped = false;
    this.#motionStartedAt = null;
    // The loop may have been stopped by a PARAR or a refusal; a deliberate
    // re-arm should bring head-yaw steering back without needing a walk press.
    this.ensureLoopRunning();
  }

  /**
   * A fresh session starts a fresh dead-man window — but does not lift a
   * PARAR latch nobody has re-armed yet (one pressed during the consent
   * prompt, say): accepting a prompt is not a re-arm. The session starts
   * recentred, so the first tick re-arms it anyway if nothing is held.
   */
  freshSession(): void {
    if (this.latchCause !== "estop") this.deadManTripped = false;
    this.#motionStartedAt = null;
    this.ensureLoopRunning();
  }
}
