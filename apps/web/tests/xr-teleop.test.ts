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
  fingerCurl,
  normalizeAngle,
  quaternionYaw,
  subtract,
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
      const pitch = { x: Math.sin((deg * DEG) / 2), y: 0, z: 0, w: Math.cos((deg * DEG) / 2) };
      expect(Math.abs(quaternionYaw(pitch))).toBeLessThan(1e-6);
    }
  });

  test("roll alone does not produce yaw", () => {
    for (const deg of [-40, 40]) {
      const roll = { x: 0, y: 0, z: Math.sin((deg * DEG) / 2), w: Math.cos((deg * DEG) / 2) };
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
  const straight: [Vec3, Vec3, Vec3] = [[0, 0, 0], [0, 0, 1], [0, 0, 2]];

  test("a straight finger is open", () => {
    expect(fingerCurl(...straight)).toBeCloseTo(0, 6);
  });

  test("a right-angled finger is partly closed", () => {
    const bent: [Vec3, Vec3, Vec3] = [[0, 0, 0], [0, 0, 1], [0, 1, 1]];
    const curl = fingerCurl(...bent);
    expect(curl).toBeGreaterThan(0.5);
    expect(curl).toBeLessThan(1.0);
  });

  test("SCALE-FREE: a small hand and a large hand read the same", () => {
    // The entire reason curl was chosen over fingertip-to-palm distance. An
    // uncalibrated distance measure reads a child's hand as a permanent fist.
    const small: [Vec3, Vec3, Vec3] = [[0, 0, 0], [0, 0, 0.3], [0, 0.3, 0.3]];
    const large: [Vec3, Vec3, Vec3] = [[0, 0, 0], [0, 0, 1.4], [0, 1.4, 1.4]];
    expect(fingerCurl(...small)).toBeCloseTo(fingerCurl(...large), 6);
  });

  test("translation-free: the same gesture anywhere in the room", () => {
    const moved = straight.map((p) => [p[0] + 3, p[1] - 2, p[2] + 9] as Vec3) as
      [Vec3, Vec3, Vec3];
    expect(fingerCurl(...moved)).toBeCloseTo(fingerCurl(...straight), 6);
  });

  test("clamped to [0,1] however far it folds", () => {
    const folded: [Vec3, Vec3, Vec3] = [[0, 0, 0], [0, 0, 1], [0, 0, 0]];
    const curl = fingerCurl(...folded);
    expect(curl).toBeLessThanOrEqual(1);
    expect(curl).toBeGreaterThanOrEqual(0);
  });

  test("degenerate joints report open, never closed", () => {
    // Coincident points give no direction. An unknown grip must read as OPEN:
    // on a fitted hand, "closed" is a command to clench around whatever is
    // in front of it, and these hands have no firmware deadman.
    const degenerate: [Vec3, Vec3, Vec3] = [[1, 1, 1], [1, 1, 1], [1, 1, 1]];
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
