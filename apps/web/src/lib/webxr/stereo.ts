/**
 * Where a head-locked quad goes in EACH EYE, which is not the same place.
 *
 * WHY THIS MODULE EXISTS: EVERYTHING WAS DOUBLED
 * ----------------------------------------------
 * Reported with the headset on, 2026-08-27: "the things are doubled, one per
 * eye (commands table and lidar stuff)".
 *
 * All three layers positioned themselves with a constant clip-space offset —
 * `gl.uniform2f(offsetLoc, 0, 0.32)` — and drew that same constant into both
 * eye viewports. That is only correct if each eye's frustum is symmetric about
 * its own forward axis. On a Quest it is not: the headset renders a wider
 * field outward than inward, so the HORIZONTAL CENTRE OF THE RENDER TARGET IS
 * NOT THE DIRECTION THE EYE LOOKS. It sits roughly 5 degrees temporally, and
 * mirrored between the eyes.
 *
 * So NDC x = 0 in the left eye is about 5 degrees to the left of forward, and
 * NDC x = 0 in the right eye is about 5 degrees to the right: the two images
 * of one panel are ~10 degrees apart, and apart OUTWARD. Outward is divergent
 * disparity. Eyes converge; they do not diverge. There is no vergence angle
 * that fuses those two images, so the operator sees two panels — one per eye —
 * and no amount of trying resolves it.
 *
 * The asymmetry is in the projection matrix all along, in `P[8]` and `P[9]`,
 * which the old code never read. It never read the eye offsets either, so
 * nothing carried the parallax that tells the brain how far away the panel is.
 *
 * WHY IT SURFACED ONLY NOW
 * ------------------------
 * The previous commit moved the panel and the radar out of the periphery and
 * into the middle of the field. The bug did not change; the operator's
 * relationship to it did. At clip-space |x| ~ 0.96 the two images sit far out
 * in the vignette, where the visual system does not attempt to fuse and simply
 * discards one. Brought to the centre, where fusion IS attempted, the same
 * divergence became the most obvious thing in the headset. The placement fix
 * was right and is kept — it just exposed this.
 *
 * WHAT REPLACES IT
 * ----------------
 * A quad is now described by WHERE IT IS, not by where it lands on a render
 * target: an angular direction from the head, an angular size, and a distance
 * in metres. Each eye then projects that description through its OWN
 * projection matrix from its OWN position. Both eyes end up looking at one
 * object, the disparity between them is the disparity that object would really
 * have, and it fuses.
 *
 * ANGLES ARE CARRIED AS "SYMMETRIC-FRUSTUM NDC", DELIBERATELY
 * -----------------------------------------------------------
 * `ox`/`oy`/`sx` are the NDC values the quad WOULD have on a symmetric
 * frustum — i.e. `tan(angle) * P[0]`. That is a real angular unit, but it is
 * also exactly the number the layers already had, tuned over three sessions
 * with the thing on somebody's head. Authoring in these units means this
 * change fixes fusion WITHOUT moving or resizing anything the operator just
 * signed off on: feed it a symmetric frustum and a zero eye offset and it
 * returns the old constants unchanged, which `stereo.test.ts` pins.
 *
 * Metres would have been the more natural unit and were rejected for that
 * reason: converting the tuned constants into metres requires assuming a
 * field of view at authoring time, and being wrong about it would silently
 * resize a panel whose size is the one thing nobody has complained about.
 */

/**
 * How far in front of the head the heads-up layers sit.
 *
 * Sets the VERGENCE, not the size — size is angular and independent of this.
 * 1.6 m is inside the range where vergence and the headset's fixed focal
 * distance disagree least, and far enough that the small residual error in
 * treating the eyes as parallel (see `EyePose.offset`) stays well under a
 * pixel.
 *
 * ONE DISTANCE FOR ALL THREE LAYERS, on purpose. Glancing from the camera
 * picture to the readiness panel to the radar should not make the eyes
 * re-verge each time; at a shared distance they are coplanar and the glance
 * costs nothing. An operator driving a robot is doing that glance constantly.
 */
export const HUD_DISTANCE_M = 1.6;

/**
 * One eye, as this module needs it.
 *
 * `projection` is `XRView.projectionMatrix`: column-major, 16 entries, and the
 * only place the frustum asymmetry that caused the doubling is written down.
 */
export type EyePose = {
  /** Column-major 4x4. `XRView.projectionMatrix`. */
  projection: ArrayLike<number>;
  /**
   * This eye's position in VIEWER space (the head), in metres. Typically
   * about (-0.032, 0, 0) and (0.032, 0, 0).
   *
   * Position only, no rotation: the quad is treated as facing each eye
   * squarely. On a Quest the two displays are parallel, so the rotation
   * between an eye and the viewer is identity and nothing is lost. On a
   * headset with canted displays this leaves a small keystone error, which is
   * a much smaller wrong than the one it replaces — and the vergence, which is
   * what actually broke fusion, is exact either way.
   */
  offset: readonly [number, number, number];
};

/** A head-locked quad, described independently of any eye. */
export type QuadSpec = {
  /** Angular centre, right-positive, as symmetric-frustum NDC. */
  ox: number;
  /** Angular centre, up-positive, as symmetric-frustum NDC. */
  oy: number;
  /** Angular half-width, same units. */
  sx: number;
  /**
   * Content aspect as height / width — 1 for a square canvas.
   *
   * Half-HEIGHT is derived rather than given so a caller cannot specify a
   * shape that contradicts its own texture, which is how the camera quad
   * arrived visibly deformed in the first real session.
   */
  aspect: number;
  /** Metres in front of the head. Defaults to `HUD_DISTANCE_M`. */
  distance?: number;
};

/** What the layer shaders want: a clip-space scale and offset, per eye. */
export type QuadPlacement = { ox: number; oy: number; sx: number; sy: number };

/**
 * Multiply a column-major 4x4 by a point, returning viewer-space xyz.
 *
 * Used for exactly one thing — putting an eye's world position back into
 * viewer space — so it takes w = 1 and returns three numbers rather than
 * growing into a matrix library.
 */
export function transformPoint(
  m: ArrayLike<number>,
  x: number,
  y: number,
  z: number,
): [number, number, number] {
  return [
    m[0] * x + m[4] * y + m[8] * z + m[12],
    m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14],
  ];
}

/**
 * Where this eye's position is, in viewer space.
 *
 * `viewerFromRef` is `XRViewerPose.transform.inverse.matrix` and `eye` is
 * `XRView.transform.position` — both are in the same reference space, so
 * composing them gives the offset of the eye from the head, which is the
 * parallax the old code was missing entirely.
 */
export function eyeOffset(
  viewerFromRef: ArrayLike<number>,
  eye: { x: number; y: number; z: number },
): [number, number, number] {
  return transformPoint(viewerFromRef, eye.x, eye.y, eye.z);
}

/**
 * How much WIDER than tall this eye's field is, in tangent terms: `P[5]/P[0]`.
 *
 * `P[0]` is `1/tan(halfFovX)` and `P[5]` is `1/tan(halfFovY)`, so the ratio is
 * `tan(halfFovX)/tan(halfFovY)` — angular width over angular height, the same
 * orientation as the `vpWidth / vpHeight` it replaces. Getting this the wrong
 * way up gives a panel wrong by the SQUARE of the aspect, which is the failure
 * the menu layer's old comment warned about.
 *
 * This is what the layers used to approximate as `vpWidth / vpHeight`. The
 * pixel aspect of the render target is only ever close to the FOV aspect;
 * `P` states it exactly, and it is the number that decides whether a square
 * canvas comes out square in the headset.
 *
 * Returns 1 without a projection — the same square-field assumption the
 * layers already fell back to, wrong in proportion rather than inverted.
 */
export function fovAspect(eye: EyePose | null | undefined): number {
  const P = eye?.projection;
  if (!P || P.length < 16) return 1;
  const p0 = P[0];
  const p5 = P[5];
  if (!Number.isFinite(p0) || !Number.isFinite(p5) || p0 === 0) return 1;
  const k = p5 / p0;
  return Number.isFinite(k) && k > 0 ? k : 1;
}

/** Every component finite — a NaN uniform draws NOTHING, not a wrong picture. */
function usable(p: QuadPlacement): boolean {
  return (
    Number.isFinite(p.ox) &&
    Number.isFinite(p.oy) &&
    Number.isFinite(p.sx) &&
    Number.isFinite(p.sy)
  );
}

/**
 * Project a head-locked quad into one eye's clip space.
 *
 * `eye` may be null — a caller outside an XR frame, or a runtime that gave us
 * no projection matrix. That returns the quad's authored angular numbers
 * unchanged, which is the pre-2026-08-27 behaviour: monoscopically correct,
 * and the right thing to fall back to, because a HUD that does not fuse is
 * still enormously better than a HUD that does not draw.
 */
export function placeQuad(
  eye: EyePose | null | undefined,
  spec: QuadSpec,
): QuadPlacement {
  const flat: QuadPlacement = {
    ox: spec.ox,
    oy: spec.oy,
    sx: spec.sx,
    sy: spec.sx * spec.aspect,
  };
  if (!eye) return flat;

  const P = eye.projection;
  if (!P || P.length < 16) return flat;
  const p0 = P[0];
  const p5 = P[5];
  const p8 = P[8];
  const p9 = P[9];
  // A projection with a zero focal term is not a projection. Dividing by it
  // gives infinities that propagate into the uniforms and take the layer off
  // the screen altogether.
  if (!Number.isFinite(p0) || !Number.isFinite(p5) || p0 === 0 || p5 === 0) {
    return flat;
  }

  const distance =
    spec.distance && spec.distance > 0 ? spec.distance : HUD_DISTANCE_M;

  // The authored angles, recovered as tangents. This is the step that makes
  // the constants field-of-view independent: `ox` divided by this eye's own
  // focal term is an angle, whatever the headset's optics turn out to be.
  const tanX = spec.ox / p0;
  const tanY = spec.oy / p5;
  const tanHalfW = spec.sx / p0;

  // The anchor, in viewer space. Head-locked: the panel rides with the head,
  // which is what it has always done and what the operator expects of a HUD.
  const ax = tanX * distance;
  const ay = tanY * distance;
  const az = -distance;

  // The same point, seen from THIS eye. This is the whole fix: the two eyes
  // subtract different offsets, so they get different clip-space positions
  // for one object — which is what stereo disparity is.
  const [ex, ey, ez] = eye.offset;
  const x = ax - ex;
  const y = ay - ey;
  const z = az - ez;
  const w = -z;
  // Behind or through the eye. Cannot happen with a sane offset and a
  // positive distance, but a projected point at w <= 0 is a divide that
  // silently turns the layer inside out.
  if (!(w > 0)) return flat;

  // `p8`/`p9` are the frustum's own off-centre, and adding them here is what
  // stops the two eyes from being pushed apart outward.
  const placed: QuadPlacement = {
    ox: (p0 * x + p8 * z) / w,
    oy: (p5 * y + p9 * z) / w,
    // Half-extents scale by distance/w, which is ~1: the eye offset is a few
    // centimetres against 1.6 m. Carried anyway because it is free and it is
    // the difference between "the same size in both eyes" and "the same size
    // to within a rounding error in both eyes".
    sx: p0 * tanHalfW * (distance / w),
    sy: p5 * tanHalfW * spec.aspect * (distance / w),
  };
  return usable(placed) ? placed : flat;
}
