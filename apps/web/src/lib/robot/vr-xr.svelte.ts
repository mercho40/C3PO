/**
 * The WebXR session on /vr-control: building it, the consent-prompt window,
 * adopting it, and everything pushed into or read out of it.
 *
 * What the session's callbacks DO (`onSample`, `onVisibility`, `onEnd`) is
 * `VrControl`'s, because every one of them reaches into the walk loop, the
 * teleop link and the voice at once, in an order that matters. This file is
 * the controller's single handle on the headset object itself.
 *
 * Moved out of `vr-control.svelte.ts` intact.
 */

import { untrack } from "svelte";
import { PUBLIC_API_URL, PUBLIC_ROBOT_CAM_URL } from "$app/env/public";
import {
  XrTeleopSession,
  checkXrSupport,
  type XrTeleopCallbacks,
  type XrTeleopOptions,
  type XrTeleopSupport,
} from "#lib/webxr/xr-teleop.js";
import type {
  MenuAlert,
  MenuItem,
  Readiness,
  VoiceLine,
} from "#lib/webxr/menu-layer.js";
import { camCredentials } from "./mjpeg-camera";
import { faceBoxesEnabled, faceSeenUrl, pollFaceSeen } from "./face-seen";
import type { ScanFeed } from "./scan.svelte";
import type { VrCamera } from "./vr-camera.svelte";
import { xrErrorCopy, type XrErrorCopy } from "./vr-logic";

/** What the controller uses of an XR session: `XrTeleopSession`'s public face. */
export type XrSession = Pick<XrTeleopSession, keyof XrTeleopSession>;

/** Builds the session `enterVr` starts. Tests pass a fake; the page passes nothing. */
export type XrSessionFactory = (
  callbacks: XrTeleopCallbacks,
  options: XrTeleopOptions,
) => XrSession;

/** What the binding pushes into a session, read when it is pushed. */
export type XrHost = {
  /** The page has been torn down. */
  destroyed: () => boolean;
  camera: VrCamera;
  scanFeed: ScanFeed;
  /** The preset menu, each entry's `verified` from the catalogue. */
  menuItems: () => readonly MenuItem[];
  /** The latch / battery band (`VrControl.menuAlert`). */
  menuAlert: () => MenuAlert;
};

export class XrBinding {
  /**
   * Tri-state on purpose. The capability flags used to start `false` and flip
   * once `checkXrSupport()` resolved, so the Quest first rendered — SSR
   * included — "WebXR no está disponible en este navegador", and a desktop
   * flashed a hand-tracking hint telling it to change a Quest setting.
   */
  xrProbe = $state.raw<"checking" | XrTeleopSupport>("checking");
  vrError = $state.raw<XrErrorCopy | null>(null);
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

  #host: XrHost;
  #factory: XrSessionFactory;
  #vr: XrSession | null = null;
  // The session between `new` and `start()` resolving — which includes the
  // headset's consent prompt, for as long as the wearer leaves it up. `#vr` is
  // only assigned after that await, so a page left during the prompt used to
  // call `this.#vr?.stop()` on null, and the session then started DETACHED:
  // no controls, no PARAR, and this page's 900 ms loop still sending head-yaw
  // walk_velocity. `abandon()` stops this one too.
  #pendingVr: XrSession | null = null;
  #stopFaces: (() => void) | null = null;
  #menuVoice: readonly VoiceLine[] | null = null;
  // Pushed by the page (it owns live posture and faults); kept here so a
  // session that starts later opens on the current value.
  #readiness: Readiness | null = null;

  constructor(host: XrHost, factory?: XrSessionFactory) {
    this.#host = host;
    this.#factory = factory ?? ((cb, o) => new XrTeleopSession(cb, o));
  }

  /** The live (adopted) session, or null. Never the one on the consent prompt. */
  get session(): XrSession | null {
    return this.#vr;
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

  /** Ask the browser what it supports. Once, from `mount()`. */
  probe(): void {
    void checkXrSupport().then((s) => {
      if (!this.#host.destroyed()) this.xrProbe = s;
    });
  }

  /** Re-read `menuItems` into a running session (voice mode swaps the list). */
  refreshMenu(): void {
    this.#vr?.setMenuItems(this.#host.menuItems());
  }

  /** The conversation panel in the headset, or null for the gestures menu. */
  setMenuVoice(lines: readonly VoiceLine[] | null): void {
    this.#menuVoice = lines;
    this.#vr?.setMenuVoice(lines);
  }

  /** See `VrControl.setReadiness`. */
  setReadiness(readiness: Readiness | null): void {
    this.#readiness = readiness;
    this.#vr?.setMenuReadiness(readiness);
  }

  /**
   * Keep the headset's panels current. Returns the teardown.
   *
   * The session is not `$state` (it is a hot-path object), so nothing reactive
   * can read THROUGH it: an effect written as `vr?.setX(value)` short-circuits
   * while there is no session, never reads `value`, and subscribes to
   * nothing — which is how the headset once showed no readiness pill, no latch
   * alert and an empty radar for every session. Each effect reads its value
   * first and pushes it inside `untrack`; `start` pushes the current values
   * once a session exists.
   */
  watch(): () => void {
    return $effect.root(() => {
      $effect(() => {
        const alert = this.#host.menuAlert();
        untrack(() => this.#vr?.setMenuAlert(alert));
      });
      $effect(() => {
        // `reason` is read as well as the ring so this re-runs when the only
        // thing that changed is WHY there is nothing — an operator staring at
        // an empty dial needs the reason to update even though the (absent)
        // ring did not.
        const ring = this.#host.scanFeed.ring;
        const reason = this.#host.scanFeed.reason;
        untrack(() => this.#vr?.setScanRing(ring, reason));
      });
      $effect(() => {
        // Straight to the headset. This state has been on the page all along,
        // beside the camera panel — and an operator wearing the headset could
        // not see the page, so "i cannot see the camara" on 2026-08-27 had no
        // answer available from inside it. Now the black field says why.
        const reason = this.#host.camera.reason();
        untrack(() => this.#vr?.setCameraReason(reason));
      });
    });
  }

  /**
   * Build a session, wait out the consent prompt, and adopt it — or not.
   * True when a session was adopted (it is `session` from then on).
   */
  async start(
    overlayRoot: HTMLElement | undefined,
    callbacks: XrTeleopCallbacks,
  ): Promise<boolean> {
    this.vrError = null;
    if (!overlayRoot) return false;
    // The headset shares the page's ONE camera connection rather than opening
    // its own: that connection already polls /status, tracks live/stale and
    // reconnects with a fresh URL, which the vision server requires because it
    // CLOSES the stream whenever it goes stale. Two independent connections
    // would also mean two MJPEG streams over the same robot Wi-Fi link.
    const camBase = PUBLIC_ROBOT_CAM_URL.trim();
    const session = this.#factory(callbacks, { camera: camBase !== "" });
    this.#pendingVr = session;
    try {
      await session.start(overlayRoot);
    } catch (err) {
      if (!this.#host.destroyed()) this.vrError = xrErrorCopy(err);
      return false;
    } finally {
      if (this.#pendingVr === session) this.#pendingVr = null;
    }
    // The page went away while the wearer was on the consent prompt (or the
    // session abandoned itself because `abandon()` asked it to). Adopting it
    // now would hand the headset a session with no controls and no PARAR.
    if (this.#host.destroyed() || !session.active) {
      session.stop();
      return false;
    }
    this.#vr = session;
    const camera = this.#host.camera;
    // The camera connection may already be running — hand over its current
    // URL and liveness so entering VR after the feed is up shows a picture
    // immediately rather than waiting for the next reconnect.
    const withCookie = camCredentials(camBase, PUBLIC_API_URL) === "include";
    if (camera.camFrameUrl)
      session.setCameraStream(camera.camFrameUrl, withCookie);
    session.setCameraLive(camera.camLive);
    // Face boxes composed into the headset picture, polled only for the
    // session's life (it ends in `detach`), and only if this browser wants them.
    if (camBase && faceBoxesEnabled()) {
      this.#stopFaces = pollFaceSeen(
        faceSeenUrl(camBase),
        (seen, at) => this.#vr?.setCameraFaces(seen, at),
        { credentials: camCredentials(camBase, PUBLIC_API_URL) },
      );
    }
    // And what to say if there is no picture yet: entering VR while the feed
    // is already down should open on the reason, not on a blank field that
    // waits for the next change to explain itself.
    session.setCameraReason(camera.reason());
    // The preset list, each entry's verification status from the catalogue.
    session.setMenuItems(this.#host.menuItems());
    session.setMenuVoice(this.#menuVoice);
    session.setMenuVisible(true);
    // The watchers keep these current, but they cannot fire just because a
    // session now exists — the first values go in here.
    session.setMenuReadiness(this.#readiness);
    session.setMenuAlert(this.#host.menuAlert());
    const scan = this.#host.scanFeed;
    session.setScanRing(scan.ring, scan.reason);
    // Off in passthrough: an operator who can see the actual room does not
    // need a drawing of it, and the corner is better spent on the room.
    session.setScanVisible(session.mode !== "immersive-ar");
    scan.start();
    this.xrMode = session.mode;
    return true;
  }

  /** The readout snapshot's camera half: what the headset layer has decoded. */
  noteCameraFrames(camLive: boolean): void {
    const vr = this.#vr;
    if (vr) {
      this.xrCameraLive = camLive && vr.cameraHasFrame;
      this.xrCameraEverHadFrame ||= vr.cameraHasFrame;
      this.xrCameraBroken = vr.cameraBroken;
    }
  }

  /** The session ended (`onEnd`): drop it and everything scoped to it. */
  detach(): void {
    this.xrMode = null;
    this.xrCameraLive = false;
    this.xrCameraEverHadFrame = false;
    this.xrCameraBroken = false;
    this.#stopFaces?.();
    this.#stopFaces = null;
    this.#vr = null;
  }

  /** "Salir de VR". The session's `onEnd` does the rest. */
  stop(): void {
    this.#vr?.stop();
  }

  /** Teardown: the live session, and one still on the consent prompt. */
  abandon(): void {
    this.#vr?.stop();
    // Still on the consent prompt: the session honours a stop requested
    // mid-start by ending itself the moment it exists, and `start` checks
    // `destroyed` after its await as well.
    this.#pendingVr?.stop();
    this.#pendingVr = null;
  }
}
