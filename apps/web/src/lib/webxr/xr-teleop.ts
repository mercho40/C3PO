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
};

export type XrTeleopOptions = {
  /**
   * MJPEG endpoint to show as the view, e.g.
   * `http://localhost:8081/stream.mjpg`. Omitted or unreachable simply means
   * no picture — the session still runs, which matters because head-yaw
   * steering does not depend on seeing anything.
   */
  cameraStreamUrl?: string;
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

function quaternionYaw(o: DOMPointReadOnly): number {
  return Math.atan2(
    2 * (o.w * o.y + o.x * o.z),
    1 - 2 * (o.y * o.y + o.z * o.z),
  );
}

/** Wrap an angle to (-pi, pi]. */
function normalizeAngle(a: number): number {
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

type Vec3 = [number, number, number];

function subtract(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function angleBetween(a: Vec3, b: Vec3): number {
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

  constructor(callbacks: XrTeleopCallbacks, options: XrTeleopOptions = {}) {
    this.#callbacks = callbacks;
    this.#options = options;
  }

  /** Whether the camera layer has decoded at least one frame. */
  get cameraLive(): boolean {
    return this.#camera?.hasFrame ?? false;
  }

  get active(): boolean {
    return this.#session !== null;
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
    if (this.#session) return;
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
      if (!gl)
        throw new Error(
          "No se pudo crear un contexto WebGL para la sesión XR.",
        );
      await gl.makeXRCompatible();
      const layer = new XRWebGLLayer(session, gl);
      await session.updateRenderState({ baseLayer: layer });

      if (this.#options.cameraStreamUrl) {
        this.#camera = new CameraLayer(gl);
        this.#camera.attach(this.#options.cameraStreamUrl);
      }

      let referenceSpace: XRReferenceSpace;
      try {
        referenceSpace = await session.requestReferenceSpace("local-floor");
      } catch {
        referenceSpace = await session.requestReferenceSpace("local");
      }

      this.#session = session;
      this.#referenceSpace = referenceSpace;
      this.#needsRecenter = true;
      this.#handsSeen = false;

      session.addEventListener("end", () => {
        this.#session = null;
        this.#referenceSpace = null;
        this.#mode = null;
        // Disposing cuts the <img> src, which is what actually closes the
        // MJPEG request — an ended session must not keep pulling frames.
        this.#camera?.dispose();
        this.#camera = null;
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
        // The camera IS the view in VR; in passthrough it is a heads-up layer
        // over the room, so it does not paint over what the operator can see.
        this.#camera?.draw(this.#mode !== "immersive-ar");

        const pose = frame.getViewerPose(space);
        if (!pose) return; // tracking lost this frame — the caller's own
        // staleness timeout (not this module) decides when that should stop
        // the robot; a single missed frame at 72-120Hz isn't it.

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

  /** End the session. `onEnd` fires once the browser confirms teardown. */
  stop(): void {
    void this.#session?.end();
  }
}
