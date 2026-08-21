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
  paintMenu,
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

// --- a 2D context that records what was asked of it -------------------------
//
// Same approach as camera-layer.test.ts stubs WebGL, and honest about the same
// limit: this covers WHAT gets drawn, not whether it is legible through a lens.
// The uncovered half needs a headset.

type Call2D = { fn: string; args: unknown[] };

function stub2d() {
  const calls: Call2D[] = [];
  const rec =
    (fn: string, ret: unknown = undefined) =>
    (...args: unknown[]) => {
      calls.push({ fn, args });
      return ret;
    };
  const ctx: Record<string, unknown> = {
    clearRect: rec("clearRect"),
    fillRect: rec("fillRect"),
    strokeRect: rec("strokeRect"),
    fillText: rec("fillText"),
    beginPath: rec("beginPath"),
    moveTo: rec("moveTo"),
    lineTo: rec("lineTo"),
    stroke: rec("stroke"),
    measureText: rec("measureText", { width: 80 }),
    // Assigned, not called — recorded via the object so assertions can read
    // the last value set.
    font: "",
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    textBaseline: "",
  };
  const texts = () =>
    calls.filter((c) => c.fn === "fillText").map((c) => String(c.args[0]));
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, texts };
}

describe("paintMenu — what the operator is shown", () => {
  test("draws every item's label", () => {
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null);
    for (const it of REAL) {
      expect(texts().some((t) => t.startsWith(it.label))).toBe(true);
    }
  });

  test("an unverified item is struck through and labelled", () => {
    const { ctx, calls, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null);
    // `dance` is the only unverified entry, so exactly one strikethrough.
    expect(calls.filter((c) => c.fn === "stroke").length).toBe(1);
    expect(texts()).toContain("sin probar en real");
  });

  test("an all-verified list draws no strikethrough at all", () => {
    const { ctx, calls, texts } = stub2d();
    const allOk = REAL.map((i) => ({ ...i, verified: true }));
    paintMenu(ctx, allOk, 0, null, null);
    expect(calls.filter((c) => c.fn === "stroke").length).toBe(0);
    expect(texts()).not.toContain("sin probar en real");
  });

  test("every item is struck through when the catalogue could not be read", () => {
    // +page.svelte maps `catalogueFailed` to all-unverified. This is what the
    // operator then sees: six inert entries, not six confident ones.
    const { ctx, calls } = stub2d();
    const none = REAL.map((i) => ({ ...i, verified: false }));
    paintMenu(ctx, none, 0, null, null);
    expect(calls.filter((c) => c.fn === "stroke").length).toBe(none.length);
  });

  test("the busy item is marked, and only that one", () => {
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, "hug");
    expect(texts().some((t) => t === "hug …")).toBe(true);
    expect(texts().some((t) => t === "wave")).toBe(true);
  });

  test("the status line is drawn when there is one", () => {
    const { ctx, texts } = stub2d();
    paintMenu(
      ctx,
      REAL,
      0,
      { text: "Enviado (sin confirmar)", kind: "warn" },
      null,
    );
    expect(texts()).toContain("Enviado (sin confirmar)");
  });

  test("a long status is truncated rather than drawn off the panel", () => {
    const { ctx, texts } = stub2d();
    const long = "x".repeat(200);
    paintMenu(ctx, REAL, 0, { text: long, kind: "error" }, null);
    const drawn = texts().find((t) => t.startsWith("xxx")) ?? "";
    expect(drawn.length).toBeGreaterThan(0);
    expect(drawn.length).toBeLessThan(long.length);
  });

  test("an empty list does not throw", () => {
    const { ctx } = stub2d();
    expect(() => paintMenu(ctx, [], 0, null, null)).not.toThrow();
  });

  test("a selection index past the end does not throw", () => {
    // `setItems` re-homes the selection, but the painter must not depend on
    // that having happened — it is called from the render loop.
    const { ctx } = stub2d();
    expect(() => paintMenu(ctx, REAL, 99, null, null)).not.toThrow();
    expect(() => paintMenu(ctx, REAL, -1, null, null)).not.toThrow();
  });
});
