/**
 * Tests for the frame the headset transmits.
 *
 * These exist because of one bug, and it is worth stating plainly. The sender
 * used to hold the last payload it was handed and resend it every tick. So
 * anything that stopped updating kept going out as though it were current:
 * end the XR session — or merely lose hand tracking — with the operator's head
 * turned, and the last large yaw was retransmitted 30 times a second while the
 * robot kept rotating. The bridge's 8-second continuous-motion latch bounded
 * it, but eight seconds of rotation nobody asked for is not a defence.
 *
 * The fix was to make the sender PULL, and to prove freshness here on every
 * send. Everything below is a case where stale state must read as zero.
 *
 * Lives outside `src/` deliberately. svelte-check type-checks everything under
 * `src/` against the app's tsconfig, which does not know `bun:test` — and the
 * obvious fix, adding `bun-types`, globally redefines `fetch` and breaks
 * `chat/+page.svelte`. Keeping tests out of the app's source tree costs one
 * relative import and nothing else.
 */

import { describe, expect, test } from "bun:test";
import { buildFrame, type TeleopInput } from "../src/lib/teleop/stream";

const STALE_AFTER = 800;
const DEADZONE = (8 * Math.PI) / 180;
const BIG_YAW = 0.7; // well past the deadzone

function input(overrides: Partial<TeleopInput> = {}): TeleopInput {
  return {
    now: 10_000,
    vrActive: true,
    lastSampleAt: 10_000,
    staleAfterMs: STALE_AFTER,
    yawErrorRadians: 0,
    yawDeadzoneRadians: DEADZONE,
    headPosition: [0, 1.62, 0],
    left: null,
    right: null,
    walking: null,
    armsRequested: false,
    ...overrides,
  };
}

const hand = {
  position: [0.2, 1.3, -0.3] as [number, number, number],
  orientation: [0, 0, 0, 1] as [number, number, number, number],
  grip: 0.5,
};

describe("dead-man", () => {
  test("idle commands nothing", () => {
    const f = buildFrame(input());
    expect(f.enabled).toBe(false);
    expect(f.walk).toBe(0);
    expect(f.head.yaw).toBe(0);
  });

  test("a held walk button is a held dead-man", () => {
    expect(buildFrame(input({ walking: "forward" })).enabled).toBe(true);
    expect(buildFrame(input({ walking: "forward" })).walk).toBe(1);
    expect(buildFrame(input({ walking: "back" })).walk).toBe(-1);
  });

  test("a head turned past the deadzone is a held dead-man", () => {
    expect(buildFrame(input({ yawErrorRadians: BIG_YAW })).enabled).toBe(true);
  });

  test("a head inside the deadzone is not", () => {
    const f = buildFrame(input({ yawErrorRadians: DEADZONE * 0.9 }));
    expect(f.enabled).toBe(false);
  });

  test("asking for the arms alone does not hold the dead-man open forever", () => {
    // It does enable the frame -- the operator's hands are busy being tracked
    // and cannot also press something -- but it must not survive staleness.
    expect(buildFrame(input({ armsRequested: true })).enabled).toBe(true);
    expect(
      buildFrame(input({ armsRequested: true, vrActive: false })).enabled,
    ).toBe(false);
  });
});

describe("staleness — the bug this file exists for", () => {
  test("ending the XR session zeroes a yaw that was large", () => {
    const turned = input({ yawErrorRadians: BIG_YAW });
    expect(buildFrame(turned).head.yaw).toBeCloseTo(BIG_YAW);

    const ended = buildFrame({ ...turned, vrActive: false });
    expect(ended.head.yaw).toBe(0);
    expect(ended.enabled).toBe(false);
  });

  test("losing tracking mid-session zeroes a yaw that was large", () => {
    // The likelier failure: the session is still live, samples just stopped.
    const stale = buildFrame(
      input({
        yawErrorRadians: BIG_YAW,
        lastSampleAt: 10_000 - STALE_AFTER - 1,
      }),
    );
    expect(stale.head.yaw).toBe(0);
    expect(stale.enabled).toBe(false);
  });

  test("a sample just inside the window is still trusted", () => {
    const fresh = buildFrame(
      input({
        yawErrorRadians: BIG_YAW,
        lastSampleAt: 10_000 - STALE_AFTER + 1,
      }),
    );
    expect(fresh.head.yaw).toBeCloseTo(BIG_YAW);
  });

  test("stale hands report untracked, not their last pose", () => {
    const live = buildFrame(input({ left: hand, right: hand }));
    expect(live.hands.left?.tracked).toBe(true);
    expect(live.hands.right?.grip).toBe(0.5);

    const stale = buildFrame(
      input({ left: hand, right: hand, vrActive: false }),
    );
    expect(stale.hands.left?.tracked).toBe(false);
    expect(stale.hands.right?.tracked).toBe(false);
    expect(stale.hands.left?.pos).toBeUndefined();
  });

  test("stale head position falls back rather than persisting", () => {
    const stale = buildFrame(
      input({ headPosition: [5, 9, -5], vrActive: false }),
    );
    expect(stale.head.pos).toEqual([0, 1.6, 0]);
  });

  test("the arms are dropped the moment tracking is not fresh", () => {
    expect(buildFrame(input({ armsRequested: true })).arms).toBe(true);
    expect(
      buildFrame(input({ armsRequested: true, lastSampleAt: 0 })).arms,
    ).toBe(false);
  });

  test("walking still works with no headset at all", () => {
    // The buttons are not headset-derived, so staleness must not disable them
    // -- an operator holding a phone or a laptop can still drive.
    const f = buildFrame(input({ vrActive: false, walking: "forward" }));
    expect(f.enabled).toBe(true);
    expect(f.walk).toBe(1);
    expect(f.head.yaw).toBe(0);
  });
});
