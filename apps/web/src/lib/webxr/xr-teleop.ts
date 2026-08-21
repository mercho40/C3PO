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
 * `$lib/teleop/stream.ts` and in the page, so the geometry here stays
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

import { CameraLayer } from "./camera-layer";
import { MenuLayer, type MenuItem, type MenuStatus } from "./menu-layer";

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
   * xr-standard mapping. Both false when there is no controller.
   */
  buttons: { trigger: boolean; primary: boolean };
};

/** Index in the `xr-standard` gamepad mapping. */
const BUTTON_TRIGGER = 0;
const BUTTON_PRIMARY = 4; // A on the right controller, X on the left

/**
 * Whether a gamepad button is pressed, tolerating every shape a runtime
 * might hand back.
 *
 * `pressed` is the field to read — `value` is analogue and a trigger resting
 * at 0.02 is not a press. A missing button reads as not pressed, because the
 * alternative in a headset is a preset firing because a runtime reported one
 * fewer button than expected.
 */
export function buttonPressed(
  gamepad: { buttons?: readonly { pressed?: boolean }[] } | null | undefined,
  index: number,
): boolean {
  return gamepad?.buttons?.[index]?.pressed === true;
}

/**
 * Thumbstick Y to a walk intent. Pure, so the thresholds are testable.
 *
 * WebXR's standard mapping puts the primary thumbstick on axes[2]/axes[3],
 * with axes[0]/axes[1] the touchpad — but not every runtime populates four
 * axes, so this falls back to [0]/[1] when the pair is absent rather than
 * reading undefined and deciding the operator is not asking for anything.
 *
 * Y is NEGATIVE when a stick is pushed away from the operator, per the
 * gamepad spec. Forward is therefore -y, and getting that backwards would
 * mean the robot walks toward someone who pulled back to stop — so it is
 * stated here once and tested, rather than inferred at the call site.
 *
 * The deadzone is deliberately large. This is not a game: a resting thumb, a
 * stick that does not quite centre, or a knock while reaching for something
 * must all read as "not walking", and the cost of a high threshold is only
 * that the operator pushes further.
 */
export const WALK_STICK_DEADZONE = 0.6;

export function walkAxisFrom(
  axes: readonly number[] | null | undefined,
): -1 | 0 | 1 {
  if (!axes) return 0;
  const y = axes.length >= 4 ? axes[3] : axes[1];
  if (typeof y !== "number" || !Number.isFinite(y)) return 0;
  if (y <= -WALK_STICK_DEADZONE) return 1; // pushed away = forward
  if (y >= WALK_STICK_DEADZONE) return -1; // pulled back = backward
  return 0;
}

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

/**
 * The operator's heading: which way they are facing, ignoring pitch and roll.
 *
 * Not an Euler extraction. The obvious
 * `atan2(2(wy + xz), 1 - 2(y² + z²))` is a Y-X-Z decomposition, and its "yaw"
 * shrinks as pitch grows — measured, for a 40 degree turn:
 *
 *     look down  0 deg -> reads 40.0     30 deg -> reads 36.0
 *               15 deg -> reads 39.0     60 deg -> reads 22.8
 *
 * So an operator glancing down at the robot — which is the natural thing to do
 * while driving it — quietly loses up to half their steering authority. The
 * sign stays right, so it never turns the wrong way; it just stops turning
 * properly at exactly the moment someone is watching their feet.
 *
 * Projecting the gaze direction onto the horizontal plane answers the question
 * we actually mean, and is pitch-independent by construction. For a pure yaw
 * rotation it agrees with the Euler form exactly, which matters: that is the
 * convention verified on hardware (positive = left, three measured turns).
 *
 * Straight up or straight down leaves no horizontal component to project, so
 * that case falls back to the head's own up-vector — the standard trick, and
 * the difference between a defined heading and NaN in a motor command.
 */
export function quaternionYaw(o: {
  x: number;
  y: number;
  z: number;
  w: number;
}): number {
  const { x, y, z, w } = o;
  // Forward is -Z in WebXR: the third column of the rotation matrix, negated.
  const fx = -2 * (x * z + w * y);
  const fz = -(1 - 2 * (x * x + y * y));

  if (Math.hypot(fx, fz) < 1e-4) {
    // Looking straight up or down. Gaze says nothing about heading, so use
    // where the top of the head points instead.
    const ux = 2 * (x * y - w * z);
    const uz = 2 * (y * z + w * x);
    const fy = -2 * (y * z - w * x);
    // Looking down flips the up-vector's horizontal sense relative to gaze.
    const sign = fy > 0 ? -1 : 1;
    return Math.atan2(-sign * ux, -sign * uz);
  }
  return Math.atan2(-fx, -fz);
}

/** Wrap an angle to (-pi, pi]. Exported for tests. */
export function normalizeAngle(a: number): number {
  let x = a % (2 * Math.PI);
  if (x > Math.PI) x -= 2 * Math.PI;
  if (x <= -Math.PI) x += 2 * Math.PI;
  return x;
}

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

// --- finger closure ---------------------------------------------------------
//
// Measured as *curl* — the angle between each finger's proximal and distal
// segments — rather than as fingertip-to-palm distance. Curl is scale-free, so
// a small hand and a large hand both read 0 when open and 1 when closed with
// no per-operator calibration step. Distance-based measures need one, and an
// uncalibrated distance measure reads a small hand as permanently clenched.
const CURL_FINGERS = [
  [
    "index-finger-phalanx-proximal",
    "index-finger-phalanx-intermediate",
    "index-finger-tip",
  ],
  [
    "middle-finger-phalanx-proximal",
    "middle-finger-phalanx-intermediate",
    "middle-finger-tip",
  ],
  [
    "ring-finger-phalanx-proximal",
    "ring-finger-phalanx-intermediate",
    "ring-finger-tip",
  ],
  [
    "pinky-finger-phalanx-proximal",
    "pinky-finger-phalanx-intermediate",
    "pinky-finger-tip",
  ],
] as const satisfies readonly (readonly [
  XRHandJoint,
  XRHandJoint,
  XRHandJoint,
])[];

/** Curl angle at which a finger reads as fully closed. ~2 rad is a firm fist. */
const FULL_CURL_RAD = 2.0;

export type Vec3 = [number, number, number];

export function subtract(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

/** Angle between two vectors, radians. Exported for tests. */
export function angleBetween(a: Vec3, b: Vec3): number {
  const na = Math.hypot(a[0], a[1], a[2]);
  const nb = Math.hypot(b[0], b[1], b[2]);
  if (na < 1e-6 || nb < 1e-6) return 0;
  const cos = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (na * nb);
  return Math.acos(Math.min(1, Math.max(-1, cos)));
}

function jointPosition(
  frame: XRFrame,
  hand: XRHand,
  space: XRSpace,
  joint: XRHandJoint,
): Vec3 | null {
  const jointSpace = hand.get(joint);
  if (!jointSpace) return null;
  const pose = frame.getJointPose?.(jointSpace, space);
  if (!pose) return null;
  const p = pose.transform.position;
  return [p.x, p.y, p.z];
}

/**
 * Reduce three joint positions to a curl fraction, 0 open to 1 closed.
 *
 * Exported so the scale-free claim can actually be tested: the same physical
 * gesture on a small hand and a large one must give the same number, which is
 * the whole reason curl was chosen over fingertip-to-palm distance.
 */
export function fingerCurl(
  proximal: Vec3,
  intermediate: Vec3,
  tip: Vec3,
): number {
  const angle = angleBetween(
    subtract(intermediate, proximal),
    subtract(tip, intermediate),
  );
  return Math.min(1, Math.max(0, angle / FULL_CURL_RAD));
}

function measureGrip(frame: XRFrame, hand: XRHand, space: XRSpace): number {
  let total = 0;
  let counted = 0;
  for (const [proximal, intermediate, tip] of CURL_FINGERS) {
    const a = jointPosition(frame, hand, space, proximal);
    const b = jointPosition(frame, hand, space, intermediate);
    const c = jointPosition(frame, hand, space, tip);
    if (!a || !b || !c) continue;
    total += angleBetween(subtract(b, a), subtract(c, b));
    counted += 1;
  }
  // No joints resolved this frame — report open rather than guessing. An
  // unknown grip must not read as "closed", which on a fitted hand is a
  // command to clench around whatever is in front of it.
  if (counted === 0) return 0;
  return Math.min(1, Math.max(0, total / counted / FULL_CURL_RAD));
}

/**
 * Draw the camera once per eye, into each eye's own viewport.
 *
 * ONE VIEWPORT PER EYE, AND THIS IS WHY THE CAMERA WAS INVISIBLE.
 *
 * The canvas backing the XR context is created detached and never sized, so
 * its drawing buffer is the HTML default 300x150 — and GL seeds the viewport
 * from the drawing buffer at context creation. Nothing in the app ever called
 * `gl.viewport`. The XR layer's framebuffer is nothing like that shape: on a
 * Quest 3 it is roughly 2064x2208 PER EYE, both eyes side by side in one
 * buffer.
 *
 * `gl.clear` is scissor-bounded, so the transparent clear covered everything
 * and the view looked correct-but-empty. `drawArrays` is VIEWPORT-bounded, so
 * a full clip-space quad landed in a 300x150 patch in the corner of one eye.
 * In passthrough that reads as "the room, with a smear"; in VR as "black, with
 * a postage stamp". Both were reported as "no camera", and both sent us
 * looking at the perception stack and the SSH tunnel.
 *
 * The per-view loop WebXR requires was also absent entirely, and the draw ran
 * BEFORE `getViewerPose` — so the views did not exist yet and it could not
 * have been correct even with the viewport set. Hence the argument order here:
 * a pose is required to get one at all.
 *
 * Returns the error if drawing threw, so the caller can drop the camera
 * without losing the head pose it is in the middle of sampling.
 */
export function drawPerEye(
  gl: WebGLRenderingContext,
  layer: XRWebGLLayer,
  pose: XRViewerPose,
  camera: { draw(opaque: boolean, vpWidth?: number, vpHeight?: number): void },
  opaque: boolean,
  /**
   * Drawn after the camera, into the same viewport, so it sits over the
   * picture rather than under it. Optional: sessions without a menu pass
   * nothing and this is a no-op.
   */
  menu?: { draw(vpWidth?: number, vpHeight?: number): void } | null,
): unknown | null {
  for (const view of pose.views) {
    const vp = layer.getViewport(view);
    if (!vp) continue;
    gl.viewport(vp.x, vp.y, vp.width, vp.height);
    try {
      // The camera IS the view in VR; in passthrough it is a heads-up layer
      // over the room, so it does not paint over what the operator can see.
      //
      // The viewport goes to draw() as well as to gl.viewport(): the quad is
      // aspect-fitted to it. Setting the viewport alone stretches the frame to
      // the eye's shape, which is how the first real session got a picture
      // that was live, correct, and visibly deformed.
      camera.draw(opaque, vp.width, vp.height);
      menu?.draw(vp.width, vp.height);
    } catch (err) {
      return err ?? new Error("camera draw failed");
    }
  }
  return null;
}

/** What an overlay root looked like before the session took it over. */
export type OverlayStyle = { background: string; display: string };

/**
 * Strip the overlay root's opaque background for the life of the session.
 *
 * THE DOM OVERLAY COMPOSITES ON TOP OF THE WEBGL LAYER. Everything the
 * renderer draws — the camera quad, and under passthrough the room itself — is
 * *behind* this element. The page styles it `bg-background`, which resolves to
 * `#06090f`: fully opaque, edge to edge, because the UA promotes the overlay
 * root to `position: fixed; inset: 0`.
 *
 * So the camera was being drawn correctly and then painted over. That is a
 * second, independent cause of the same "no camera" symptom the viewport bug
 * produced — and it is worse in `immersive-ar`, the mode this now prefers,
 * because AR is exactly where the overlay composites. Fixing the viewport
 * alone would have shown the operator the same black field and sent them
 * looking at the renderer a third time.
 *
 * Done in script rather than CSS because it has to be tied to the session
 * lifetime and undone afterwards — a console left with a transparent body
 * shows the browser's background, not the app's. The panels inside keep their
 * own backgrounds, so the controls stay readable against the room, which is
 * what a heads-up overlay should look like.
 *
 * Returns the previous inline values so they can be put back exactly.
 */
export function stripOverlayBackground(root: HTMLElement): OverlayStyle {
  const previous: OverlayStyle = {
    background: root.style.background,
    display: root.style.display,
  };
  root.style.background = "transparent";
  // The UA also forces `display: block` on `:xr-overlay`, which collapses the
  // root's flex column. Asking for flex back is harmless where the UA wins.
  root.style.display = "flex";
  return previous;
}

/** Undo `stripOverlayBackground`, restoring exactly what was there before. */
export function restoreOverlayBackground(
  root: HTMLElement,
  previous: OverlayStyle,
): void {
  root.style.background = previous.background;
  root.style.display = previous.display;
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
  #pendingStreamUrl = "";
  #pendingLive = true;
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
  setCameraStream(url: string): void {
    this.#pendingStreamUrl = url;
    this.#camera?.setStreamUrl(url);
  }

  /** Tell the view whether the feed is still live. Stale is dimmed, not hidden. */
  setCameraLive(live: boolean): void {
    this.#pendingLive = live;
    this.#camera?.setLive(live);
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

  setMenuStatus(status: MenuStatus): void {
    this.#menu?.setStatus(status);
  }

  setMenuBusy(name: string | null): void {
    this.#menu?.setBusy(name);
  }

  /** Move the highlight to the next VERIFIED item. */
  advanceMenu(): void {
    this.#menu?.advance();
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
    const requested: XRSessionMode[] = (await checkXrSupport()).immersiveAr
      ? ["immersive-ar", "immersive-vr"]
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

      if (this.#options.camera) {
        this.#camera = new CameraLayer(gl);
        this.#menu = new MenuLayer(gl);
        // The URL arrives via setCameraStream(), possibly before this point —
        // apply whatever the page last told us so a reconnect that happened
        // during startup is not lost.
        if (this.#pendingStreamUrl)
          this.#camera.setStreamUrl(this.#pendingStreamUrl);
        this.#camera.setLive(this.#pendingLive);
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
        try {
          await session.end();
        } catch {
          // already ending
        }
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
        // Release the context rather than waiting for GC — see `#gl`.
        const lose = this.#gl?.getExtension("WEBGL_lose_context");
        lose?.loseContext();
        this.#gl = null;
        // A GL failure is per-session, not permanent: a re-entered session
        // gets a fresh context and deserves a fresh attempt at a picture.
        this.#cameraBroken = false;
        // Disposing cuts the <img> src, which is what actually closes the
        // MJPEG request — an ended session must not keep pulling frames.
        this.#camera?.dispose();
        this.#camera = null;
        this.#menu?.dispose();
        this.#menu = null;
        this.#callbacks.onEnd?.();
      });

      const onFrame: XRFrameRequestCallback = (_time, frame) => {
        const active = this.#session;
        const space = this.#referenceSpace;
        if (!active || !space) return;
        active.requestAnimationFrame(onFrame);

        gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

        const pose = frame.getViewerPose(space);
        if (!pose) return; // tracking lost this frame — the caller's own
        // staleness timeout (not this module) decides when that should stop
        // the robot; a single missed frame at 72-120Hz isn't it.

        if (this.#camera) {
          const failure = drawPerEye(
            gl,
            layer,
            pose,
            this.#camera,
            this.#mode !== "immersive-ar",
            this.#menu,
          );
          if (failure) {
            // Shader compile or link failure throws out of draw(), and this
            // callback is what samples head pose. Letting it escape kills
            // steering silently for the rest of the session while the robot
            // stays safe but unresponsive. Drop the picture, keep the pose.
            this.#cameraBroken = true;
            this.#camera = null;
            console.error(
              "[xr] camera layer disabled after draw failure",
              failure,
            );
          }
        }

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
      try {
        await session.end();
      } catch {
        // Already ending/ended — the throw below is the useful signal.
      }
      throw err;
    }
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
      const axes = source.gamepad?.axes;
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
  #sampleButtons(session: XRSession): { trigger: boolean; primary: boolean } {
    let trigger = false;
    let primary = false;
    for (const source of session.inputSources) {
      const gp = source.gamepad;
      if (!gp) continue;
      if (buttonPressed(gp, BUTTON_TRIGGER)) trigger = true;
      if (buttonPressed(gp, BUTTON_PRIMARY)) primary = true;
    }
    return { trigger, primary };
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
