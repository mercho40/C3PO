/**
 * Tests for the per-eye placement that stopped the headset showing two of
 * everything.
 *
 * WHAT THESE ARE ACTUALLY ASSERTING
 * ---------------------------------
 * Not "the numbers changed". Whether an operator sees one panel or two is a
 * question about GEOMETRY: given where each eye is and what each eye's
 * projection is, do the two drawn images correspond to a single point in
 * space, and does fusing them require the eyes to CONVERGE (which they can) or
 * to DIVERGE (which they cannot)?
 *
 * So each test here un-projects what the layer would draw back into a gaze
 * direction, and checks the two eyes agree. That is testable off-hardware and
 * it is the actual property; a golden-number test would have passed happily
 * with the old code, which is why the bug survived three placement rounds.
 *
 * The frusta below are a Quest 3's real shape — asymmetric, mirrored between
 * the eyes — because a symmetric frustum hides this bug completely.
 */

import { describe, expect, test } from "bun:test";
import {
  eyeOffset,
  fovAspect,
  HUD_DISTANCE_M,
  placeQuad,
  transformPoint,
  type EyePose,
} from "../src/lib/webxr/stereo";

const DEG = Math.PI / 180;
/** Half the interpupillary distance: eyes about 64 mm apart. */
const HALF_IPD = 0.032;

/**
 * An OpenGL frustum matrix from the four half-angles, in degrees.
 *
 * Column-major, the layout `XRView.projectionMatrix` uses.
 */
function frustum(
  leftDeg: number,
  rightDeg: number,
  upDeg: number,
  downDeg: number,
): Float32Array {
  const n = 0.1;
  const f = 1000;
  const l = -n * Math.tan(leftDeg * DEG);
  const r = n * Math.tan(rightDeg * DEG);
  const t = n * Math.tan(upDeg * DEG);
  const b = -n * Math.tan(downDeg * DEG);
  const m = new Float32Array(16);
  m[0] = (2 * n) / (r - l);
  m[5] = (2 * n) / (t - b);
  m[8] = (r + l) / (r - l);
  m[9] = (t + b) / (t - b);
  m[10] = -(f + n) / (f - n);
  m[11] = -1;
  m[14] = (-2 * f * n) / (f - n);
  return m;
}

/**
 * A Quest 3's eyes: wider outward than inward, which is the whole problem.
 *
 * The 10 degrees between the two eyes' 52/42 split is what the old code
 * silently turned into 10 degrees of divergence.
 */
const LEFT_EYE: EyePose = {
  projection: frustum(52, 42, 52, 52),
  offset: [-HALF_IPD, 0, 0],
};
const RIGHT_EYE: EyePose = {
  projection: frustum(42, 52, 52, 52),
  offset: [HALF_IPD, 0, 0],
};

/** A perfectly symmetric eye at the origin — the case that hides everything. */
const IDEAL_EYE: EyePose = {
  projection: frustum(45, 45, 45, 45),
  offset: [0, 0, 0],
};

/**
 * Where this eye has to look to see something drawn at `ndc`, as a tangent.
 *
 * The inverse of what `placeQuad` does: `ndc.x = p0 * tanX - p8`, so
 * `tanX = (ndc.x + p8) / p0`. Positive is to the eye's right.
 */
function gaze(eye: EyePose, ndc: { ox: number; oy: number }) {
  const P = eye.projection;
  return {
    tanX: (ndc.ox + P[8]) / P[0],
    tanY: (ndc.oy + P[9]) / P[5],
  };
}

/** Where this eye's line of sight is when it reaches `distance` ahead. */
function sightAt(eye: EyePose, ndc: { ox: number; oy: number }, d: number) {
  const g = gaze(eye, ndc);
  return { x: eye.offset[0] + g.tanX * d, y: eye.offset[1] + g.tanY * d };
}

const PANEL = { ox: 0, oy: 0.32, sx: 0.44, aspect: 430 / 640 };
const RADAR = { ox: -0.42, oy: -0.44, sx: 0.2, aspect: 1 };

describe("the doubling: what the old constant-clip-space placement did", () => {
  test("both eyes drew at the same spot, and that spot DIVERGES", () => {
    // The old code, exactly: one offset uniform, both eyes.
    const old = { ox: PANEL.ox, oy: PANEL.oy };

    const l = gaze(LEFT_EYE, old);
    const r = gaze(RIGHT_EYE, old);

    // The left eye is sent LEFT of its own forward axis and the right eye
    // RIGHT of its own. That is the two images pulling apart outward.
    expect(l.tanX).toBeLessThan(0);
    expect(r.tanX).toBeGreaterThan(0);

    // Fusing a real point requires the LEFT eye to aim further RIGHT than the
    // right eye does — that is what convergence is. This is the opposite
    // ordering, so there is no distance, however far, at which these two lines
    // of sight meet in front of the operator. They meet nowhere. Two panels.
    expect(l.tanX).toBeLessThan(r.tanX);

    // And by a lot: about ten degrees, far beyond any vergence range.
    const apart =
      (Math.atan(r.tanX) - Math.atan(l.tanX)) / DEG;
    expect(apart).toBeGreaterThan(8);
  });

  test("a symmetric frustum hides it completely — why it was never caught", () => {
    // Two ideal eyes at the same point see no disparity at all, so the old
    // code looked perfect in every test that used a tidy projection. The
    // asymmetry above is not an edge case; it is what the hardware does.
    const old = { ox: PANEL.ox, oy: PANEL.oy };
    expect(gaze(IDEAL_EYE, old).tanX).toBeCloseTo(0, 6);
  });
});

describe("placeQuad: both eyes look at ONE point", () => {
  for (const [name, spec] of [
    ["panel", PANEL],
    ["radar", RADAR],
  ] as const) {
    test(`${name} — the two lines of sight meet where the quad is`, () => {
      const l = placeQuad(LEFT_EYE, spec);
      const r = placeQuad(RIGHT_EYE, spec);

      // The eyes are drawn at DIFFERENT places on their render targets, which
      // is the point: identical coordinates were the bug.
      expect(l.ox).not.toBeCloseTo(r.ox, 4);

      // Follow each eye's line of sight out to the panel's distance. Both
      // arrive at the same place — so there IS a single object there.
      const hitL = sightAt(LEFT_EYE, l, HUD_DISTANCE_M);
      const hitR = sightAt(RIGHT_EYE, r, HUD_DISTANCE_M);
      expect(hitL.x).toBeCloseTo(hitR.x, 4);
      expect(hitL.y).toBeCloseTo(hitR.y, 4);
    });
  }

  test("and the vergence is CONVERGENT, which eyes can actually do", () => {
    const l = placeQuad(LEFT_EYE, PANEL);
    const r = placeQuad(RIGHT_EYE, PANEL);
    // The ordering that was inverted before: left eye aims further right.
    expect(gaze(LEFT_EYE, l).tanX).toBeGreaterThan(gaze(RIGHT_EYE, r).tanX);
  });

  test("the object sits at the distance it claims, not at infinity", () => {
    const l = placeQuad(LEFT_EYE, PANEL);
    const r = placeQuad(RIGHT_EYE, PANEL);
    const gl = gaze(LEFT_EYE, l).tanX;
    const gr = gaze(RIGHT_EYE, r).tanX;
    // Two rays from x = -+HALF_IPD converging at distance d satisfy
    // (gl - gr) * d = 2 * HALF_IPD. Solving for d recovers the distance the
    // quad was placed at — the depth cue the old code carried none of.
    expect((2 * HALF_IPD) / (gl - gr)).toBeCloseTo(HUD_DISTANCE_M, 3);
  });

  test("the panel is the same size in both eyes", () => {
    // Different positions, identical sizes: a size difference between the
    // eyes reads as a tilt that is not there.
    const l = placeQuad(LEFT_EYE, PANEL);
    const r = placeQuad(RIGHT_EYE, PANEL);
    expect(l.sx).toBeCloseTo(r.sx, 5);
    expect(l.sy).toBeCloseTo(r.sy, 5);
  });
});

describe("the placement the operator signed off on is not moved", () => {
  test("an ideal eye reproduces the authored constants exactly", () => {
    // The angular position and size are UNCHANGED by this fix — it changes
    // where each eye draws them, not where they are. Anything else would
    // silently undo three sessions of placement tuning, including the
    // 2026-08-27 move to the centre that this bug was hiding behind.
    const p = placeQuad(IDEAL_EYE, PANEL);
    expect(p.ox).toBeCloseTo(PANEL.ox, 5);
    expect(p.oy).toBeCloseTo(PANEL.oy, 5);
    expect(p.sx).toBeCloseTo(PANEL.sx, 5);
    // Square field, so the aspect correction is the content's own.
    expect(p.sy).toBeCloseTo(PANEL.sx * PANEL.aspect, 5);
  });

  test("the point both eyes converge on IS the authored angle", () => {
    // Not just "the two eyes agree" — they agree on the RIGHT place. The
    // authored `oy` is read against the eye's own vertical focal term, so the
    // panel ends up at the angle above forward it was tuned to, and the two
    // eyes' lift is identical because their vertical fields are.
    const lift = (eye: EyePose) =>
      (PANEL.oy / eye.projection[5]) * HUD_DISTANCE_M;
    expect(lift(LEFT_EYE)).toBeCloseTo(lift(RIGHT_EYE), 6);

    const hitL = sightAt(LEFT_EYE, placeQuad(LEFT_EYE, PANEL), HUD_DISTANCE_M);
    const hitR = sightAt(
      RIGHT_EYE,
      placeQuad(RIGHT_EYE, PANEL),
      HUD_DISTANCE_M,
    );
    expect(hitL.y).toBeCloseTo(lift(LEFT_EYE), 4);
    expect(hitR.y).toBeCloseTo(lift(RIGHT_EYE), 4);
    // Horizontally the panel is authored dead centre, so that is where the
    // two eyes must meet — neither pulled toward its own render target.
    expect(hitL.x).toBeCloseTo(0, 4);
    expect(hitR.x).toBeCloseTo(0, 4);
  });
});

describe("fovAspect", () => {
  test("is width over height, the orientation the layers had before", () => {
    // A field twice as wide as it is tall gives 2. Inverting this scales a
    // panel by the SQUARE of the aspect, which is the failure the menu
    // layer's comment has warned about since the first deformed camera quad.
    const wide: EyePose = {
      projection: frustum(45, 45, 26.565, 26.565), // tan 45 = 1, tan 26.565 = 0.5
      offset: [0, 0, 0],
    };
    expect(fovAspect(wide)).toBeCloseTo(2, 4);
  });

  test("falls back to a square field rather than to zero or NaN", () => {
    expect(fovAspect(null)).toBe(1);
    expect(fovAspect(undefined)).toBe(1);
    expect(
      fovAspect({ projection: new Float32Array(4), offset: [0, 0, 0] }),
    ).toBe(1);
    expect(
      fovAspect({ projection: new Float32Array(16), offset: [0, 0, 0] }),
    ).toBe(1);
  });
});

/**
 * Every fallback here has the same job: DRAW SOMETHING.
 *
 * A NaN or an infinity in a vertex position does not produce a wrong picture,
 * it produces no picture — which in a headset is the black view that has
 * repeatedly been reported as "the camera is broken" and sent people to the
 * perception container. Monoscopic and visible beats correct and absent.
 */
describe("degenerate input falls back to drawing, never to nothing", () => {
  const bad: Array<[string, EyePose | null | undefined]> = [
    ["no eye at all", null],
    ["undefined eye", undefined],
    ["a truncated matrix", { projection: [1, 2, 3], offset: [0, 0, 0] }],
    [
      "a zero focal term",
      { projection: new Float32Array(16), offset: [0, 0, 0] },
    ],
    [
      "an eye behind the quad",
      { projection: frustum(45, 45, 45, 45), offset: [0, 0, 5] },
    ],
    [
      "a NaN offset",
      { projection: frustum(45, 45, 45, 45), offset: [NaN, 0, 0] },
    ],
  ];

  for (const [name, eye] of bad) {
    test(`${name} still yields finite, non-zero uniforms`, () => {
      const p = placeQuad(eye, PANEL);
      for (const v of [p.ox, p.oy, p.sx, p.sy]) {
        expect(Number.isFinite(v)).toBe(true);
      }
      expect(p.sx).toBeGreaterThan(0);
      expect(p.sy).toBeGreaterThan(0);
    });
  }

  test("a non-positive distance uses the default rather than dividing by it", () => {
    expect(placeQuad(LEFT_EYE, { ...PANEL, distance: 0 })).toEqual(
      placeQuad(LEFT_EYE, PANEL),
    );
    expect(placeQuad(LEFT_EYE, { ...PANEL, distance: -3 })).toEqual(
      placeQuad(LEFT_EYE, PANEL),
    );
  });
});

describe("eyeOffset — the parallax the old code never read", () => {
  test("an identity head frame passes the eye's position through", () => {
    const identity = new Float32Array([
      1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
    ]);
    expect(eyeOffset(identity, { x: -0.032, y: 0.01, z: 0 })).toEqual([
      -0.032, 0.01, 0,
    ]);
  });

  test("a translated head frame gives the offset FROM THE HEAD, not the room", () => {
    // The operator has walked two metres. The eye is still 32 mm from the
    // bridge of their nose, and that — not its position in the room — is what
    // sets the disparity.
    const viewerFromRef = new Float32Array([
      1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -2, 0, 0, 1,
    ]);
    const [x, y, z] = eyeOffset(viewerFromRef, { x: 2 - 0.032, y: 0, z: 0 });
    expect(x).toBeCloseTo(-0.032, 6);
    expect(y).toBeCloseTo(0, 6);
    expect(z).toBeCloseTo(0, 6);
  });

  test("transformPoint applies rotation from a column-major matrix", () => {
    // 90 degrees about +y: +x goes to -z. Getting column- and row-major
    // confused here would put the eyes apart along the wrong axis, which
    // reads as a vertical misalignment and is instantly sickening.
    const yaw90 = new Float32Array([
      0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1,
    ]);
    const [x, y, z] = transformPoint(yaw90, 1, 0, 0);
    expect(x).toBeCloseTo(0, 6);
    expect(y).toBeCloseTo(0, 6);
    expect(z).toBeCloseTo(-1, 6);
  });
});
