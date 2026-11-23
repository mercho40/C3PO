/**
 * WebXR input, reduced to numbers: controller buttons and the walk stick, the
 * head's heading, and finger closure from hand-tracking joints.
 *
 * Pure helpers, moved out of `xr-teleop.ts` intact so they can be read (and
 * tested, xr-teleop.test.ts) without the session around them. The session
 * calls them once per frame; `xr-teleop.ts` re-exports them.
 */

/** Index in the `xr-standard` gamepad mapping. */
export const BUTTON_TRIGGER = 0;
export const BUTTON_GRIP = 1;
export const BUTTON_PRIMARY = 4; // A on the right controller, X on the left
export const BUTTON_SECONDARY = 5; // B on the right controller, Y on the left

/**
 * The gamepad of a Touch controller, or null for anything else.
 *
 * A TRACKED HAND CAN HAVE A GAMEPAD TOO. Quest Browser exposes hand input
 * sources with a gamepad whose button 0 is the PINCH — the same index as the
 * controller trigger. Reading it as one meant an operator closing their hand
 * (to grip, or just mirroring the arms) fired whichever preset was selected,
 * and a hand's axes could in principle steer the walk. Presets and walking are
 * controller gestures; hands only ever drive the arm mirror, through
 * `#sampleHands`, which reads joints and not this.
 */
export function controllerGamepad(source: {
  hand?: unknown;
  gamepad?: Gamepad | null;
}): Gamepad | null {
  if (source.hand) return null;
  return source.gamepad ?? null;
}

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

export function measureGrip(
  frame: XRFrame,
  hand: XRHand,
  space: XRSpace,
): number {
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
