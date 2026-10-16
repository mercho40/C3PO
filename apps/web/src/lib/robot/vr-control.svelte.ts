/**
 * VR control — the Quest 3 teleop feature's state and dispatch, end to end:
 * hold-to-walk, WebXR head-yaw turning, the teleop stream and arm mirror,
 * one-shot preset skills, and the robot camera connection the headset shares.
 *
 * This used to live inline in a 1460-line `+page.svelte`. It moved here intact:
 * every safety rule below is one a headset session found the hard way, and the
 * comments explaining them came with it. The page and `#lib/components/vr/*`
 * only render this; the decisions that can be tested without a browser live in
 * `./vr-logic.ts`.
 *
 * `/invoke` is admin-only for every non-safety skill (`apps/back/src/routes/
 * skills.ts`), so walking through `walk_velocity` and every preset needs
 * `role: "admin"`. The teleop stream does not go through apps/back at all.
 */

import { untrack } from "svelte";
import { browser } from "$app/env";
import {
  PUBLIC_ROBOT_CAM_URL,
  PUBLIC_TELEOP_HOST,
  PUBLIC_TELEOP_PORT,
} from "$app/env/public";
import { createApi } from "#lib/api.js";
import { redirectToLogin } from "./poller.svelte";
import { estop } from "./estop.svelte";
import type { EstopPhase } from "./estop";
import {
  classifyHttpError,
  classifyInvoke,
  type SkillOutcome,
} from "./skill-outcome";
import {
  SAFE_STEP,
  WALK_REFUSAL_BACKOFF_MS,
  estopHalts,
  pruneOutcome,
  pruneOutcomes,
  stampOutcome,
  stopInFlight,
  precautionaryStopNeeded,
  walkResultAction,
  xrErrorCopy,
  type StampedOutcome,
  type WalkDispatch,
  type XrErrorCopy,
} from "./vr-logic";
import {
  XrTeleopSession,
  checkXrSupport,
  type HandSample,
  type XrTeleopSupport,
} from "#lib/webxr/xr-teleop.js";
import {
  buildFrame,
  connectTeleop,
  type TeleopFramePayload,
  type TeleopHandle,
  type TeleopState,
  type TeleopStatus,
} from "#lib/teleop/stream.js";
import {
  connectRobotCamera,
  type RobotCamHandle,
  type RobotCamState,
  type RobotCamStatus,
} from "./mjpeg-camera";
import { describeRobotCam } from "./camera-copy";
import { ScanFeed } from "./scan.svelte";
import {
  alertFor,
  type MenuAlert,
  type MenuItem,
  type Readiness,
} from "#lib/webxr/menu-layer.js";
import { cameraReasonFor, type CameraReason } from "#lib/webxr/camera-layer.js";

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

const YAW_DEADZONE_RAD = (6 * Math.PI) / 180; // ignore natural head wobble
const YAW_FULL_SCALE_RAD = (30 * Math.PI) / 180; // head turn that saturates vyaw
const YAW_MAX_RAD_S = 0.3; // below the bridge's 0.3 hard cap
const VR_STALE_MS = 800; // no fresh pose sample in this long -> treat as 0

// USB link probe cadence. See `#startLinkWatch`.
const LINK_PROBE_MS = 5000;
const LINK_PROBE_TIMEOUT_MS = 4000;

export type WalkDir = "forward" | "back";

/** A walk refusal, or a stop that did not land. */
export type WalkNotice = StampedOutcome & { stopFailed: boolean };

export type VrControlOptions = {
  /** The live posture token, or null while offline. Outcomes key off it. */
  posture: () => string | null;
  /**
   * The in-headset preset menu, each entry's `verified` straight from the
   * catalogue. Read when a session starts.
   */
  menuItems?: () => readonly MenuItem[];
};

export class VrControl {
  // --- locomotion ------------------------------------------------------
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
   * MAX_HOLD_S dead-man, or the console's PARAR (see `#haltForEstop`). The
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

  // --- WebXR -----------------------------------------------------------
  /**
   * Tri-state on purpose. The capability flags used to start `false` and flip
   * once `checkXrSupport()` resolved, so the Quest first rendered — SSR
   * included — "WebXR no está disponible en este navegador", and a desktop
   * flashed a hand-tracking hint telling it to change a Quest setting.
   */
  xrProbe = $state.raw<"checking" | XrTeleopSupport>("checking");
  vrActive = $state(false);
  vrError = $state.raw<XrErrorCopy | null>(null);
  /**
   * Guards `enterVr` itself. The session object has its own re-entrancy
   * guard, but it is PER INSTANCE and `enterVr` builds a new instance on every
   * call — so two taps made two sessions and the guard never fired. `vrActive`
   * is no use either: it is set after several awaits, including one that blocks
   * on the headset's own consent prompt.
   *
   * $state as well, so "Entrar en VR" can say it is waiting for that prompt
   * instead of silently ignoring every further tap.
   */
  enteringVr = $state(false);
  //: Which mode the live session actually got. Passthrough composites the DOM
  //: overlay; plain VR may not, which is what produced a black view with no
  //: controls in the first headset session.
  xrMode = $state<string | null>(null);
  //: Whether the in-headset camera layer has decoded a frame. Distinct from
  //: the on-page panel: they share a source but not a decoder, so one can be
  //: live while the other is not.
  xrCameraLive = $state(false);
  //: Has the headset layer EVER decoded a frame? Distinct from `xrCameraLive`,
  //: which is a liveness flag. Conflating them made the page tell the operator
  //: "no frame ever arrived, perception is probably not running" during an
  //: ordinary one-second stall — a confident, wrong diagnosis pointing them at
  //: the robot while a real picture was on screen in front of them.
  xrCameraEverHadFrame = $state(false);
  //: The XR layer dropped the picture because drawing threw — a shader compile
  //: or link failure, not anything to do with the robot. The session sets this
  //: and nothing read it, so a GL failure was reported to the operator as
  //: "perception is probably not running": the worst diagnosis attached to the
  //: least likely cause.
  xrCameraBroken = $state(false);
  yawDisplayDeg = $state(0);
  trackingStale = $state(false);
  handsVisible = $state(false);

  //: OBSERVE-ONLY: watch the robot's view, command nothing.
  //
  //: For the second person in the room, for a demo, and for checking a camera
  //: without becoming a commander. Set BEFORE the session starts and never
  //: changed while one is live — a toggle mid-session would mean the operator
  //: cannot tell by looking whether the thing on their head is driving.
  //
  //: There are two independent ways this page commands the robot — the teleop
  //: socket the bridge PULLS frames from, and the local `#sendVelocity` loop —
  //: so one flag on one branch would not be enough. Closed at three points:
  //:   1. entering observe-only calls `disconnectStream()`, and
  //:      `connectStream()` refuses while the session lives, so the socket
  //:      does not exist;
  //:   2. `#loopShouldRun()` returns false, so the local loop never starts —
  //:      it keys on `vrActive`, which IS set for an observer;
  //:   3. `buildFrame()` takes `observeOnly` and returns the idle payload
  //:      before reading any pose — in the pure policy, where it is tested,
  //:      rather than in a caller that has to remember.
  //: Any one would do. Three means a later edit to one of them cannot quietly
  //: arm a headset whose wearer was told it was passive.
  viewOnly = $state(false);

  //: The lidar radar, bottom-left of each eye.
  //:
  //: POLLED ONLY WHILE A SESSION IS OPEN. The ring exists for the operator
  //: whose view of the room is a 69-degree camera on a black surround; on the
  //: desktop console the map already shows the same obstacles with more
  //: context. Polling it from a page nobody is wearing would put 4 requests a
  //: second through the bridge for a panel that is not drawn.
  readonly scanFeed = new ScanFeed();

  // --- USB link watch ----------------------------------------------------
  linkLost = $state(false);

  // --- teleop stream -----------------------------------------------------
  //
  // A second, deliberately separate transport: a WebSocket straight to
  // `apps/bridge/src/bridge/teleop/server.py` (port 8767), carrying head
  // yaw + both wrists + finger closure at ~30Hz. See that module and
  // `#lib/teleop/stream.ts` for why this does not go through apps/back.
  //
  // While it is open it becomes the ONLY commander of locomotion: the
  // `walk_velocity` loop below is suspended, and the walk buttons feed the
  // stream's `walk` axis instead. Two independent writers of SetVelocity
  // would silently fight, last-writer-wins, with neither aware of the other.
  teleopState = $state<TeleopState>("closed");
  teleopDetail = $state("");
  teleopStatus = $state.raw<TeleopStatus | null>(null);
  armsRequested = $state(false);

  // --- camera ------------------------------------------------------------
  camState = $state<RobotCamState>("closed");
  camDetail = $state("");
  camFrameUrl = $state<string | null>(null);
  camLive = $state(false);
  camStatus = $state.raw<RobotCamStatus | null>(null);

  // --- presets -----------------------------------------------------------
  /** The preset holding the dispatch lock, and its label for "Esperando a «…»". */
  lock = $state.raw<{ key: string; label: string } | null>(null);
  /** Tiles with a request in flight (the lock holder, plus damp if it bypassed). */
  inFlight = $state.raw<Record<string, boolean>>({});
  outcomes = $state.raw<Record<string, StampedOutcome>>({});

  /** The last result, phrased for a screen reader. One polite live region. */
  announcement = $state("");
  /** Wall clock, ticked once a second, so outcome ages ("hace 12 s") move. */
  now = $state(Date.now());

  get streaming(): boolean {
    return this.teleopState === "open";
  }

  //: The bridge holds locomotion whenever the socket is up — INCLUDING while
  //: stalled. `streaming` goes false there, which used to re-open the door for
  //: this page's own velocity loop: the socket is still OPEN, its 33 Hz sender
  //: is still transmitting, and the moment the bridge starts reading again
  //: both writers are live on SetVelocity. `stalled` is a diagnosis, not a
  //: handover.
  get bridgeHolds(): boolean {
    return this.teleopState === "open" || this.teleopState === "stalled";
  }

  get vrSupported(): boolean {
    const p = this.xrProbe;
    return p !== "checking" && (p.immersiveVr || p.immersiveAr);
  }

  get arSupported(): boolean {
    const p = this.xrProbe;
    return p !== "checking" && p.immersiveAr;
  }

  get handTrackingSupported(): boolean {
    const p = this.xrProbe;
    return p !== "checking" && p.handTracking;
  }

  /**
   * The console's PARAR is still going out ("Parando…"). Nothing here moves
   * meanwhile — reactive, because it reads the shared e-stop store, so the
   * walk buttons can say so.
   */
  get stopInFlight(): boolean {
    return stopInFlight(estop.phase);
  }

  /**
   * The two latches that stop motion while everything else looks healthy, as
   * the headset shows them.
   *
   * `deadman_tripped` is the one that bit on 2026-08-24: it fires at 8 s of
   * continuous motion — a limit a TAPPED button never reached and a HELD
   * thumbstick reaches every time — so walking stopped dead seven times in one
   * session and read as "the walking is a bit buggy". The status carrying it
   * already arrived twice a second, on the page, behind a headset.
   */
  get menuAlert(): MenuAlert {
    // This page's OWN latch too, when it is the one commanding (no stream):
    // the HTTP loop's 8 s dead-man otherwise trips with nothing in the
    // headset — the 2026-08-24 symptom, on the other transport.
    const own = this.deadManTripped && !this.bridgeHolds;
    return alertFor(
      this.teleopStatus?.deadman_tripped === true ||
        (own && this.latchCause === "deadman"),
      this.teleopStatus?.stopped_by_estop === true ||
        (own && this.latchCause === "estop"),
    );
  }

  /**
   * Whether this console has a camera URL at all. Without one the headset
   * never asks for a picture, and "no frame ever arrived" is the console's
   * configuration, not the robot's vision process.
   */
  get camConfigured(): boolean {
    return PUBLIC_ROBOT_CAM_URL.trim() !== "";
  }

  // --- plain fields: hot-path or non-rendered state ------------------------
  #posture: () => string | null;
  #controlTimer: ReturnType<typeof setInterval> | null = null;
  #uiTimer: ReturnType<typeof setInterval> | null = null;
  #linkTimer: ReturnType<typeof setInterval> | null = null;
  #clockTimer: ReturnType<typeof setInterval> | null = null;
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
  // Bumped by every PARAR (`#haltForEstop`). A walk_velocity answer from an
  // earlier epoch is about a motion the operator has already overridden — see
  // `#sendVelocity`.
  #estopEpoch = 0;
  #unwatchEstop: (() => void) | null = null;
  #unwatchHeadset: (() => void) | null = null;
  #menuItems: () => readonly MenuItem[];
  // Pushed by the page (it owns live posture and faults); kept here so a
  // session that starts later opens on the current value.
  #readiness: Readiness | null = null;
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
  //: Previous frame's button state, for edge detection. The session reports
  //: held-state at display rate, so without this a single press of A would
  //: cycle the highlight through the whole list in under a tenth of a second
  //: and the trigger would fire the same skill 70 times.
  #prevButtons = { trigger: false, primary: false };

  // Raw pose, updated at XR frame rate (~72-120Hz) -- kept as plain fields
  // rather than $state so looking around doesn't force a Svelte re-render
  // every frame. The readout timer mirrors a rounded snapshot into the two
  // $state fields above for the UI.
  #yawErrorRadians = 0;
  #lastYawSampleAt = 0;
  // Head position and both wrists, at XR frame rate. Plain for the same reason
  // as `#yawErrorRadians`: these change 72-120 times a second and nothing
  // renders them directly.
  #headPosition: [number, number, number] = [0, 1.6, 0];
  #handLeft: HandSample | null = null;
  #handRight: HandSample | null = null;

  #vr: XrTeleopSession | null = null;
  // The session between `new` and `start()` resolving — which includes the
  // headset's consent prompt, for as long as the wearer leaves it up. `#vr` is
  // only assigned after that await, so a page left during the prompt used to
  // call `this.#vr?.stop()` on null, and the session then started DETACHED:
  // no controls, no PARAR, and this page's 900 ms loop still sending head-yaw
  // walk_velocity. `destroy()` stops this one too.
  #pendingVr: XrTeleopSession | null = null;
  #teleop: TeleopHandle | null = null;
  #camHandle: RobotCamHandle | null = null;
  #destroyed = false;

  constructor(opts: VrControlOptions) {
    this.#posture = opts.posture;
    this.#menuItems = opts.menuItems ?? (() => []);
  }

  /**
   * Browser-only setup. Returns the teardown. Call from `onMount`.
   */
  mount(): () => void {
    // `pointerup` alone is not enough: a pointer can be cancelled, the window
    // can lose focus, or the page can be hidden by the system UI, none of
    // which fire it on the button.
    const release = () => this.releaseAllControls();
    const onHidden = () => {
      if (document.visibilityState === "hidden") this.releaseAllControls();
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", onHidden);

    this.#startLinkWatch();
    this.#unwatchEstop ??= this.#watchEstop();
    this.#unwatchHeadset ??= this.#watchHeadset();

    void checkXrSupport().then((s) => {
      if (!this.#destroyed) this.xrProbe = s;
    });

    this.#clockTimer = setInterval(() => this.#clock(), 1000);

    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", onHidden);
      if (this.#linkTimer) clearInterval(this.#linkTimer);
      this.#linkTimer = null;
      if (this.#clockTimer) clearInterval(this.#clockTimer);
      this.#clockTimer = null;
      this.#unwatchEstop?.();
      this.#unwatchEstop = null;
      this.#unwatchHeadset?.();
      this.#unwatchHeadset = null;
    };
  }

  /**
   * Teardown that stops the robot. Call from `onDestroy`.
   *
   * SSR runs component setup and teardown on the server, so this fired there —
   * and its safety stop called `goto("/login")` on a 401, which throws "Cannot
   * call goto(...) on the server" and took the whole dev server down with it.
   * The page had never been rendered before the first headset session, which
   * is exactly when it surfaced.
   *
   * Nothing in this teardown means anything off the client: there is no robot
   * to stop from a render process.
   */
  destroy(): void {
    if (!browser) return;
    // Decided BEFORE the teardown below clears the very things it looks at.
    // The precautionary stop used to go out unconditionally, so merely leaving
    // an idle page sent walk_velocity (and the audit log now records it as a
    // walk command). See `precautionaryStopNeeded`.
    const needsStop = precautionaryStopNeeded({
      movedSinceStop: this.#movedSinceStop,
      walking: this.walking !== null,
      loopActive: this.#controlTimer !== null,
      vrActive: this.vrActive,
    });
    this.#destroyed = true;
    this.#unwatchEstop?.();
    this.#unwatchEstop = null;
    this.#unwatchHeadset?.();
    this.#unwatchHeadset = null;
    this.scanFeed.stop();
    this.walking = null;
    this.armsRequested = false;
    this.#vr?.stop();
    // Still on the consent prompt: the session honours a stop requested
    // mid-start by ending itself the moment it exists, and `#startVrSession`
    // checks `#destroyed` after its await as well.
    this.#pendingVr?.stop();
    this.#pendingVr = null;
    // Closing the stream sends one last released frame, so the bridge stops on
    // a frame rather than waiting out its staleness timeout.
    this.#teleop?.close();
    this.#teleop = null;
    this.#stopLoop();
    // The readout timer is separate from the control loop and `#stopLoop()`
    // does not touch it. Left running, it fires every 200 ms and writes state
    // on a destroyed component for as long as the tab is open, and every
    // return to this page adds another one.
    this.#stopReadouts();
    this.#camHandle?.close();
    this.#camHandle = null;
    if (needsStop) void this.#sendVelocity(0, 0, 0.5);
  }

  // =========================================================================
  // The console's PARAR
  // =========================================================================

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
  #watchEstop(): () => void {
    let prev: EstopPhase | null = null;
    return $effect.root(() => {
      $effect(() => {
        const next = estop.phase;
        // Only the phase is a dependency; everything else is read and written
        // inside.
        untrack(() => {
          const was = prev;
          prev = next;
          if (estopHalts(was, next)) this.#haltForEstop();
          // Once the stop is no longer in progress the loop may run again —
          // LATCHED, so nothing moves until the operator re-arms. Without this
          // the in-headset re-arm (let go, head to centre) could never happen:
          // the loop that checks for it would stay stopped until a walk press.
          if (!stopInFlight(next)) this.#ensureLoopRunning();
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
  #haltForEstop(): void {
    this.#estopEpoch++;
    this.walking = null;
    this.armsRequested = false;
    this.deadManTripped = true;
    this.latchCause = "estop";
    this.#motionStartedAt = null;
    this.#refusedUntil = 0;
    // PARAR is the stop now; nothing of ours is outstanding after it.
    this.#movedSinceStop = false;
    this.#stopLoop();
  }

  // =========================================================================
  // What the headset shows
  // =========================================================================

  /**
   * Keep the headset's panels current. Returns the teardown.
   *
   * The session is not `$state` (it is a hot-path object), so nothing reactive
   * can read THROUGH it: an effect written as `vr?.setX(value)` short-circuits
   * while there is no session, never reads `value`, and subscribes to
   * nothing — which is how the headset once showed no readiness pill, no latch
   * alert and an empty radar for every session. Each effect reads its value
   * first and pushes it inside `untrack`; `#startVrSession` pushes the current
   * values once a session exists.
   */
  #watchHeadset(): () => void {
    return $effect.root(() => {
      $effect(() => {
        const alert = this.menuAlert;
        untrack(() => this.#vr?.setMenuAlert(alert));
      });
      $effect(() => {
        // `reason` is read as well as the ring so this re-runs when the only
        // thing that changed is WHY there is nothing — an operator staring at
        // an empty dial needs the reason to update even though the (absent)
        // ring did not.
        const ring = this.scanFeed.ring;
        const reason = this.scanFeed.reason;
        untrack(() => this.#vr?.setScanRing(ring, reason));
      });
      $effect(() => {
        // Straight to the headset. This state has been on the page all along,
        // beside the camera panel — and an operator wearing the headset could
        // not see the page, so "i cannot see the camara" on 2026-08-27 had no
        // answer available from inside it. Now the black field says why.
        const reason = this.#cameraReason();
        untrack(() => this.#vr?.setCameraReason(reason));
      });
    });
  }

  /** The page's camera state, phrased for somebody wearing the headset. */
  #cameraReason(): CameraReason {
    if (!this.camConfigured) return cameraReasonFor("error", undefined, false);
    // The same translation the on-page panel uses, so a token like "cors" or
    // "HTTP 503" reaches the headset as words, not as a code.
    const { overlay } = describeRobotCam({
      state: this.camState,
      detail: this.camDetail || null,
      status: this.camStatus,
      url: this.camFrameUrl,
    });
    return cameraReasonFor(
      this.camState,
      overlay?.kind === "message" ? overlay.title : undefined,
    );
  }

  /**
   * Whether the robot can act at all, from the page's live state. The page
   * owns posture and faults, so it computes this and pushes it here.
   *
   * On 2026-08-21 an operator in the headset reported three failures —
   * gestures refused "something about the arm", walk buttons dead, no turning
   * with head movement — which were one fact: the robot was limp in
   * zero_torque. The page already knew. It was behind a headset, which is the
   * same as nowhere.
   */
  setReadiness(readiness: Readiness | null): void {
    this.#readiness = readiness;
    this.#vr?.setMenuReadiness(readiness);
  }

  // =========================================================================
  // Clock and outcome expiry
  // =========================================================================

  #clock(): void {
    this.now = Date.now();
    this.prune();
  }

  /** The walk refusal, if it still describes the robot (pure read, like `outcomeFor`). */
  get currentWalkNotice(): WalkNotice | null {
    const n = this.walkNotice;
    return n && pruneOutcome(n, this.now, this.#posture()) ? n : null;
  }

  /** Drop outcomes that no longer describe the robot (see `pruneOutcome`). */
  prune(): void {
    const posture = this.#posture();
    const next = pruneOutcomes(this.outcomes, this.now, posture);
    if (next !== this.outcomes) this.outcomes = next;
    if (this.walkNotice) {
      const kept = pruneOutcome(this.walkNotice, this.now, posture);
      if (kept !== this.walkNotice) {
        this.walkNotice = kept
          ? { ...kept, stopFailed: this.walkNotice.stopFailed }
          : null;
      }
    }
  }

  // =========================================================================
  // USB link watch
  // =========================================================================
  //
  // `adb reverse` forwards live on the USB connection, not the headset. Unplug
  // the cable, jostle it, or let the device drop its ADB session and ALL of
  // them vanish at once — and nothing recreates them. From inside the headset
  // that is indistinguishable from the server being down, which is exactly how
  // it presented on 2026-08-20: "localhost is not working", with every service
  // on the Mac perfectly healthy.
  //
  // Polling our own origin is enough to tell them apart, because the page was
  // served through the same forward that would have died.
  //
  // WHAT is polled matters. This used to be `HEAD /vr-control?ping`, and
  // SvelteKit renders pages for HEAD: every 5 s, from every open tab, it ran the
  // auth hook, the (protected) layout load (GET /state → the bridge), this
  // page's load (GET /skills → the bridge) and a full SSR. A bridge or tunnel
  // stall longer than the 4 s abort then read as "se perdió el enlace USB… el
  // robot está bien" — confidently blaming a cable for a robot-side problem.
  //
  // A static file is served before SvelteKit's handler (by Vite's asset
  // middleware in dev, by the CDN in production): no hook, no load, no backend.
  // It answers if and only if the path to the console server is up, which is
  // exactly the question. Any HTTP status counts as "up" — `fetch` only rejects
  // when nothing answered at all.
  #startLinkWatch(): void {
    if (this.#linkTimer) return;
    this.#linkTimer = setInterval(async () => {
      try {
        await fetch(`/robots.txt?ping=${Date.now()}`, {
          method: "HEAD",
          cache: "no-store",
          signal: AbortSignal.timeout(LINK_PROBE_TIMEOUT_MS),
        });
        this.linkLost = false;
      } catch {
        this.linkLost = true;
      }
    }, LINK_PROBE_MS);
  }

  // =========================================================================
  // Locomotion
  // =========================================================================

  /**
   * Release every held control, from anywhere.
   *
   * The walk buttons are released only by their own pointer events. That is
   * fine until something removes the button without delivering one: the system
   * "leave immersive" gesture, the headset coming off, the dom-overlay being
   * torn down. `walking` then stays latched — and `buildFrame` derives `walk`
   * and half of `enabled` from it with no freshness gate (deliberately: the
   * buttons must keep working with no headset at all), so the page transmits
   * "walk forward" 30 times a second, indefinitely, with nothing to stop it.
   *
   * The page's own MAX_HOLD_S dead-man cannot catch it either, because that
   * loop is suspended while streaming. So: a window-level release, wired to
   * every event that means "the operator is no longer holding this".
   */
  releaseAllControls(): void {
    // Forget the stick's walk, but do NOT treat whatever it reads next as a
    // fresh push: these events (session end, blur, visibility change, pointer
    // up/cancel) say nothing about where the operator's thumb is. The stick
    // has to be seen at neutral first — see `#stickNeedsNeutral`.
    this.#stickWalk = null;
    this.#stickNeedsNeutral = true;
    // Same rule for the buttons: assume HELD, so a trigger still down from
    // before is not mistaken for a new press that fires a preset. The cost is
    // one release before the next press counts, which is the deliberate act.
    this.#prevButtons = { trigger: true, primary: true };
    if (this.walking !== null) {
      this.walking = null;
      if (!this.bridgeHolds && !this.#loopShouldRun()) {
        this.#stopLoop();
        void this.#sendVelocity(0, 0, 0.5);
      }
    }
  }

  #computeVyaw(): number {
    if (!this.vrActive || this.deadManTripped) return 0;
    if (Date.now() - this.#lastYawSampleAt > VR_STALE_MS) return 0; // tracking stale
    const err = this.#yawErrorRadians;
    const mag = Math.abs(err);
    if (mag < YAW_DEADZONE_RAD) return 0;
    const scaled =
      ((mag - YAW_DEADZONE_RAD) / (YAW_FULL_SCALE_RAD - YAW_DEADZONE_RAD)) *
      YAW_MAX_RAD_S;
    return Math.sign(err) * Math.min(YAW_MAX_RAD_S, scaled);
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
        // no-op on the server, which is what `destroy()`'s comment is about.
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
    if (this.#destroyed) return;
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
      streamHolds: this.bridgeHolds,
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
      ...stampOutcome(outcome, Date.now(), this.#posture()),
      stopFailed: action === "stop_failed",
    };
    this.announcement =
      action === "stop_failed"
        ? `La parada no se confirmó: ${outcome.text}`
        : `No camina: ${outcome.text}`;

    if (action === "refuse") {
      // The robot did not do it, so the button must stop claiming it is.
      this.walking = null;
      this.#movedSinceStop = false;
      this.#motionStartedAt = null;
      this.#refusedUntil = Date.now() + WALK_REFUSAL_BACKOFF_MS;
      // Without VR this loop exists only for the held button; with VR it is
      // also the head-yaw loop, which keeps running and is held off by
      // `#refusedUntil` instead — stopping it outright would leave steering
      // dead until something happened to call `#ensureLoopRunning()`.
      if (!this.#loopShouldRun()) this.#stopLoop();
    }
  }

  #loopShouldRun(): boolean {
    // Observe-only commands nothing, and this is the guard that matters most:
    // the condition at the bottom is true whenever `vrActive` is, and an
    // observer's session sets `vrActive` like any other. Without this line,
    // entering "solo para ver" would start the velocity loop on the head pose.
    if (this.viewOnly) return false;
    // While the socket is up the bridge owns locomotion. Running this loop too
    // would put two writers on SetVelocity. `bridgeHolds`, not `streaming`:
    // a stalled socket is still transmitting.
    if (this.bridgeHolds) return false;
    // PARAR is going out. A tick that fires now stops the loop instead of
    // dispatching; `#watchEstop` restarts it, latched, once the stop is done.
    if (this.stopInFlight) return false;
    return this.walking !== null || this.vrActive;
  }

  #stopLoop(): void {
    if (this.#controlTimer) clearInterval(this.#controlTimer);
    this.#controlTimer = null;
  }

  // --- UI readouts -----------------------------------------------------
  //
  // These used to be written only by `tick()`, which is the LOCOMOTION loop —
  // and that loop is deliberately suspended while the teleop stream is
  // connected, because the bridge is the commander then. So during the normal
  // operating mode the "N deg de giro" readout froze at a stale number and
  // "seguimiento perdido" could never appear: the operator's only in-headset
  // indication that the pose driving the robot had gone stale was dead
  // precisely when it mattered.
  //
  // Readouts are not dispatch. They run whenever there is a session to read.
  #refreshReadouts(): void {
    this.yawDisplayDeg = Math.round((this.#yawErrorRadians * 180) / Math.PI);
    this.trackingStale =
      this.vrActive && Date.now() - this.#lastYawSampleAt > VR_STALE_MS;
    // A walk the THUMBSTICK started ends when the head pose goes stale. The
    // stick only ever releases a walk by being SAMPLED at neutral, and the
    // samples come from the XR frame loop — which stops when the headset comes
    // off, the system menu opens, or tracking is lost. Yaw and arms were
    // already gated on freshness; `walking` was not, so a stick held at that
    // moment kept walking — on the stream and on the HTTP loop alike — until
    // the 8 s hold latch, about 1.6 m with nobody looking. DOM-button walks
    // are left alone: their release comes from pointer events, which do not
    // depend on tracking. (`#tick` runs this too, so the loop catches it even
    // when the readout timer is not running.)
    if (this.trackingStale && this.#stickWalk !== null) {
      if (this.walking === this.#stickWalk) this.stopWalking();
      this.#stickWalk = null;
      // No sample is arriving, so nothing says the thumb has let go.
      this.#stickNeedsNeutral = true;
    }
    const vr = this.#vr;
    if (vr) {
      this.xrCameraLive = this.camLive && vr.cameraHasFrame;
      this.xrCameraEverHadFrame ||= vr.cameraHasFrame;
      this.xrCameraBroken = vr.cameraBroken;
    }
  }

  #ensureReadouts(): void {
    if (this.#uiTimer) return;
    this.#uiTimer = setInterval(() => this.#refreshReadouts(), 200);
  }

  #stopReadouts(): void {
    if (this.#uiTimer) clearInterval(this.#uiTimer);
    this.#uiTimer = null;
    this.trackingStale = false;
  }

  #ensureLoopRunning(): void {
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
    this.#refreshReadouts();

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
      Math.abs(this.#yawErrorRadians) < YAW_DEADZONE_RAD
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
    if (this.walking || this.#destroyed) return;
    // An observer does not walk. Blocked downstream as well (`#loopShouldRun`,
    // `buildFrame`), but not setting the state at all means the buttons do not
    // show a walk that is not happening either.
    if (this.viewOnly) return;
    // PARAR is still going out. A press now would race the stop the operator
    // just asked for — and a hand still on the button is exactly what a
    // reflexive stop leaves behind. The buttons are aria-disabled meanwhile;
    // this is the guard behind them (it covers the stream path too, where
    // `walking` alone drives the bridge).
    if (this.stopInFlight) {
      this.announcement =
        "PARAR en curso: la marcha espera a que termine la parada.";
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
    if (this.bridgeHolds) return;
    this.#ensureLoopRunning();
    this.#tick(); // immediate feedback, don't wait for the next interval tick
  }

  stopWalking(): void {
    if (!this.walking) return;
    this.walking = null;
    if (this.bridgeHolds) return;
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
  #applyWalkAxis(axis: -1 | 0 | 1): void {
    if (this.#destroyed) return;
    if (this.viewOnly) {
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
   * The in-headset preset menu: A cycles, trigger fires.
   *
   * Fires on the PRESS edge, not the release. A gesture is a one-shot with no
   * hold semantics, and waiting for release means an operator who presses and
   * keeps holding gets nothing and presses harder. Dispatches through
   * `runSkill`, so the headset obeys the same lock and outcome rules as the
   * tiles on the page.
   */
  #applyMenuButtons(b: { trigger: boolean; primary: boolean }): void {
    const prev = this.#prevButtons;
    this.#prevButtons = { ...b };
    // Observers cycle the highlight — reading the list is not commanding — but
    // the trigger does nothing.
    if (b.primary && !prev.primary) this.#vr?.advanceMenu();
    if (this.viewOnly) return;
    if (!(b.trigger && !prev.trigger)) return;
    // `menuSelection` returns null for anything unverified, so this cannot
    // dispatch untested motion even if the highlight somehow sat on it.
    const item = this.#vr?.menuSelection;
    if (!item) return;
    void this.runSkill(item.name, item.label, item.name);
  }

  // =========================================================================
  // VR head-yaw
  // =========================================================================

  /**
   * @param observe Enter without becoming a commander — see `viewOnly`.
   */
  async enterVr(
    overlayRoot: HTMLElement | undefined,
    observe = false,
  ): Promise<void> {
    if (this.enteringVr) return;
    this.enteringVr = true;
    this.viewOnly = observe;
    try {
      if (observe) {
        // An already-open teleop socket is the one path that survives every
        // guard on `viewOnly`: the bridge PULLS frames from it, so it does not
        // care what this page thinks its mode is. Close it before the headset
        // goes on, rather than trusting the operator to notice a connected
        // stream and a passive session at the same time.
        this.disconnectStream();
        // Anything already held from before is not this session's to keep: an
        // observer inherits no held buttons.
        this.releaseAllControls();
      }
      await this.#startVrSession(overlayRoot);
    } finally {
      this.enteringVr = false;
      // A session that never came up leaves no observer behind: the next
      // "Entrar en VR" must command, as it says.
      if (!this.vrActive) this.viewOnly = false;
    }
  }

  async #startVrSession(overlayRoot: HTMLElement | undefined): Promise<void> {
    this.vrError = null;
    if (!overlayRoot) return;
    // The headset shares the page's ONE camera connection rather than opening
    // its own: that connection already polls /status, tracks live/stale and
    // reconnects with a fresh URL, which the vision server requires because it
    // CLOSES the stream whenever it goes stale. Two independent connections
    // would also mean two MJPEG streams over the same robot Wi-Fi link.
    const camBase = PUBLIC_ROBOT_CAM_URL.trim();
    const session = new XrTeleopSession(
      {
        onSample: (s) => {
          this.#yawErrorRadians = s.yawErrorRadians;
          this.#headPosition = s.headPosition;
          this.#handLeft = s.left;
          this.#handRight = s.right;
          this.#lastYawSampleAt = Date.now();
          this.#noteHandsVisible();
          this.#applyWalkAxis(s.walkAxis);
          this.#applyMenuButtons(s.buttons);
        },
        onEnd: () => {
          this.vrActive = false;
          // Back to commanding by default. Observe-only is a property of ONE
          // session, chosen on the way in; leaving it latched would mean the
          // next operator's "Entrar en VR" silently did nothing to the robot
          // and they would have no way to tell from inside the headset.
          this.viewOnly = false;
          // The radar is polled for a panel that no longer exists; a 4 Hz fetch
          // outliving the session is the readout-timer leak in another timer.
          this.scanFeed.stop();
          this.xrMode = null;
          this.xrCameraLive = false;
          this.xrCameraEverHadFrame = false;
          this.xrCameraBroken = false;
          this.#vr = null;
          // Ended by `destroy()`, which has already released everything and
          // decided the last stop itself. Going on would send a second one
          // from a page that no longer exists.
          if (this.#destroyed) return;
          // A held walk button does NOT survive the session that was showing
          // it. Nothing else clears `walking` here: the window listeners cover
          // pointerup/cancel/blur/visibilitychange, and session end fires none
          // of them reliably — the document becomes *visible* when an
          // immersive session ends, not hidden.
          //
          // Without this, taking the headset off mid-stride left `walking` set
          // and `loopShouldRun()` true, so the page kept dispatching
          // walk_velocity every 900 ms to a robot nobody was watching, until
          // the 8 s hold latch caught it. That is about 1.6 m of unattended
          // walking.
          this.releaseAllControls();
          // Leaving VR must not leave the arms up: the stream's own frames stop
          // carrying `arms: true`, which is the bridge's cue to ramp the
          // arm_sdk weight back down.
          // The next pulled frame reports `fresh: false` on its own, so there is
          // nothing to push here — that is the point of pulling.
          this.armsRequested = false;
          this.#handLeft = null;
          this.#handRight = null;
          // The readout timer is started on VR entry and was never stopped:
          // `stopReadouts()` had no callers at all, so a 200 ms interval
          // outlived the session and the component, writing $state on a
          // destroyed page for as long as the tab lived.
          this.#stopReadouts();
          if (!this.#loopShouldRun()) {
            this.#stopLoop();
            void this.#sendVelocity(0, 0, 0.5);
          }
        },
      },
      { camera: camBase !== "" },
    );
    this.#pendingVr = session;
    try {
      await session.start(overlayRoot);
    } catch (err) {
      if (!this.#destroyed) this.vrError = xrErrorCopy(err);
      return;
    } finally {
      if (this.#pendingVr === session) this.#pendingVr = null;
    }
    // The page went away while the wearer was on the consent prompt (or the
    // session abandoned itself because `destroy()` asked it to). Adopting it
    // now would hand the headset a session with no controls and no PARAR.
    if (this.#destroyed || !session.active) {
      session.stop();
      return;
    }
    this.#vr = session;
    // The camera connection may already be running — hand over its current
    // URL and liveness so entering VR after the feed is up shows a picture
    // immediately rather than waiting for the next reconnect.
    if (this.camFrameUrl) session.setCameraStream(this.camFrameUrl);
    session.setCameraLive(this.camLive);
    // And what to say if there is no picture yet: entering VR while the feed
    // is already down should open on the reason, not on a blank field that
    // waits for the next change to explain itself.
    session.setCameraReason(this.#cameraReason());
    // The preset list, each entry's verification status from the catalogue.
    session.setMenuItems(this.#menuItems());
    session.setMenuVisible(true);
    // The watchers keep these current, but they cannot fire just because a
    // session now exists — the first values go in here.
    session.setMenuReadiness(this.#readiness);
    session.setMenuAlert(this.menuAlert);
    session.setScanRing(this.scanFeed.ring, this.scanFeed.reason);
    // Off in passthrough: an operator who can see the actual room does not
    // need a drawing of it, and the corner is better spent on the room.
    session.setScanVisible(session.mode !== "immersive-ar");
    this.scanFeed.start();
    this.xrMode = session.mode;
    this.vrActive = true;
    this.#ensureReadouts();
    // A fresh session starts a fresh dead-man window — but does not lift a
    // PARAR latch nobody has re-armed yet (one pressed during the consent
    // prompt, say): accepting a prompt is not a re-arm. The session starts
    // recentred, so the first tick re-arms it anyway if nothing is held.
    if (this.latchCause !== "estop") this.deadManTripped = false;
    this.#motionStartedAt = null;
    this.#ensureLoopRunning();
  }

  exitVr(): void {
    this.#vr?.stop();
  }

  recenterVr(): void {
    // Recentring makes yawError ~0, which is itself the re-arm condition --
    // clearing the latch here just avoids waiting a tick for it.
    this.#vr?.recenter();
    // Nothing re-arms while PARAR is still going out ("Parando…").
    if (this.stopInFlight) return;
    this.deadManTripped = false;
    this.#motionStartedAt = null;
    // The loop may have been stopped by a PARAR or a refusal; a deliberate
    // re-arm should bring head-yaw steering back without needing a walk press.
    this.#ensureLoopRunning();
  }

  // =========================================================================
  // Teleop stream
  // =========================================================================

  /**
   * Build the frame the sender is about to transmit. Pulled ~30 times a
   * second by `#lib/teleop/stream.ts`, never cached — see `buildFrame`, which
   * holds the freshness policy and its tests.
   */
  #buildTeleopFrame(): TeleopFramePayload {
    return buildFrame({
      // Third guard, and the one a caller cannot forget: `buildFrame` returns
      // the idle payload on this before reading any pose. `connectStream()`
      // refuses in observe-only, so nothing should pull this at all — but
      // being wrong about "should" means a headset commanding a robot while
      // its wearer believes it cannot.
      observeOnly: this.viewOnly,
      now: Date.now(),
      vrActive: this.vrActive,
      lastSampleAt: this.#lastYawSampleAt,
      staleAfterMs: VR_STALE_MS,
      yawErrorRadians: this.#yawErrorRadians,
      yawDeadzoneRadians: YAW_DEADZONE_RAD,
      headPosition: this.#headPosition,
      left: this.#handLeft,
      right: this.#handRight,
      walking: this.walking,
      armsRequested: this.armsRequested,
    });
  }

  /** Mirror hand visibility into the UI, cheaply. Called from the XR loop. */
  #noteHandsVisible(): void {
    // Guarded assignment: this runs at XR frame rate, and writing a $state
    // field unconditionally would queue a Svelte re-render 72-120 times a
    // second for a boolean that changes twice a session.
    const seen = this.#handLeft !== null || this.#handRight !== null;
    if (seen !== this.handsVisible) this.handsVisible = seen;
  }

  connectStream(): void {
    // Opening this socket is exactly what makes the headset a commander, and
    // the bridge PULLS from it — so one tap would turn a session somebody
    // entered as passive into one that drives, with nothing visible changing
    // inside the headset. The button is disabled too; this is the guard.
    // `viewOnly` alone, not `&& vrActive`: it is already set while the consent
    // prompt is up, and a socket opened in that window would become a live
    // commander the moment the session ended and `viewOnly` reset.
    if (this.viewOnly) return;
    this.#teleop?.close();
    this.teleopStatus = null;
    const host = PUBLIC_TELEOP_HOST || location.hostname;
    const port = PUBLIC_TELEOP_PORT || "8767";
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    this.#teleop = connectTeleop(`${protocol}://${host}:${port}`, {
      getFrame: () => this.#buildTeleopFrame(),
      onState: (state, detail) => {
        this.teleopState = state;
        this.teleopDetail = detail ?? "";
        if (state === "open") {
          // The bridge is the commander now; make sure this page is not also
          // mid-flight with a walk_velocity call. Only when it could be: the
          // same rule as `destroy()`'s stop, so connecting from an idle page
          // does not log a walk command nobody gave.
          const outstanding = precautionaryStopNeeded({
            movedSinceStop: this.#movedSinceStop,
            walking: this.walking !== null,
            loopActive: this.#controlTimer !== null,
            vrActive: this.vrActive,
          });
          this.#stopLoop();
          if (outstanding) void this.#sendVelocity(0, 0, 0.5);
          return;
        }
        this.armsRequested = false;
        if (state === "stalled") {
          // Deliberately NOT starting the local loop here. The socket is still
          // open, so the bridge may start reading again at any moment — and
          // two writers on SetVelocity is a worse failure than one that has
          // stopped. The bridge's own 0.4 s staleness watchdog has already
          // brought the robot to rest, so nothing is running away while the
          // operator decides. The status line says to reconnect.
          return;
        }
        this.#ensureLoopRunning();
      },
      onStatus: (status) => (this.teleopStatus = status),
    });
  }

  disconnectStream(): void {
    this.armsRequested = false;
    this.#teleop?.close();
    this.#teleop = null;
    this.teleopState = "closed";
    // Cleared too: the status line renders on `teleopDetail` being truthy, so
    // a leftover stall or "session already active" message kept showing after
    // a deliberate disconnect, as though it were current.
    this.teleopDetail = "";
    this.teleopStatus = null;
    // `close()` sets its own `closed` flag BEFORE the socket's close event
    // fires, so `onState("closed")` never reaches us — which means the branch
    // that normally restarts the local loop does not run. Without this,
    // pressing "Desconectar" in the headset silently ends head-yaw steering:
    // the stream is gone and the loop that would take over was stopped when
    // the stream opened. It only came back if the operator happened to press
    // a walk button.
    this.#ensureLoopRunning();
  }

  toggleArms(): void {
    this.armsRequested = !this.armsRequested;
  }

  // =========================================================================
  // Camera mirror
  // =========================================================================
  //
  // Same source as /live-camera: `apps/perception`'s vision container, which
  // is the process that already owns the D435i — and a V4L2 device has exactly
  // one owner, so it is the only thing that CAN show this picture.
  //
  // This page previously used its own WebSocket relay over teleimager's ZeroMQ
  // feed. That path is gone: teleimager is not running on the robot, and the
  // `/dev/video4` it opened no longer exists (colour moved to video5). Two
  // camera transports where one cannot work is worse than one that does.
  //
  // The status poll is not decoration. An <img> pointed at an MJPEG stream
  // keeps showing its last frame forever, with no event — so a camera that
  // died two minutes ago looks identical to a working one. The picture comes
  // from the <img>; whether it is LIVE comes from /status.

  /** Open the camera connection once. Safe to call repeatedly. */
  ensureCamera(): void {
    if (this.#camHandle) return;
    this.connectCamera();
  }

  /**
   * A fresh stream URL on the existing connection — what "Reconectar" and the
   * camera view's own image-error retry ask for. The vision server CLOSES the
   * stream when it goes stale, so a retry has to change the URL; the handle's
   * `reconnect()` does that and reads /status immediately.
   */
  reconnectCamera(): void {
    if (this.#camHandle) this.#camHandle.reconnect();
    else this.connectCamera();
  }

  connectCamera(): void {
    this.#camHandle?.close();
    this.#camHandle = null;
    const base = PUBLIC_ROBOT_CAM_URL.trim();
    if (!base) {
      this.camState = "error";
      this.camDetail = "PUBLIC_ROBOT_CAM_URL no está configurado.";
      return;
    }
    this.#camHandle = connectRobotCamera(base, {
      onState: (state, detail) => {
        this.camState = state;
        this.camDetail = detail ?? "";
      },
      onStatus: (status) => {
        this.camStatus = status;
        this.camLive = status?.live ?? false;
        // Stale is dimmed in the headset rather than hidden: losing the view
        // mid-motion is worse than an obviously old one.
        this.#vr?.setCameraLive(this.camLive);
      },
      onStreamUrl: (url) => {
        this.camFrameUrl = url;
        // Same URL to the headset. It changes on every reconnect (the source
        // cache-busts it), and the XR layer must follow — reusing a dead MJPEG
        // URL is a retry that does nothing, so a stall would otherwise freeze
        // the headset view on its last frame for the rest of the session.
        this.#vr?.setCameraStream(url);
      },
    });
  }

  // =========================================================================
  // Presets: bring-up steps and gestures
  // =========================================================================

  /** Whether a tile must refuse a tap right now, because another one holds the lock. */
  lockedOut(key: string): boolean {
    // An observer runs nothing — the gesture and bring-up tiles are reachable
    // in passthrough, where the overlay composites. PARAR is not a tile.
    if (this.viewOnly) return true;
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
    return o ? pruneOutcome(o, this.now, this.#posture()) : null;
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
    this.#vr?.setMenuBusy(key);
    this.#vr?.setMenuStatus(null);
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
      this.#vr?.setMenuBusy(this.lock?.key ?? null);
    }
    if (this.#destroyed) return;
    // Whatever the result was goes to the headset verbatim: "Enviado" and
    // "Enviado (sin confirmar)" mean very different things about a robot the
    // wearer cannot see.
    this.#vr?.setMenuStatus({
      text: outcome.text,
      kind: outcome.kind === "unverified" ? "warn" : outcome.kind,
    });
    this.outcomes = {
      ...this.outcomes,
      [key]: stampOutcome(outcome, Date.now(), this.#posture()),
    };
    this.now = Date.now();
    this.announcement = `${label}: ${outcome.text}`;
  }
}
