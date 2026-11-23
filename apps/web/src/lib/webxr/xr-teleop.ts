/**
 * WebXR teleop input: head yaw, both wrists, and finger closure.
 *
 * Supersedes the earlier head-yaw-only session. One `immersive-vr` session is
 * all a browser will give you, so head and hands have to come from the same
 * one — and the arm mapping needs the head pose anyway, since the operator's
 * shoulders are estimated from it (`bridge/teleop/retarget.py`).
 *
 * This module never talks to the network. It samples pose and hands it to a
 * callback; the throttling, the wire format and the dead-man policy live in
 * `#lib/teleop/stream.ts` and in the page, so the geometry here stays
 * independently testable and the transport can change without touching it.
 *
 * Coordinate system: WebXR is Y-up, right-handed, -Z forward, metres,
 * quaternions `[x, y, z, w]`. Yaw is the Y component of a Y-X-Z Euler
 * decomposition: `atan2(2*(w*y + x*z), 1 - 2*(y*y + z*z))`. Head pitch and
 * roll are deliberately dropped — the robot has no wired neck control, so
 * only "which way is the operator facing" feeds anything.
 *
 * Hand tracking is an optional feature. A Quest 3 with hand tracking switched
 * off, or a session started with controllers, simply reports no hands and the
 * head-yaw half keeps working — the page shows which of the two it got rather
 * than failing the session.
 *
 * Never live-tested against an actual headset.
 */

import type { FaceSeen } from "#lib/robot/face-seen.js";
import { CameraLayer, type CameraReason } from "./camera-layer";
import {
  MenuLayer,
  NO_CONTROLLER_STOP_HINT,
  type MenuItem,
  type MenuAlert,
  type MenuStatus,
  type Readiness,
  type VoiceLine,
} from "./menu-layer";
import { ScanLayer, type ScanRing } from "./scan-layer";
import { StatusLayer } from "./status-layer";

/**
 * What the status bar says about the controls.
 *
 * Folded, it is the only place the operator can learn that A opens the
 * gestures — so it says so. Open, the panel's own footer has the full list,
 * and repeating it here would put the same words twice in one glance; only
 * the stop chord stays, because a stop must be findable from anywhere.
 */
export function statusHint(
  menuExpanded: boolean,
  controllers: boolean,
): string {
  if (!controllers) return NO_CONTROLLER_STOP_HINT;
  return menuExpanded
    ? "2 grips PARAR"
    : "A gestos · B/Y hablar · 2 grips PARAR";
}
import {
  BUTTON_GRIP,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  BUTTON_TRIGGER,
  buttonPressed,
  controllerGamepad,
  measureGrip,
  normalizeAngle,
  quaternionYaw,
  walkAxisFrom,
} from "./xr-input";
import {
  drawPerEye,
  restoreOverlayBackground,
  stripOverlayBackground,
  type OverlayStyle,
} from "./xr-render";

// The pure helpers live in xr-input.ts and xr-render.ts; re-exported so the
// session stays the one import for callers and tests.
export {
  WALK_STICK_DEADZONE,
  angleBetween,
  buttonPressed,
  controllerGamepad,
  fingerCurl,
  normalizeAngle,
  quaternionYaw,
  subtract,
  walkAxisFrom,
  type Vec3,
} from "./xr-input";
export {
  drawPerEye,
  restoreOverlayBackground,
  stripOverlayBackground,
  type LayerFailures,
  type OverlayStyle,
  type XrLayer,
} from "./xr-render";

export type HandSample = {
  /** Wrist position in the reference space, metres. */
  position: [number, number, number];
  /** Wrist orientation, `[x, y, z, w]`. */
  orientation: [number, number, number, number];
  /** Finger closure, 0 fully open to 1 fully closed. */
  grip: number;
};

export type XrSample = {
  /** Signed yaw error from the calibrated forward reference, radians. */
  yawErrorRadians: number;
  /** Raw headset yaw in the reference space, radians. */
  yawRadians: number;
  /** Headset position in the reference space, metres. */
  headPosition: [number, number, number];
  left: HandSample | null;
  right: HandSample | null;
  /**
   * Walk intent from a controller thumbstick: 1 forward, -1 back, 0 neutral.
   *
   * A thumbstick rather than a drawn button, because it is a dead-man the
   * hardware enforces: let go and it springs to centre, so "stop" needs no
   * code path, no event, and nothing to be working. A button drawn into the
   * scene has to be found by eye, pressed by raycast, and RELEASED by another
   * raycast — and the release is the one that matters. Under passthrough the
   * page's own DOM buttons already work; this is for `immersive-vr`, where
   * `dom-overlay` does not composite and there is nothing to press.
   *
   * 0 when no controller is present, which is the same as "not asking to
   * walk". Hand-tracking-only sessions keep working exactly as before.
   */
  walkAxis: -1 | 0 | 1;
  /**
   * Controller buttons, OR-ed across both hands.
   *
   * Raw held-state, not edges. Edge detection belongs to whoever acts on it:
   * this fires at display rate and has no idea what a press should mean, and
   * a session that swallowed repeats would make a held button indistinguishable
   * from a released one for any consumer that wanted the difference.
   *
   * `trigger` is buttons[0] and `primary` is buttons[4] (A/X) in the
   * xr-standard mapping; `secondary` is buttons[5] (B/Y), push-to-talk.
   * `grips` counts the controllers whose grip (buttons[1]) is squeezed: two
   * at once is the in-headset PARAR. All false/0 when there is no controller.
   */
  buttons: XrButtons;
};

export type XrButtons = {
  trigger: boolean;
  primary: boolean;
  secondary: boolean;
  grips: number;
};

export type XrTeleopOptions = {
  /**
   * Whether to draw a camera layer at all. The URL and its liveness arrive
   * later through `setCameraStream` / `setCameraLive`, because both change
   * during a session: the vision server closes the stream whenever it goes
   * stale, and recovery means a NEW cache-busted URL. Passing a single URL up
   * front would freeze the headset on the last frame at the first stall.
   */
  camera?: boolean;
};

export type XrTeleopCallbacks = {
  /** Called on every valid pose sample — roughly display refresh rate. */
  onSample?: (sample: XrSample) => void;
  /** Called once the session has fully ended, for any reason. */
  onEnd?: () => void;
  /** The session's visibility changed (Meta menu, system overlay: "visible-blurred"). */
  onVisibility?: (state: XRVisibilityState) => void;
};

export type XrTeleopSupport = {
  /** Passthrough. Preferred — see `start()`. */
  immersiveAr: boolean;
  immersiveVr: boolean;
  /**
   * Whether the browser exposes the hand-tracking API at all. This is a
   * capability check, not a promise: the feature is still optional at session
   * request time and the user can have it switched off in system settings, in
   * which case the session starts fine and reports no hands.
   */
  handTracking: boolean;
};

export async function checkXrSupport(): Promise<XrTeleopSupport> {
  if (typeof navigator === "undefined" || !navigator.xr) {
    return { immersiveAr: false, immersiveVr: false, handTracking: false };
  }
  const supported = async (mode: XRSessionMode) => {
    try {
      return await navigator.xr!.isSessionSupported(mode);
    } catch {
      return false;
    }
  };
  const [immersiveAr, immersiveVr] = await Promise.all([
    supported("immersive-ar"),
    supported("immersive-vr"),
  ]);
  return {
    immersiveAr,
    immersiveVr,
    handTracking: typeof XRHand !== "undefined",
  };
}

export class XrTeleopSession {
  #session: XRSession | null = null;
  #referenceSpace: XRReferenceSpace | null = null;
  #forwardYaw = 0;
  #needsRecenter = true;
  #callbacks: XrTeleopCallbacks;
  #handsSeen = false;
  #mode: XRSessionMode | null = null;
  #options: XrTeleopOptions;
  #camera: CameraLayer | null = null;
  #menu: MenuLayer | null = null;
  #scan: ScanLayer | null = null;
  #status: StatusLayer | null = null;
  #controllers = true;
  #pendingStreamUrl = "";
  #pendingStreamCreds = true;
  #pendingFaces: [FaceSeen | null, number] = [null, 0];
  #pendingLive = true;
  #pendingReason: CameraReason = null;
  #cameraBroken = false;
  //: Held so the context can be explicitly released on teardown. A fresh
  //: canvas and context are created per VR entry and were only ever reclaimed
  //: by GC; Chrome force-loses the oldest context past roughly sixteen live
  //: ones, which during a debugging session of repeated enter/exit could kill
  //: an ACTIVE context and present as a black layer.
  #gl: WebGLRenderingContext | null = null;
  #starting = false;
  //: The overlay root's own inline background, so it can be put back exactly
  //: as it was when the session ends. See `#makeOverlayTransparent`.
  #overlayRoot: HTMLElement | null = null;
  #overlayStyleBackup: OverlayStyle | null = null;
  //: Set if stop() is called while start() is still in flight. The session
  //: does not exist yet, so there is nothing to end — this makes the newborn
  //: session end itself the moment it exists.
  #abandonOnStart = false;

  constructor(callbacks: XrTeleopCallbacks, options: XrTeleopOptions = {}) {
    this.#callbacks = callbacks;
    this.#options = options;
  }

  /** Whether the camera layer has decoded at least one frame, ever. */
  get cameraHasFrame(): boolean {
    return this.#camera?.hasFrame ?? false;
  }

  /** True if the camera layer failed and was dropped to protect head tracking. */
  get cameraBroken(): boolean {
    return this.#cameraBroken;
  }

  /**
   * Point the headset view at an MJPEG URL. Call again on every reconnect —
   * the source hands out a new cache-busted URL each time, and reusing the old
   * one is a retry that does nothing.
   */
  setCameraStream(url: string, withCredentials = true): void {
    this.#pendingStreamUrl = url;
    this.#pendingStreamCreds = withCredentials;
    this.#camera?.setStreamUrl(url, withCredentials);
  }

  /** Who the camera sees, drawn into the picture; see `#lib/robot/face-seen`. */
  setCameraFaces(seen: FaceSeen | null, receivedAt: number): void {
    this.#pendingFaces = [seen, receivedAt];
    this.#camera?.setFaces(seen, receivedAt);
  }

  /** Tell the view whether the feed is still live. Stale is dimmed, not hidden. */
  setCameraLive(live: boolean): void {
    this.#pendingLive = live;
    this.#camera?.setLive(live);
  }

  /**
   * Why there is no picture, shown in the headset when there is none.
   *
   * The page has always known this — `camState` and `camDetail` are on screen
   * next to the camera panel — and it never reached the one place an operator
   * with a headset on is looking. Same shape of gap as the readiness banner
   * and the lidar ring before it.
   */
  setCameraReason(reason: CameraReason): void {
    this.#pendingReason = reason;
    this.#camera?.setReason(reason);
  }

  get active(): boolean {
    return this.#session !== null;
  }

  /**
   * Preset menu, drawn in the headset. Everything here is a no-op before the
   * session starts and after it ends, so the page can call them whenever its
   * own state changes without tracking session lifetime.
   */
  setMenuItems(items: readonly MenuItem[]): void {
    this.#menu?.setItems(items);
  }

  setMenuVisible(visible: boolean): void {
    this.#menu?.setVisible(visible);
  }

  // Readiness, the alert and the outcome keep their `setMenu*` names — callers
  // have always pushed them "to the headset menu" — but they are drawn in the
  // status bar now, which stays up while the panel is folded.

  /** The result of the last thing fired, in the status bar. */
  setMenuStatus(status: MenuStatus): void {
    this.#status?.setStatus(status);
  }

  setMenuBusy(name: string | null): void {
    this.#menu?.setBusy(name);
  }

  /** Why the robot can or cannot act — drawn where the operator is looking. */
  setMenuReadiness(readiness: Readiness | null): void {
    this.#status?.setReadiness(readiness);
  }

  /** A latch currently stopping motion, and the gesture that clears it. */
  setMenuAlert(alert: MenuAlert): void {
    this.#status?.setAlert(alert);
  }

  /** "Hablar con el robot": the conversation lines, or null for gestures. */
  setMenuVoice(lines: readonly VoiceLine[] | null): void {
    this.#menu?.setVoice(lines);
  }

  /**
   * The lidar ring, or null when none is arriving.
   *
   * `reason` is what the panel says INSTEAD of drawing an empty dial. "No
   * scan" has several causes an operator in a headset cannot tell apart, and
   * a clear-looking circle is the worst possible way to report any of them.
   */
  setScanRing(ring: ScanRing | null, reason?: string | null): void {
    this.#scan?.setRing(ring, reason);
  }

  /** Whether the radar is drawn at all — off in passthrough, where the
   * operator can simply look around the room themselves. */
  setScanVisible(visible: boolean): void {
    this.#scan?.setVisible(visible);
  }

  /**
   * A: open the folded panel, or — once open — move the highlight to the
   * next VERIFIED item.
   *
   * The first press only opens. Moving the highlight as well would mean the
   * operator never sees where it was, and the item the trigger fires is
   * chosen by looking, not by counting presses.
   */
  advanceMenu(): void {
    const menu = this.#menu;
    if (!menu) return;
    if (menu.expanded) menu.advance();
    else menu.open();
  }

  /** Fold the panel away: after a gesture fires, or when left untouched. */
  closeMenu(): void {
    this.#menu?.close();
  }

  /** Whether the gestures / voice panel is on screen right now. */
  get menuExpanded(): boolean {
    return this.#menu?.expanded ?? false;
  }

  /** What firing the trigger would run, or null. Never an unverified skill. */
  get menuSelection(): MenuItem | null {
    const item = this.#menu?.selectedItem ?? null;
    // Belt and braces: `advance()` only ever lands on verified entries, but
    // the caller is about to COMMAND A ROBOT with whatever this returns, and
    // an empty or all-unverified catalogue is exactly when the highlight has
    // nowhere legitimate to sit.
    return item?.verified ? item : null;
  }

  /** Whether any hand has been tracked since the session began. */
  get handsSeen(): boolean {
    return this.#handsSeen;
  }

  /**
   * Which mode the session actually got. `immersive-ar` means passthrough and
   * a composited DOM overlay; `immersive-vr` means the overlay may not be
   * visible at all, which the page should say out loud rather than leave the
   * wearer staring at black.
   */
  get mode(): XRSessionMode | null {
    return this.#mode;
  }

  /** Re-capture "forward" as the headset's current yaw, on the next frame. */
  recenter(): void {
    this.#needsRecenter = true;
  }

  /**
   * Request an immersive-vr session, with `overlayRoot` shown as a
   * `dom-overlay` — the Quest Browser keeps that element visible and
   * interactive in front of the (otherwise blank) VR view, which is what
   * lets the rest of `/vr-control`'s buttons stay usable while worn.
   */
  async start(overlayRoot: HTMLElement): Promise<void> {
    // `#session` is not assigned until several awaits later — after a support
    // probe, `requestSession` (which blocks on user consent, for seconds), a
    // GL compatibility call and a reference-space request. Guarding on it
    // alone lets two taps of "Entrar en VR" both pass: if the second session
    // also succeeds the page keeps only one handle, and the orphan's `end`
    // event later clears `vrActive` for a session that is still live.
    //
    // Worse, `stop()` during that window is a no-op, because there is nothing
    // to stop yet — so a component destroyed mid-start leaves the wearer in an
    // immersive session that nothing on the page owns. That is exactly the
    // "stranded in a blank view" case the teardown below defends against.
    if (this.#session || this.#starting) return;
    this.#starting = true;
    try {
      await this.#start(overlayRoot);
    } finally {
      this.#starting = false;
    }
  }

  async #start(overlayRoot: HTMLElement): Promise<void> {
    if (typeof navigator === "undefined" || !navigator.xr) {
      throw new Error("WebXR no está disponible en este navegador.");
    }

    // PASSTHROUGH FIRST, and this is a correction rather than a preference.
    //
    // The first headset session drew a black void. The cause: `dom-overlay` is
    // an AR feature. Under `immersive-vr` the Quest browser does not composite
    // the DOM at all, so the operator got our cleared (transparent, therefore
    // black) WebGL layer and nothing else — no controls, no PARAR, no way to
    // tell whether anything was working.
    //
    // `immersive-ar` fixes it twice over. The DOM overlay is composited, so the
    // controls are actually there; and passthrough means the operator sees the
    // room — including the robot they are driving. For teleoperation from the
    // same room that is not a consolation prize, it is the better view: you
    // watch the machine, not a rendering of it.
    //
    // VR stays as a fallback for a headset without passthrough. There the
    // overlay may still not composite, which is why `start()` reports the mode
    // it got and the page warns.
    //
    // 2026-08-24: THAT REASONING IS NOW OBSOLETE, so the preference is
    // reversed. The controls are no longer DOM — `menu-layer.ts` paints the
    // gesture list, the readiness banner and the latch alerts into the XR
    // layer itself, in WebGL. `immersive-vr` therefore no longer means "no
    // controls"; it means the compositor shows nothing except what we draw.
    //
    // Which is what the operator asked for, twice, in the words "modo
    // environment" and "everything that is not the camera is black". Under
    // passthrough the black clear this module does in VR is deliberately not
    // applied — painting the room black to show a camera OF that room is the
    // wrong trade — so an AR session can never be a surround, however the
    // camera quad is drawn. The mode was the reason the surround never
    // appeared, not the clear colour.
    //
    // What that costs: in AR the operator could see the actual robot beside
    // the rendering of it, which for same-room teleop is genuinely useful. AR
    // stays as the fallback, and `mode` is still reported so the page can say
    // which one it got.
    const requested: XRSessionMode[] = (await checkXrSupport()).immersiveAr
      ? ["immersive-vr", "immersive-ar"]
      : ["immersive-vr"];

    let session: XRSession | null = null;
    let lastError: unknown = null;
    for (const mode of requested) {
      try {
        session = await navigator.xr.requestSession(mode, {
          // All optional: a headset without hand tracking still gives a usable
          // head-yaw session, and requiring the feature would turn "no hands"
          // into "no session".
          optionalFeatures: ["dom-overlay", "local-floor", "hand-tracking"],
          domOverlay: { root: overlayRoot },
        });
        this.#mode = mode;
        break;
      } catch (err) {
        lastError = err;
      }
    }
    if (!session)
      throw lastError ?? new Error("No se pudo iniciar una sesión XR.");

    // Everything past this point must either fully succeed or end the
    // session again. A partially-initialised session is worse than none:
    // the headset is already in an immersive view, but a caller that saw
    // `start()` throw never stores the handle, so its own "exit" control
    // becomes a no-op and the wearer is stranded in a blank view with no
    // way back from the page.
    try {
      // A WebXR session needs a compatible WebGL layer bound even when
      // nothing meaningful is drawn — the spec expects a frame submitted
      // every tick, and a cleared/transparent framebuffer is a valid,
      // minimal one (the dom-overlay draws the actual UI on top).
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl", { xrCompatible: true });
      this.#gl = gl;
      if (!gl)
        throw new Error(
          "No se pudo crear un contexto WebGL para la sesión XR.",
        );
      await gl.makeXRCompatible();
      const layer = new XRWebGLLayer(session, gl);
      await session.updateRenderState({ baseLayer: layer });

      // The panels are NOT conditional on a camera. A session started with no
      // stream configured used to get an entirely blank headset — no picture,
      // and also no readiness banner explaining that, and no radar. The camera
      // is the thing that might be absent; the reasons it is absent are not.
      this.#menu = new MenuLayer(gl);
      this.#scan = new ScanLayer(gl);
      this.#status = new StatusLayer(gl);
      if (this.#options.camera) {
        this.#camera = new CameraLayer(gl);
        // The URL arrives via setCameraStream(), possibly before this point —
        // apply whatever the page last told us so a reconnect that happened
        // during startup is not lost.
        if (this.#pendingStreamUrl)
          this.#camera.setStreamUrl(
            this.#pendingStreamUrl,
            this.#pendingStreamCreds,
          );
        this.#camera.setFaces(...this.#pendingFaces);
        this.#camera.setLive(this.#pendingLive);
        // Applied before the first frame, so a session entered while the feed
        // is already down opens on the reason rather than on a blank field
        // that only gets its explanation at the next poll.
        this.#camera.setReason(this.#pendingReason);
      }

      let referenceSpace: XRReferenceSpace;
      try {
        referenceSpace = await session.requestReferenceSpace("local-floor");
      } catch {
        referenceSpace = await session.requestReferenceSpace("local");
      }

      if (this.#abandonOnStart) {
        // stop() arrived while we were still setting up. End it now rather
        // than hand back a session nobody is holding.
        this.#abandonOnStart = false;
        this.#mode = null;
        try {
          await session.end();
        } catch {
          // already ending
        }
        // The "end" listener that normally releases the context is registered
        // BELOW this point, so ending the session here fires nothing: the GL
        // context and the three layers built on it were leaked on every
        // abandoned start. Chrome force-loses the oldest context past roughly
        // sixteen live ones, and the one it takes may be an ACTIVE session's —
        // presenting as a black layer in a headset somebody is wearing.
        this.#releaseGraphics();
        return;
      }
      this.#makeOverlayTransparent(overlayRoot);
      this.#session = session;
      this.#referenceSpace = referenceSpace;
      this.#needsRecenter = true;
      this.#handsSeen = false;

      session.addEventListener("end", () => {
        this.#session = null;
        this.#referenceSpace = null;
        this.#mode = null;
        // Put the page back the way it looked before, or the console is left
        // with a transparent body over the browser's own background.
        this.#restoreOverlay();
        this.#releaseGraphics();
        // A GL failure is per-session, not permanent: a re-entered session
        // gets a fresh context and deserves a fresh attempt at a picture.
        this.#cameraBroken = false;
        this.#callbacks.onEnd?.();
      });
      session.addEventListener("visibilitychange", () =>
        this.#callbacks.onVisibility?.(session.visibilityState),
      );

      const onFrame: XRFrameRequestCallback = (_time, frame) => {
        const active = this.#session;
        const space = this.#referenceSpace;
        if (!active || !space) return;
        active.requestAnimationFrame(onFrame);

        gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
        // OPAQUE black in VR, transparent under passthrough.
        //
        // The camera quad no longer fills the eye — it is fitted to the frame's
        // aspect and inset — so whatever the clear leaves behind is now VISIBLE
        // around it. Transparent in `immersive-vr` means the compositor shows
        // its own background there, which reads as the picture floating in
        // something rather than being the world. Black surround makes the frame
        // the only lit thing in the headset, which is what "environment mode"
        // means to the operator who asked for it.
        //
        // AR keeps the transparent clear: under passthrough the operator is in
        // the same room as the robot, and painting their room black to show
        // them a camera of it is the wrong trade.
        const opaqueSurround = this.#mode !== "immersive-ar";
        gl.clearColor(0, 0, 0, opaqueSurround ? 1 : 0);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

        const pose = frame.getViewerPose(space);
        if (!pose) return; // tracking lost this frame — the caller's own
        // staleness timeout (not this module) decides when that should stop
        // the robot; a single missed frame at 72-120Hz isn't it.

        // The open panel dims the picture behind it, and the bar's hint follows
        // whether the panel is open. Both setters are no-ops when unchanged.
        const expanded = this.#menu?.expanded ?? false;
        this.#camera?.setDimmed(expanded);
        this.#status?.setHint(statusHint(expanded, this.#controllers));

        if (this.#camera || this.#menu || this.#scan || this.#status) {
          const failed = drawPerEye(
            gl,
            layer,
            pose,
            this.#camera,
            this.#mode !== "immersive-ar",
            this.#menu,
            this.#scan,
            this.#status,
          );
          // Shader compile or link failure throws out of draw(), and this
          // callback is what samples head pose. Letting it escape kills
          // steering silently for the rest of the session while the robot
          // stays safe but unresponsive. Drop the layer, keep the pose — and
          // drop ONLY the layer that threw.
          if (failed.camera) {
            this.#cameraBroken = true;
            this.#camera = null;
            console.error(
              "[xr] camera layer disabled after draw failure",
              failed.camera,
            );
          }
          if (failed.menu) {
            this.#menu = null;
            console.error(
              "[xr] menu layer disabled after draw failure",
              failed.menu,
            );
          }
          if (failed.scan) {
            this.#scan = null;
            console.error(
              "[xr] scan layer disabled after draw failure",
              failed.scan,
            );
          }
          if (failed.status) {
            this.#status = null;
            console.error(
              "[xr] status layer disabled after draw failure",
              failed.status,
            );
          }
        }

        // Under a system overlay the inputs freeze (grips read 0, hands untracked):
        // nothing sampled then may steer the robot, so draw but do not sample.
        if (active.visibilityState !== "visible") return;

        const yaw = quaternionYaw(pose.transform.orientation);
        if (this.#needsRecenter) {
          this.#forwardYaw = yaw;
          this.#needsRecenter = false;
        }

        const p = pose.transform.position;
        const hands = this.#sampleHands(frame, active, space);
        this.#callbacks.onSample?.({
          yawRadians: yaw,
          yawErrorRadians: normalizeAngle(yaw - this.#forwardYaw),
          headPosition: [p.x, p.y, p.z],
          ...hands,
          walkAxis: this.#sampleWalkAxis(active),
          buttons: this.#sampleButtons(active),
        });
      };
      session.requestAnimationFrame(onFrame);
    } catch (err) {
      this.#session = null;
      this.#referenceSpace = null;
      this.#mode = null;
      try {
        await session.end();
      } catch {
        // Already ending/ended — the throw below is the useful signal.
      }
      // Same reason as the abandon branch above: a failure between creating the
      // context and registering the "end" listener — `makeXRCompatible`,
      // `updateRenderState`, or both `requestReferenceSpace` calls — left the
      // context and any layers already built alive with nothing to release
      // them. Retrying after a transient failure would then accumulate them.
      this.#releaseGraphics();
      throw err;
    }
  }

  /**
   * Drop the GL context and everything built on it.
   *
   * Extracted from the "end" listener because it is needed on two paths that
   * never reach it: a start abandoned mid-flight, and a start that throws
   * before the listener is registered. Idempotent — every field is
   * null-guarded and then nulled — so the listener firing after one of those
   * is harmless.
   */
  #releaseGraphics(): void {
    // Release the context rather than waiting for GC — see `#gl`.
    const lose = this.#gl?.getExtension("WEBGL_lose_context");
    lose?.loseContext();
    this.#gl = null;
    // Disposing cuts the <img> src, which is what actually closes the MJPEG
    // request — a session that is going away must not keep pulling frames.
    this.#camera?.dispose();
    this.#camera = null;
    this.#menu?.dispose();
    this.#menu = null;
    this.#scan?.dispose();
    this.#scan = null;
    this.#status?.dispose();
    this.#status = null;
  }

  #sampleHands(
    frame: XRFrame,
    session: XRSession,
    space: XRReferenceSpace,
  ): { left: HandSample | null; right: HandSample | null } {
    let left: HandSample | null = null;
    let right: HandSample | null = null;

    for (const source of session.inputSources) {
      const hand = source.hand;
      if (!hand) continue;
      const wrist = hand.get("wrist");
      if (!wrist) continue;
      const wristPose = frame.getJointPose?.(wrist, space);
      // A hand that has left the tracking volume yields no pose. Leaving it
      // null is the whole signal: the bridge holds that arm's last target
      // rather than dropping it.
      if (!wristPose) continue;

      const { position, orientation } = wristPose.transform;
      const sample: HandSample = {
        position: [position.x, position.y, position.z],
        orientation: [
          orientation.x,
          orientation.y,
          orientation.z,
          orientation.w,
        ],
        grip: measureGrip(frame, hand, space),
      };
      this.#handsSeen = true;
      if (source.handedness === "left") left = sample;
      else if (source.handedness === "right") right = sample;
    }
    return { left, right };
  }

  /**
   * Walk intent from whichever controller is reporting one.
   *
   * Either hand, and the LARGEST magnitude wins rather than the first found:
   * `inputSources` has no guaranteed order, so first-wins would make which
   * controller works depend on enumeration order — fine in testing, confusing
   * in a headset, and impossible to diagnose from inside one.
   *
   * Contradictory sticks (one forward, one back) cancel to 0. That is the
   * safe reading of "two hands disagree", and it is what a startled operator
   * grabbing both controllers produces.
   */
  #sampleWalkAxis(session: XRSession): -1 | 0 | 1 {
    let best: -1 | 0 | 1 = 0;
    let bestMag = 0;
    let sawForward = false;
    let sawBack = false;
    for (const source of session.inputSources) {
      const axes = controllerGamepad(source)?.axes;
      const intent = walkAxisFrom(axes);
      if (intent === 0) continue;
      if (intent > 0) sawForward = true;
      else sawBack = true;
      const y = axes && axes.length >= 4 ? axes[3] : axes?.[1];
      const mag = Math.abs(typeof y === "number" ? y : 0);
      if (mag > bestMag) {
        bestMag = mag;
        best = intent;
      }
    }
    if (sawForward && sawBack) return 0;
    return best;
  }

  /** Buttons from any controller. Either hand works — see `#sampleWalkAxis`. */
  #sampleButtons(session: XRSession): XrButtons {
    let trigger = false;
    let primary = false;
    let secondary = false;
    let grips = 0;
    let controllers = 0;
    for (const source of session.inputSources) {
      const gp = controllerGamepad(source);
      if (!gp) continue;
      controllers += 1;
      if (buttonPressed(gp, BUTTON_TRIGGER)) trigger = true;
      if (buttonPressed(gp, BUTTON_PRIMARY)) primary = true;
      if (buttonPressed(gp, BUTTON_SECONDARY)) secondary = true;
      if (buttonPressed(gp, BUTTON_GRIP)) grips += 1;
    }
    // Hand tracking: no gamepad, so no grip chord; the panel and bar say so.
    this.#controllers = controllers > 0;
    this.#menu?.setControllers(this.#controllers);
    return { trigger, primary, secondary, grips };
  }

  #makeOverlayTransparent(root: HTMLElement): void {
    this.#overlayRoot = root;
    this.#overlayStyleBackup = stripOverlayBackground(root);
  }

  #restoreOverlay(): void {
    if (this.#overlayRoot && this.#overlayStyleBackup) {
      restoreOverlayBackground(this.#overlayRoot, this.#overlayStyleBackup);
    }
    this.#overlayRoot = null;
    this.#overlayStyleBackup = null;
  }

  /** End the session. `onEnd` fires once the browser confirms teardown. */
  stop(): void {
    if (!this.#session && this.#starting) {
      // Mid-start: there is no session to end yet, so record the intent and
      // let `#start` honour it as soon as one exists.
      this.#abandonOnStart = true;
      return;
    }
    void this.#session?.end();
  }
}
