/**
 * Tests for the headset maths: the yaw that steers, and the curl that grips.
 *
 * `quaternionYaw` is the entire steering signal. It worked on the robot — three
 * measured turns, correct direction each time — but a single sample of "it went
 * left" does not pin a function against pitch, roll, wrap, or quaternion
 * double-cover. That is what these do.
 *
 * `fingerCurl` has never executed anywhere. Hand tracking has not once produced
 * a frame in a real session, so every claim made about it so far is unverified
 * by anything. Most important is the scale-free property, which is the entire
 * reason curl was chosen over fingertip-to-palm distance: without it, a small
 * hand reads as permanently clenched.
 */

import { describe, expect, test } from "bun:test";
import {
  angleBetween,
  drawPerEye,
  fingerCurl,
  restoreOverlayBackground,
  stripOverlayBackground,
  normalizeAngle,
  quaternionYaw,
  subtract,
  walkAxisFrom,
  WALK_STICK_DEADZONE,
  type Vec3,
} from "../src/lib/webxr/xr-teleop";

/** A quaternion for a yaw-only rotation about WebXR's up axis. */
function yawQuat(radians: number) {
  return { x: 0, y: Math.sin(radians / 2), z: 0, w: Math.cos(radians / 2) };
}

const DEG = Math.PI / 180;

describe("quaternionYaw — the steering signal", () => {
  test("identity is zero", () => {
    expect(quaternionYaw({ x: 0, y: 0, z: 0, w: 1 })).toBeCloseTo(0, 6);
  });

  test("recovers the yaw it was built from", () => {
    for (const deg of [-170, -90, -45, -6, 0, 6, 45, 90, 170]) {
      expect(quaternionYaw(yawQuat(deg * DEG))).toBeCloseTo(deg * DEG, 6);
    }
  });

  test("left is positive — the convention the robot was measured against", () => {
    // Verified on hardware 2026-08-20: a positive commanded yaw rotated the
    // G1 counterclockwise, three times. This keeps the client half of that
    // agreement honest.
    expect(quaternionYaw(yawQuat(30 * DEG))).toBeGreaterThan(0);
    expect(quaternionYaw(yawQuat(-30 * DEG))).toBeLessThan(0);
  });

  test("q and -q are the same rotation and must give the same yaw", () => {
    // Quaternion double-cover. A tracker is free to hand us either sign, and
    // reading them differently would flip the steering mid-session.
    const q = yawQuat(50 * DEG);
    const negated = { x: -q.x, y: -q.y, z: -q.z, w: -q.w };
    expect(quaternionYaw(negated)).toBeCloseTo(quaternionYaw(q), 6);
  });

  test("pitch alone does not produce yaw", () => {
    // Looking up and down must not steer the robot. Head pitch is deliberately
    // dropped — there is no wired neck control to send it to.
    for (const deg of [-60, -30, 30, 60]) {
      const pitch = {
        x: Math.sin((deg * DEG) / 2),
        y: 0,
        z: 0,
        w: Math.cos((deg * DEG) / 2),
      };
      expect(Math.abs(quaternionYaw(pitch))).toBeLessThan(1e-6);
    }
  });

  test("roll alone does not produce yaw", () => {
    for (const deg of [-40, 40]) {
      const roll = {
        x: 0,
        y: 0,
        z: Math.sin((deg * DEG) / 2),
        w: Math.cos((deg * DEG) / 2),
      };
      expect(Math.abs(quaternionYaw(roll))).toBeLessThan(1e-6);
    }
  });

  test("PITCH-INDEPENDENT: looking down must not cost steering", () => {
    // The reason this is a projection and not an Euler extraction. The obvious
    // atan2(2(wy+xz), 1-2(y²+z²)) reads a 40 degree turn as 36 at 30 degrees
    // of down-look and 22.8 at 60 — so an operator watching the robot's feet,
    // which is the natural thing to do, quietly loses half their authority.
    const yaw = 40 * DEG;
    for (const pitchDeg of [0, 15, 30, 45, 60, 89]) {
      const pitch = pitchDeg * DEG;
      const [cy, sy] = [Math.cos(yaw / 2), Math.sin(yaw / 2)];
      const [cp, sp] = [Math.cos(pitch / 2), Math.sin(pitch / 2)];
      const q = { w: cy * cp, x: cy * sp, y: sy * cp, z: -sy * sp };
      expect(quaternionYaw(q)).toBeCloseTo(yaw, 5);
    }
  });

  test("straight down still yields a number, not NaN", () => {
    // No horizontal gaze left to project. A NaN here sails through every
    // clamp downstream and lands in a velocity command.
    const p = 90 * DEG;
    const q = { x: Math.sin(p / 2), y: 0, z: 0, w: Math.cos(p / 2) };
    const heading = quaternionYaw(q);
    expect(Number.isFinite(heading)).toBe(true);
  });
});

describe("normalizeAngle", () => {
  test("wraps into (-pi, pi]", () => {
    expect(normalizeAngle(0)).toBeCloseTo(0, 9);
    expect(normalizeAngle(Math.PI * 1.5)).toBeCloseTo(-Math.PI * 0.5, 9);
    expect(normalizeAngle(-Math.PI * 1.5)).toBeCloseTo(Math.PI * 0.5, 9);
    expect(normalizeAngle(Math.PI * 2)).toBeCloseTo(0, 9);
  });

  test("the short way round the back — recentred behind you", () => {
    // Recentre facing north, then turn to just past south. The error must be
    // ~-179 degrees, not ~+181: the robot should take the short path.
    const err = normalizeAngle(181 * DEG);
    expect(err).toBeLessThan(0);
    expect(Math.abs(err)).toBeLessThan(Math.PI);
  });
});

describe("fingerCurl — never executed in a real session", () => {
  const straight: [Vec3, Vec3, Vec3] = [
    [0, 0, 0],
    [0, 0, 1],
    [0, 0, 2],
  ];

  test("a straight finger is open", () => {
    expect(fingerCurl(...straight)).toBeCloseTo(0, 6);
  });

  test("a right-angled finger is partly closed", () => {
    const bent: [Vec3, Vec3, Vec3] = [
      [0, 0, 0],
      [0, 0, 1],
      [0, 1, 1],
    ];
    const curl = fingerCurl(...bent);
    expect(curl).toBeGreaterThan(0.5);
    expect(curl).toBeLessThan(1.0);
  });

  test("SCALE-FREE: a small hand and a large hand read the same", () => {
    // The entire reason curl was chosen over fingertip-to-palm distance. An
    // uncalibrated distance measure reads a child's hand as a permanent fist.
    const small: [Vec3, Vec3, Vec3] = [
      [0, 0, 0],
      [0, 0, 0.3],
      [0, 0.3, 0.3],
    ];
    const large: [Vec3, Vec3, Vec3] = [
      [0, 0, 0],
      [0, 0, 1.4],
      [0, 1.4, 1.4],
    ];
    expect(fingerCurl(...small)).toBeCloseTo(fingerCurl(...large), 6);
  });

  test("translation-free: the same gesture anywhere in the room", () => {
    const moved = straight.map(
      (p) => [p[0] + 3, p[1] - 2, p[2] + 9] as Vec3,
    ) as [Vec3, Vec3, Vec3];
    expect(fingerCurl(...moved)).toBeCloseTo(fingerCurl(...straight), 6);
  });

  test("clamped to [0,1] however far it folds", () => {
    const folded: [Vec3, Vec3, Vec3] = [
      [0, 0, 0],
      [0, 0, 1],
      [0, 0, 0],
    ];
    const curl = fingerCurl(...folded);
    expect(curl).toBeLessThanOrEqual(1);
    expect(curl).toBeGreaterThanOrEqual(0);
  });

  test("degenerate joints report open, never closed", () => {
    // Coincident points give no direction. An unknown grip must read as OPEN:
    // on a fitted hand, "closed" is a command to clench around whatever is
    // in front of it, and these hands have no firmware deadman.
    const degenerate: [Vec3, Vec3, Vec3] = [
      [1, 1, 1],
      [1, 1, 1],
      [1, 1, 1],
    ];
    expect(fingerCurl(...degenerate)).toBe(0);
  });
});

describe("vector helpers", () => {
  test("angleBetween is symmetric and handles parallel/antiparallel", () => {
    const a: Vec3 = [1, 0, 0];
    const b: Vec3 = [0, 1, 0];
    expect(angleBetween(a, b)).toBeCloseTo(Math.PI / 2, 6);
    expect(angleBetween(b, a)).toBeCloseTo(Math.PI / 2, 6);
    expect(angleBetween(a, [2, 0, 0])).toBeCloseTo(0, 6);
    expect(angleBetween(a, [-1, 0, 0])).toBeCloseTo(Math.PI, 6);
  });

  test("a zero-length vector gives zero rather than NaN", () => {
    // acos of a division by zero is NaN, and a NaN grip would sail through
    // every clamp downstream into a motor command.
    expect(angleBetween([0, 0, 0], [1, 0, 0])).toBe(0);
  });

  test("subtract", () => {
    expect(subtract([3, 5, 7], [1, 2, 3])).toEqual([2, 3, 4]);
  });
});

/**
 * The bug that cost two headset sessions.
 *
 * Nothing threw, nothing logged, and the code read as correct. The renderer
 * simply never called `gl.viewport`, so every draw went into the 300x150
 * default seeded from an unsized canvas — a postage stamp in the corner of one
 * eye of a ~2064x2208-per-eye framebuffer. It was reported as "the camera does
 * not work" and sent us to the perception container and the SSH tunnel.
 *
 * These assert the two things that make it visible: the viewport is set from
 * the LAYER (never assumed), and it is set once PER EYE.
 */
describe("drawPerEye — why the camera was invisible", () => {
  type Rect = { x: number; y: number; width: number; height: number };

  function harness(views: string[], viewports: (Rect | null)[]) {
    const viewportCalls: number[][] = [];
    const draws: boolean[] = [];
    const gl = {
      viewport: (x: number, y: number, w: number, h: number) =>
        viewportCalls.push([x, y, w, h]),
    } as unknown as WebGLRenderingContext;
    let i = 0;
    const layer = {
      getViewport: () => viewports[i++] ?? null,
    } as unknown as XRWebGLLayer;
    const pose = { views } as unknown as XRViewerPose;
    const camera = { draw: (opaque: boolean) => draws.push(opaque) };
    return { gl, layer, pose, camera, viewportCalls, draws };
  }

  const LEFT = { x: 0, y: 0, width: 2064, height: 2208 };
  const RIGHT = { x: 2064, y: 0, width: 2064, height: 2208 };

  test("sets the viewport for each eye, from the layer", () => {
    const h = harness(["left", "right"], [LEFT, RIGHT]);
    expect(drawPerEye(h.gl, h.layer, h.pose, h.camera, true)).toBeNull();

    expect(h.viewportCalls).toEqual([
      [0, 0, 2064, 2208],
      [2064, 0, 2064, 2208],
    ]);
  });

  test("draws once per eye — a single draw fills only one", () => {
    const h = harness(["left", "right"], [LEFT, RIGHT]);
    drawPerEye(h.gl, h.layer, h.pose, h.camera, true);
    expect(h.draws.length).toBe(2);
  });

  test("passes opacity through — opaque in VR, blended over passthrough in AR", () => {
    const vr = harness(["left", "right"], [LEFT, RIGHT]);
    drawPerEye(vr.gl, vr.layer, vr.pose, vr.camera, true);
    expect(vr.draws).toEqual([true, true]);

    const ar = harness(["left", "right"], [LEFT, RIGHT]);
    drawPerEye(ar.gl, ar.layer, ar.pose, ar.camera, false);
    expect(ar.draws).toEqual([false, false]);
  });

  test("a view with no viewport is skipped, not drawn into the previous one", () => {
    // Leaving the previous eye's viewport set would draw the second eye's
    // image on top of the first — a doubled image in one eye and nothing in
    // the other, which in a headset reads as a broken display rather than a
    // missing viewport.
    const h = harness(["left", "right"], [LEFT, null]);
    drawPerEye(h.gl, h.layer, h.pose, h.camera, true);
    expect(h.viewportCalls.length).toBe(1);
    expect(h.draws.length).toBe(1);
  });

  test("a throwing draw is returned, not propagated", () => {
    // This callback is what samples head pose. An exception escaping it kills
    // steering for the rest of the session — the robot stays safe, and stops
    // responding, with nothing on screen to say why.
    const boom = new Error("shader link failed");
    const gl = { viewport: () => {} } as unknown as WebGLRenderingContext;
    const layer = { getViewport: () => LEFT } as unknown as XRWebGLLayer;
    const pose = { views: ["left", "right"] } as unknown as XRViewerPose;
    const camera = {
      draw: () => {
        throw boom;
      },
    };

    expect(drawPerEye(gl, layer, pose, camera, true)).toBe(boom);
  });

  test("no views is not an error — tracking can be mid-recovery", () => {
    const h = harness([], []);
    expect(drawPerEye(h.gl, h.layer, h.pose, h.camera, true)).toBeNull();
    expect(h.draws.length).toBe(0);
  });
});

/**
 * The second independent cause of "no camera" — the one that would have
 * survived fixing the first.
 *
 * The DOM overlay composites ON TOP of the WebGL layer. Everything the
 * renderer draws — the camera quad, and under passthrough the room itself — is
 * behind the overlay root. The page styles that root `bg-background`
 * (#06090f, fully opaque) and the UA promotes it to `position: fixed;
 * inset: 0`. So the camera was drawn correctly and then painted over, edge to
 * edge, and the operator would have seen exactly the same black field.
 */
describe("overlay transparency — drawn correctly, then painted over", () => {
  function root(background = "", display = "") {
    return { style: { background, display } } as unknown as HTMLElement;
  }

  test("an opaque background is stripped", () => {
    const el = root("rgb(6, 9, 15)", "flex");
    stripOverlayBackground(el);
    expect(el.style.background).toBe("transparent");
  });

  test("the previous values come back exactly", () => {
    // Not "reset to empty" — the console is a normal page after the session
    // ends, and a transparent body there shows the browser's background.
    const el = root("rgb(6, 9, 15)", "flex");
    const previous = stripOverlayBackground(el);
    restoreOverlayBackground(el, previous);

    expect(el.style.background).toBe("rgb(6, 9, 15)");
    expect(el.style.display).toBe("flex");
  });

  test("a root with no inline style is restored to having none", () => {
    // The real page styles the root with a class, not inline — so the correct
    // restore is the empty string, letting the stylesheet apply again. Writing
    // a literal colour back would pin it past a theme change.
    const el = root("", "");
    const previous = stripOverlayBackground(el);
    expect(el.style.background).toBe("transparent");

    restoreOverlayBackground(el, previous);
    expect(el.style.background).toBe("");
    expect(el.style.display).toBe("");
  });

  test("flex is re-asserted, because the UA forces display:block on :xr-overlay", () => {
    const el = root("rgb(6, 9, 15)", "");
    stripOverlayBackground(el);
    expect(el.style.display).toBe("flex");
  });
});

describe("walkAxisFrom — the thumbstick that walks the robot", () => {
  /**
   * The sign is the whole risk here. Getting it backwards means the robot
   * walks TOWARD the operator when they pull back to stop it — the panic
   * gesture producing the opposite of what it asks for, on a 35 kg humanoid,
   * driven by someone whose eyes are covered.
   *
   * Per the gamepad spec, stick Y is NEGATIVE when pushed away from the user.
   */
  const AWAY = -1; // pushed away from the operator
  const TOWARD = 1; // pulled back toward the operator

  test("pushing the stick AWAY walks forward", () => {
    expect(walkAxisFrom([0, 0, 0, AWAY])).toBe(1);
  });

  test("pulling the stick BACK walks backward", () => {
    expect(walkAxisFrom([0, 0, 0, TOWARD])).toBe(-1);
  });

  test("a resting thumb is not a walk request", () => {
    for (const y of [0, 0.1, -0.1, 0.3, -0.3, 0.59, -0.59]) {
      expect(walkAxisFrom([0, 0, 0, y])).toBe(0);
    }
  });

  test("the deadzone boundary is inclusive, so exactly-at-threshold moves", () => {
    expect(walkAxisFrom([0, 0, 0, -WALK_STICK_DEADZONE])).toBe(1);
    expect(walkAxisFrom([0, 0, 0, WALK_STICK_DEADZONE])).toBe(-1);
  });

  test("falls back to the two-axis layout when there is no second pair", () => {
    // Some runtimes report only [x, y]. Reading axes[3] there is undefined,
    // and undefined must not read as "not walking" when the operator IS.
    expect(walkAxisFrom([0, AWAY])).toBe(1);
    expect(walkAxisFrom([0, TOWARD])).toBe(-1);
    expect(walkAxisFrom([0, 0])).toBe(0);
  });

  test("no controller, no axes, or garbage is 0 — never a movement", () => {
    expect(walkAxisFrom(null)).toBe(0);
    expect(walkAxisFrom(undefined)).toBe(0);
    expect(walkAxisFrom([])).toBe(0);
    expect(walkAxisFrom([0])).toBe(0);
    expect(walkAxisFrom([0, NaN])).toBe(0);
    expect(walkAxisFrom([0, 0, 0, NaN])).toBe(0);
    expect(walkAxisFrom([0, Infinity])).toBe(0);
  });

  test("a fully deflected stick is the same intent as a just-past one", () => {
    // No proportional speed: walk_velocity's own clamp owns the magnitude, and
    // a stick that goes faster the harder you push is a stick that surprises
    // you at the extremes.
    expect(walkAxisFrom([0, 0, 0, -1])).toBe(walkAxisFrom([0, 0, 0, -0.61]));
  });
});
