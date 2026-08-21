/**
 * Tests for the in-headset preset menu.
 *
 * The subject here is not layout, it is the GATE. `works_real` is the field
 * that stops an agent — or an operator whose eyes are covered — commanding
 * motion nobody has ever watched run, and this menu is a new way to reach
 * those skills. `dance` is the live example: accepted by the firmware, never
 * observed to work, and labelled NEVER RUN ON HARDWARE in the bridge's own
 * smoke test.
 *
 * So every test below is a way the highlight could come to rest on something
 * unverified, and therefore a way the trigger could dispatch it.
 */

import { describe, expect, test } from "bun:test";
import {
  firstSelectable,
  nextSelectable,
  type MenuItem,
} from "../src/lib/webxr/menu-layer";

const item = (name: string, verified: boolean): MenuItem => ({
  name,
  label: name,
  verified,
});

// The real list as of 2026-08-21: dance is the unverified one.
const REAL = [
  item("wave", true),
  item("dance", false),
  item("shake_hand", true),
  item("hug", true),
  item("clap", true),
  item("release_arm", true),
];

describe("nextSelectable — the highlight never rests on untested motion", () => {
  test("skips an unverified entry", () => {
    // From `wave` (0) the next index is `dance` (1), which must be skipped.
    expect(nextSelectable(REAL, 0)).toBe(2);
    expect(REAL[nextSelectable(REAL, 0)].name).toBe("shake_hand");
  });

  test("wraps around the end of the list", () => {
    expect(nextSelectable(REAL, 5)).toBe(0);
  });

  test("wraps PAST an unverified entry at the wrap point", () => {
    const list = [item("dance", false), item("wave", true)];
    expect(nextSelectable(list, 1)).toBe(1); // only one selectable: stays
  });

  test("cycling the whole list never lands on unverified", () => {
    let i = firstSelectable(REAL);
    for (let n = 0; n < 50; n += 1) {
      expect(REAL[i].verified).toBe(true);
      i = nextSelectable(REAL, i);
    }
  });

  test("an all-unverified catalogue does not spin — it stays put", () => {
    // The dangerous shape: nothing is selectable. Returning some index anyway
    // would put the highlight on untested motion; looping forever would hang
    // the render thread inside a headset.
    const none = [item("dance", false), item("point_at", false)];
    expect(nextSelectable(none, 0)).toBe(0);
    expect(nextSelectable(none, 1)).toBe(1);
  });

  test("an empty catalogue is index 0 and not a crash", () => {
    expect(nextSelectable([], 0)).toBe(0);
    expect(nextSelectable([], 3)).toBe(0);
    expect(firstSelectable([])).toBe(0);
  });

  test("a single verified entry stays on itself rather than moving off", () => {
    const one = [item("wave", true)];
    expect(nextSelectable(one, 0)).toBe(0);
  });
});

describe("firstSelectable — where the highlight starts", () => {
  test("skips a leading unverified entry", () => {
    const list = [item("dance", false), item("wave", true)];
    expect(firstSelectable(list)).toBe(1);
  });

  test("is the first entry when it is verified", () => {
    expect(firstSelectable(REAL)).toBe(0);
  });

  test("falls back to 0 when nothing is verified", () => {
    // Callers must not treat this as "selectable": `menuSelection` re-checks
    // `verified` before returning anything, which is what makes 0 safe here.
    const none = [item("dance", false)];
    expect(firstSelectable(none)).toBe(0);
    expect(none[firstSelectable(none)].verified).toBe(false);
  });
});
