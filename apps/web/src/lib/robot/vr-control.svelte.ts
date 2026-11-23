/**
 * VR control — the Quest 3 teleop feature's state and dispatch, end to end:
 * hold-to-walk, WebXR head-yaw turning, the teleop stream and arm mirror,
 * one-shot preset skills, and the robot camera connection the headset shares.
 *
 * This used to live inline in a 1460-line `+page.svelte`, then in this one
 * 1760-line class. It is a facade now: the page and `#lib/components/vr/*` talk
 * only to `VrControl`, with the same API as before, and the work is done by
 * focused units that each moved here intact, comments and all:
 *
 *   vr-stop.svelte.ts          PARAR: the e-stop watch, what it halts, the grips chord
 *   vr-walk.svelte.ts          hold-to-walk, stick, head yaw, the /invoke loop, the dead-man
 *   vr-teleop-link.svelte.ts   the teleop WebSocket and the arm request
 *   vr-xr.svelte.ts            the WebXR session: consent prompt, adoption, headset pushes
 *   vr-camera.svelte.ts        the page's one MJPEG connection, shared with the headset
 *   vr-presets.svelte.ts       bring-up and gesture tiles, the dispatch lock, outcomes
 *   vr-link-watch.svelte.ts    the USB (adb reverse) link probe
 *
 * What stays here is the wiring between them, and the three places where one
 * event has to reach several units in a fixed order: the session callbacks
 * (`#sessionCallbacks`), `releaseAllControls`, and `destroy`. Every safety rule
 * below is one a headset session found the hard way. The decisions that can be
 * tested without a browser live in `./vr-logic.ts`.
 *
 * `/invoke` is admin-only for every non-safety skill (`apps/back/src/routes/
 * skills.ts`), so walking through `walk_velocity` and every preset needs
 * `role: "admin"`. The teleop stream does not go through apps/back at all.
 */

import { browser } from "$app/env";
import { buildFrame, type TeleopFramePayload } from "#lib/teleop/stream.js";
import type {
  HandSample,
  XrButtons,
  XrTeleopCallbacks,
} from "#lib/webxr/xr-teleop.js";
import {
  alertFor,
  type MenuAlert,
  type MenuItem,
  type VoiceLine,
  type Readiness,
} from "#lib/webxr/menu-layer.js";
import { MENU_IDLE_CLOSE_MS } from "#lib/webxr/hud-layout.js";
import { ScanFeed } from "./scan.svelte";
import { VrStop } from "./vr-stop.svelte";
import {
  VR_STALE_MS,
  WalkController,
  YAW_DEADZONE_RAD,
  type WalkDir,
  type WalkNotice,
} from "./vr-walk.svelte";
import { TeleopLink } from "./vr-teleop-link.svelte";
import { XrBinding, type XrSessionFactory } from "./vr-xr.svelte";
import { VrCamera } from "./vr-camera.svelte";
import { VrPresets } from "./vr-presets.svelte";
import { LinkWatch } from "./vr-link-watch.svelte";

export { STOP_CHORD_MS } from "./vr-stop.svelte";
export { MAX_HOLD_S, WALK_VX } from "./vr-walk.svelte";
export type { WalkDir, WalkNotice } from "./vr-walk.svelte";
export type { XrSession, XrSessionFactory } from "./vr-xr.svelte";

export type VrControlOptions = {
  /** The live posture token, or null while offline. Outcomes key off it. */
  posture: () => string | null;
  /**
   * The in-headset preset menu, each entry's `verified` straight from the
   * catalogue. Read when a session starts.
   */
  menuItems?: () => readonly MenuItem[];
  /** `batteryWarning` for the live reading, shown in the headset's alert band. */
  battery?: () => "warn" | "alert" | null;
  /**
   * "Hablar con el robot" (vr-voice.svelte.ts). `onItem` gets every `voice:*`
   * menu selection and says whether it took it; `onTalk` is push-to-talk
   * (B/Y held / released); `cancelTalk` drops a recording without sending it;
   * `busy` is an agent turn running, during which head yaw does not steer.
   */
  voice?: {
    onItem: (name: string) => boolean;
    onTalk: (down: boolean) => void;
    cancelTalk: () => void;
    busy: () => boolean;
  };
  /**
   * Builds the WebXR session. Default: a real `XrTeleopSession`. Injected so
   * the runtime tests drive a fake one without mocking the xr-teleop module
   * for the whole test process.
   */
  xrSession?: XrSessionFactory;
};

export class VrControl {
  // --- WebXR -----------------------------------------------------------
  vrActive = $state(false);
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
  //: socket the bridge PULLS frames from, and the local walk_velocity loop
  //: (`vr-walk.svelte.ts`) — so one flag on one branch would not be enough.
  //: Closed at three points:
  //:   1. entering observe-only calls `disconnectStream()`, and
  //:      `connectStream()` refuses while the session lives, so the socket
  //:      does not exist;
  //:   2. the walk loop's `#loopShouldRun()` returns false, so the local loop
  //:      never starts — it keys on `vrActive`, which IS set for an observer;
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

  /** The last result, phrased for a screen reader. One polite live region. */
  announcement = $state("");

  // --- the units -----------------------------------------------------------
  #walk: WalkController;
  #stop: VrStop;
  #teleop: TeleopLink;
  #xr: XrBinding;
  #camera: VrCamera;
  #presets: VrPresets;
  #link = new LinkWatch();

  // --- plain fields: hot-path or non-rendered state ------------------------
  #posture: () => string | null;
  #uiTimer: ReturnType<typeof setInterval> | null = null;
  #clockTimer: ReturnType<typeof setInterval> | null = null;
  #unwatchEstop: (() => void) | null = null;
  #unwatchHeadset: (() => void) | null = null;
  //: Previous frame's button state, for edge detection. The session reports
  //: held-state at display rate, so without this a single press of A would
  //: cycle the highlight through the whole list in under a tenth of a second
  //: and the trigger would fire the same skill 70 times.
  #prevButtons: XrButtons = {
    trigger: false,
    primary: false,
    secondary: false,
    grips: 0,
  };
  //: When the headset panel was last opened or moved (Date.now() ms). It folds
  //: itself after MENU_IDLE_CLOSE_MS untouched — see hud-layout.ts.
  #menuTouchedAt = 0;
  #voice: VrControlOptions["voice"];
  #battery: () => "warn" | "alert" | null;
  // Head position and both wrists, at XR frame rate. Plain for the same reason
  // as the walk loop's yaw: these change 72-120 times a second and nothing
  // renders them directly.
  #headPosition: [number, number, number] = [0, 1.6, 0];
  #handLeft: HandSample | null = null;
  #handRight: HandSample | null = null;
  #destroyed = false;

  constructor(opts: VrControlOptions) {
    this.#posture = opts.posture;
    this.#voice = opts.voice;
    this.#battery = opts.battery ?? (() => null);
    const menuItems = opts.menuItems ?? (() => []);
    const destroyed = () => this.#destroyed;
    const viewOnly = () => this.viewOnly;
    const headset = () => this.#xr.session;
    const announce = (text: string) => (this.announcement = text);

    this.#walk = new WalkController({
      posture: opts.posture,
      viewOnly,
      vrActive: () => this.vrActive,
      bridgeHolds: () => this.#teleop.bridgeHolds,
      destroyed,
      voiceBusy: () => this.#voice?.busy() ?? false,
      announce,
      refreshReadouts: () => this.#refreshReadouts(),
    });
    this.#teleop = new TeleopLink({
      viewOnly,
      destroyed,
      getFrame: () => this.#buildTeleopFrame(),
      onCommanding: () => this.#walk.yieldToStream(),
      onReleased: () => this.#walk.ensureLoopRunning(),
    });
    // PARAR, in the order it halts things: the socket, the walk loop, the arms.
    this.#stop = new VrStop({
      stopStream: () => this.#teleop.stop(),
      latchWalk: () => this.#walk.latchForEstop(),
      dropArms: () => (this.#teleop.armsRequested = false),
      resume: () => this.#walk.ensureLoopRunning(),
    });
    this.#camera = new VrCamera(headset);
    this.#xr = new XrBinding(
      {
        destroyed,
        camera: this.#camera,
        scanFeed: this.scanFeed,
        menuItems,
        menuAlert: () => this.menuAlert,
      },
      opts.xrSession,
    );
    this.#presets = new VrPresets({
      posture: opts.posture,
      viewOnly,
      destroyed,
      headset,
      announce,
    });
  }

  // =========================================================================
  // State the page renders, read through to the unit that owns it
  // =========================================================================

  // --- locomotion (vr-walk) ----------------------------------------------
  get walking(): WalkDir | null {
    return this.#walk.walking;
  }
  get walkNotice(): WalkNotice | null {
    return this.#walk.walkNotice;
  }
  /** The dead-man / PARAR latch. See `WalkController.deadManTripped`. */
  get deadManTripped(): boolean {
    return this.#walk.deadManTripped;
  }
  get latchCause(): "deadman" | "estop" {
    return this.#walk.latchCause;
  }
  get lastMoveSimulated(): boolean {
    return this.#walk.lastMoveSimulated;
  }
  get yawPaused(): boolean {
    return this.#walk.yawPaused;
  }
  /**
   * The console's PARAR is still going out ("Parando…"). Nothing here moves
   * meanwhile — reactive, because it reads the shared e-stop store, so the
   * walk buttons can say so.
   */
  get stopInFlight(): boolean {
    return this.#walk.stopInFlight;
  }
  /** The walk refusal, if it still describes the robot (pure read, like `outcomeFor`). */
  get currentWalkNotice(): WalkNotice | null {
    return this.#walk.currentNotice(this.now);
  }

  // --- WebXR (vr-xr) -----------------------------------------------------
  get xrProbe(): XrBinding["xrProbe"] {
    return this.#xr.xrProbe;
  }
  get vrError(): XrBinding["vrError"] {
    return this.#xr.vrError;
  }
  get xrMode(): string | null {
    return this.#xr.xrMode;
  }
  get xrCameraLive(): boolean {
    return this.#xr.xrCameraLive;
  }
  get xrCameraEverHadFrame(): boolean {
    return this.#xr.xrCameraEverHadFrame;
  }
  get xrCameraBroken(): boolean {
    return this.#xr.xrCameraBroken;
  }
  get vrSupported(): boolean {
    return this.#xr.vrSupported;
  }
  get arSupported(): boolean {
    return this.#xr.arSupported;
  }
  get handTrackingSupported(): boolean {
    return this.#xr.handTrackingSupported;
  }

  // --- USB link watch (vr-link-watch) --------------------------------------
  get linkLost(): boolean {
    return this.#link.linkLost;
  }

  // --- teleop stream (vr-teleop-link) --------------------------------------
  get teleopState(): TeleopLink["teleopState"] {
    return this.#teleop.teleopState;
  }
  get teleopDetail(): string {
    return this.#teleop.teleopDetail;
  }
  get teleopStatus(): TeleopLink["teleopStatus"] {
    return this.#teleop.teleopStatus;
  }
  get armsRequested(): boolean {
    return this.#teleop.armsRequested;
  }
  get streaming(): boolean {
    return this.#teleop.streaming;
  }
  /** The bridge holds the teleop lease — open OR stalled. See `TeleopLink.bridgeHolds`. */
  get bridgeHolds(): boolean {
    return this.#teleop.bridgeHolds;
  }

  // --- camera (vr-camera) ------------------------------------------------
  get camState(): VrCamera["camState"] {
    return this.#camera.camState;
  }
  get camDetail(): string {
    return this.#camera.camDetail;
  }
  get camFrameUrl(): string | null {
    return this.#camera.camFrameUrl;
  }
  get camLive(): boolean {
    return this.#camera.camLive;
  }
  get camStatus(): VrCamera["camStatus"] {
    return this.#camera.camStatus;
  }
  /** Whether this console has a camera URL at all. See `VrCamera.camConfigured`. */
  get camConfigured(): boolean {
    return this.#camera.camConfigured;
  }

  // --- presets (vr-presets) ----------------------------------------------
  get lock(): VrPresets["lock"] {
    return this.#presets.lock;
  }
  get inFlight(): VrPresets["inFlight"] {
    return this.#presets.inFlight;
  }
  get outcomes(): VrPresets["outcomes"] {
    return this.#presets.outcomes;
  }
  /** Wall clock, ticked once a second, so outcome ages ("hace 12 s") move. */
  get now(): number {
    return this.#presets.now;
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
      this.#battery(),
      this.yawPaused,
    );
  }

  /** Re-read `menuItems` into a running session (voice mode swaps the list). */
  refreshMenu(): void {
    this.#xr.refreshMenu();
  }

  /** The conversation panel in the headset, or null for the gestures menu. */
  setMenuVoice(lines: readonly VoiceLine[] | null): void {
    this.#xr.setMenuVoice(lines);
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

    this.#link.start();
    this.#unwatchEstop ??= this.#stop.watch();
    this.#unwatchHeadset ??= this.#xr.watch();

    this.#xr.probe();

    this.#clockTimer = setInterval(() => this.#clock(), 1000);

    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", onHidden);
      this.#link.stop();
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
    const needsStop = this.#walk.precautionaryStopNeeded();
    this.#destroyed = true;
    this.#unwatchEstop?.();
    this.#unwatchEstop = null;
    this.#unwatchHeadset?.();
    this.#unwatchHeadset = null;
    this.scanFeed.stop();
    this.#walk.walking = null;
    this.#teleop.armsRequested = false;
    // The live session, and one still on the consent prompt (`XrBinding.start`
    // checks `destroyed` after its await as well).
    this.#xr.abandon();
    // Closing the stream sends one last released frame, so the bridge stops on
    // a frame rather than waiting out its staleness timeout.
    this.#teleop.close();
    this.#walk.stopLoop();
    // The readout timer is separate from the control loop and `stopLoop()`
    // does not touch it. Left running, it fires every 200 ms and writes state
    // on a destroyed component for as long as the tab is open, and every
    // return to this page adds another one.
    this.#stopReadouts();
    this.#camera.close();
    if (needsStop) this.#walk.sendStop();
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
    this.#xr.setReadiness(readiness);
  }

  // =========================================================================
  // Clock and outcome expiry
  // =========================================================================

  #clock(): void {
    this.#presets.now = Date.now();
    this.prune();
  }

  /** Drop outcomes that no longer describe the robot (see `pruneOutcome`). */
  prune(): void {
    const posture = this.#posture();
    this.#presets.prune(posture);
    this.#walk.pruneNotice(this.now, posture);
  }

  // =========================================================================
  // Locomotion (vr-walk)
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
    // The stick has to be seen at neutral before it walks again.
    this.#walk.forgetStick();
    // Same rule for the buttons: assume HELD, so a trigger still down from
    // before is not mistaken for a new press that fires a preset. The cost is
    // one release before the next press counts, which is the deliberate act.
    // B/Y too (a held talk button is not a new utterance; the recording is
    // dropped), but NOT the grips: a PARAR chord still held should fire.
    this.#prevButtons = {
      trigger: true,
      primary: true,
      secondary: true,
      grips: 0,
    };
    this.#voice?.cancelTalk();
    this.#walk.releaseHeld();
  }

  startWalking(dir: WalkDir): void {
    this.#walk.startWalking(dir);
  }

  stopWalking(): void {
    this.#walk.stopWalking();
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
    this.yawDisplayDeg = Math.round(
      (this.#walk.yawErrorRadians * 180) / Math.PI,
    );
    this.trackingStale =
      this.vrActive && Date.now() - this.#walk.lastYawSampleAt > VR_STALE_MS;
    // A walk the THUMBSTICK started ends when the head pose goes stale.
    this.#walk.noteTracking(this.trackingStale);
    this.#xr.noteCameraFrames(this.#camera.camLive);
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

  /**
   * The in-headset preset menu: A opens it, then cycles; trigger fires.
   *
   * Fires on the PRESS edge, not the release. A gesture is a one-shot with no
   * hold semantics, and waiting for release means an operator who presses and
   * keeps holding gets nothing and presses harder. Dispatches through
   * `runSkill`, so the headset obeys the same lock and outcome rules as the
   * tiles on the page.
   *
   * The panel folds after a gesture is fired and after MENU_IDLE_CLOSE_MS with
   * nothing pressed, so it is never left covering the picture. The trigger
   * fires nothing while it is folded: `menuSelection` is null then.
   */
  #applyMenuButtons(b: XrButtons): void {
    const now = Date.now();
    const prev = this.#prevButtons;
    this.#prevButtons = { ...b };
    const session = this.#xr.session;
    // Observers open and cycle the panel — reading the list is not
    // commanding — but the trigger does nothing.
    if (b.primary && !prev.primary) {
      session?.advanceMenu();
      this.#menuTouchedAt = now;
    }
    if (
      session?.menuExpanded &&
      now - this.#menuTouchedAt > MENU_IDLE_CLOSE_MS
    ) {
      // Folds the gestures only: the voice panel forces itself open, since a
      // reply into a folded panel is a reply nobody reads.
      session.closeMenu();
    }
    if (this.viewOnly) return;
    // Push-to-talk on B/Y: an utterance commands the robot, so observers get
    // none (the guard above).
    if (b.secondary !== prev.secondary) this.#voice?.onTalk(b.secondary);
    if (!(b.trigger && !prev.trigger)) return;
    // `menuSelection` returns null for anything unverified, so this cannot
    // dispatch untested motion even if the highlight somehow sat on it.
    const item = this.#xr.session?.menuSelection;
    if (!item) return;
    this.#menuTouchedAt = now;
    if (item.name.startsWith("voice:")) {
      this.#voice?.onItem(item.name);
      return;
    }
    // Fold first: the outcome lands in the status bar, over an unobstructed
    // picture of the robot doing what was asked.
    session?.closeMenu();
    void this.runSkill(item.name, item.label, item.name);
  }

  // =========================================================================
  // VR head-yaw (vr-xr)
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
    // Built, the consent prompt waited out and adopted — or not, in which case
    // nothing below runs: no readouts, no loop, on a session nobody drives.
    if (!(await this.#xr.start(overlayRoot, this.#sessionCallbacks()))) return;
    this.vrActive = true;
    this.#ensureReadouts();
    // A fresh session starts a fresh dead-man window — but does not lift a
    // PARAR latch nobody has re-armed yet. See `WalkController.freshSession`.
    this.#walk.freshSession();
  }

  /**
   * What the headset's events do. Each one reaches several units, and the
   * order inside each is part of what makes it safe.
   */
  #sessionCallbacks(): XrTeleopCallbacks {
    return {
      onSample: (s) => {
        this.#stop.applyChord(s.buttons.grips, Date.now());
        this.#walk.yawErrorRadians = s.yawErrorRadians;
        this.#headPosition = s.headPosition;
        this.#handLeft = s.left;
        this.#handRight = s.right;
        this.#walk.lastYawSampleAt = Date.now();
        this.#walk.noteYawHold(s.yawErrorRadians);
        this.#noteHandsVisible();
        this.#walk.applyWalkAxis(s.walkAxis);
        this.#applyMenuButtons(s.buttons);
      },
      onVisibility: (v) => {
        if (v === "visible") return;
        // Samples stop under the overlay: zero yaw and arms now, not VR_STALE_MS later.
        this.#walk.lastYawSampleAt = 0;
        // Coming back is not a deliberate act: steer again only from centre.
        this.#walk.holdYawUntilCentred();
        this.releaseAllControls();
      },
      onEnd: () => {
        this.vrActive = false;
        // A recording still held when the headset came off is not a command.
        this.#voice?.cancelTalk();
        // Back to commanding by default. Observe-only is a property of ONE
        // session, chosen on the way in; leaving it latched would mean the
        // next operator's "Entrar en VR" silently did nothing to the robot
        // and they would have no way to tell from inside the headset.
        this.viewOnly = false;
        // The radar is polled for a panel that no longer exists; a 4 Hz fetch
        // outliving the session is the readout-timer leak in another timer.
        this.scanFeed.stop();
        // Mode, the headset camera flags, its face poll, and the handle.
        this.#xr.detach();
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
        this.#teleop.armsRequested = false;
        this.#handLeft = null;
        this.#handRight = null;
        // The readout timer is started on VR entry and was never stopped:
        // `stopReadouts()` had no callers at all, so a 200 ms interval
        // outlived the session and the component, writing $state on a
        // destroyed page for as long as the tab lived.
        this.#stopReadouts();
        this.#walk.stopUnlessNeeded();
      },
    };
  }

  exitVr(): void {
    this.#xr.stop();
  }

  recenterVr(): void {
    // Recentring makes yawError ~0, which is itself the re-arm condition.
    this.#xr.session?.recenter();
    this.#walk.rearm();
  }

  // =========================================================================
  // Teleop stream (vr-teleop-link)
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
      lastSampleAt: this.#walk.lastYawSampleAt,
      staleAfterMs: VR_STALE_MS,
      yawErrorRadians: this.#walk.yawSteers() ? this.#walk.yawErrorRadians : 0,
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

  connectStream(): Promise<void> {
    return this.#teleop.connect();
  }

  disconnectStream(): void {
    this.#teleop.disconnect();
  }

  toggleArms(): void {
    this.#teleop.toggleArms();
  }

  // =========================================================================
  // Camera mirror (vr-camera)
  // =========================================================================

  /** Open the camera connection once. Safe to call repeatedly. */
  ensureCamera(): void {
    this.#camera.ensure();
  }

  /** A fresh stream URL on the existing connection. See `VrCamera.reconnect`. */
  reconnectCamera(): void {
    this.#camera.reconnect();
  }

  connectCamera(): void {
    this.#camera.connect();
  }

  // =========================================================================
  // Presets: bring-up steps and gestures (vr-presets)
  // =========================================================================

  /** Whether a tile must refuse a tap right now, because another one holds the lock. */
  lockedOut(key: string): boolean {
    return this.#presets.lockedOut(key);
  }

  /** The tile's last result, if it still describes the robot. */
  outcomeFor(key: string): ReturnType<VrPresets["outcomeFor"]> {
    return this.#presets.outcomeFor(key);
  }

  /** Dispatch one preset through `/skills/:name/invoke`. See `VrPresets.runSkill`. */
  runSkill(
    key: string,
    label: string,
    skill: string,
    body: Record<string, unknown> = {},
  ): Promise<void> {
    return this.#presets.runSkill(key, label, skill, body);
  }
}
