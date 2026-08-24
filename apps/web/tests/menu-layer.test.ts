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
  alertFor,
  firstSelectable,
  nextSelectable,
  paintMenu,
  readinessFor,
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

  /**
   * Strikethroughs, counted as a DELTA against an all-verified baseline.
   *
   * Counting raw `stroke()` calls was a proxy, and it broke the moment the
   * panel grew a footer divider — a layout change silently invalidating an
   * assertion about the safety gate is exactly the wrong sensitivity to have.
   * The baseline absorbs whatever chrome the panel draws.
   */
  function strikeCount(items: readonly MenuItem[]): number {
    const strokesFor = (list: readonly MenuItem[]) => {
      const { ctx, calls } = stub2d();
      paintMenu(ctx, list, 0, null, null);
      return calls.filter((c) => c.fn === "stroke").length;
    };
    const baseline = strokesFor(items.map((i) => ({ ...i, verified: true })));
    return strokesFor(items) - baseline;
  }

  test("an unverified item is struck through and labelled", () => {
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null);
    // `dance` is the only unverified entry, so exactly one strikethrough.
    expect(strikeCount(REAL)).toBe(1);
    expect(texts()).toContain("sin probar en real");
  });

  test("an all-verified list draws no strikethrough at all", () => {
    const { ctx, texts } = stub2d();
    const allOk = REAL.map((i) => ({ ...i, verified: true }));
    paintMenu(ctx, allOk, 0, null, null);
    expect(strikeCount(allOk)).toBe(0);
    expect(texts()).not.toContain("sin probar en real");
  });

  test("every item is struck through when the catalogue could not be read", () => {
    // +page.svelte maps `catalogueFailed` to all-unverified. This is what the
    // operator then sees: six inert entries, not six confident ones.
    const none = REAL.map((i) => ({ ...i, verified: false }));
    expect(strikeCount(none)).toBe(none.length);
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

describe("readinessFor — why nothing is happening", () => {
  /**
   * Reconstructed from the session of 2026-08-21. The operator reported three
   * separate failures — gestures refused, walk buttons dead, no head turning —
   * which were one fact the headset never showed them: the robot was limp in
   * zero_torque. Each case below is a state that produced silence, and the
   * sentence that should have been on the panel instead.
   */

  test("zero_torque names the bring-up ladder, not the FSM id", () => {
    const r = readinessFor("zero_torque", true, []);
    expect(r.ok).toBe(false);
    // "FSM 0" is true and useless with a headset on.
    expect(r.text).toContain("damp");
    expect(r.text).toContain("501");
  });

  test("damp reads the same as zero_torque", () => {
    expect(readinessFor("damp", true, []).ok).toBe(false);
  });

  test("a stripped motion controller names the script that fixes it", () => {
    // posture goes 'unknown' when the FSM getters answer nothing, which is
    // exactly what a colleague's xr_teleoperate leaves behind. Happened twice
    // in one day; both times every command returned rpc_code 0 and did nothing.
    for (const p of [null, undefined, "unknown", "no_data_yet"]) {
      const r = readinessFor(p, true, []);
      expect(r.ok).toBe(false);
      expect(r.text).toContain("select_motion_mode");
    }
  });

  test("preparation says gestures work even though walking does not", () => {
    // FSM 4 permits arms but not walking. Reporting a flat "not ready" here
    // would be wrong in the direction that wastes the operator's time.
    const r = readinessFor("preparation", true, []);
    expect(r.ok).toBe(false);
    expect(r.text).toContain("Gestos");
    expect(r.text).toContain("501");
  });

  test("the walk programs are the only ready states", () => {
    for (const p of ["walk", "walk_waist", "run"]) {
      expect(readinessFor(p, true, []).ok).toBe(true);
    }
  });

  test("an unrecognised posture is not ready, and says what it is", () => {
    const r = readinessFor("squat", true, []);
    expect(r.ok).toBe(false);
    expect(r.text).toContain("squat");
  });

  test("low battery outranks everything, including a ready posture", () => {
    // The robot died mid-session at 14% while discharging at 2.3 A. This is
    // the one state that gets worse while the operator reads the message, and
    // the only one whose remedy is not a command.
    const r = readinessFor("walk_waist", true, ["low_battery_14pct"]);
    expect(r.ok).toBe(false);
    expect(r.text).toContain("14");
    expect(r.text).toContain("cargar");
  });

  test("offline outranks posture but not battery", () => {
    expect(readinessFor("walk", false, []).ok).toBe(false);
    expect(readinessFor("walk", false, []).text).toContain("conexión");
    // A stale posture cached from before the link dropped must not read ready.
    expect(readinessFor("walk", false, ["low_battery_9pct"]).text).toContain(
      "9",
    );
  });

  test("null faults are survived", () => {
    expect(() => readinessFor("walk", true, null)).not.toThrow();
    expect(() => readinessFor("walk", true, undefined)).not.toThrow();
    expect(readinessFor("walk", true, null).ok).toBe(true);
  });

  test("the banner is drawn even when everything is fine", () => {
    // An operator who only ever sees this line when something is wrong has no
    // reason to trust its absence.
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null, readinessFor("walk_waist", true, []));
    expect(texts()).toContain("Listo");
  });

  test("the banner shows the not-ready reason on the panel", () => {
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null, readinessFor("zero_torque", true, []));
    expect(texts().some((t) => t.includes("damp"))).toBe(true);
  });
});

describe("alertFor — the latch that stopped you, and how to clear it", () => {
  /**
   * From 2026-08-24: seven `teleop.deadman.tripped held_s=8.0` in one session,
   * experienced as "the walking is a bit buggy". The 8 s continuous-motion
   * limit was written when walking meant TAPPING a DOM button, which never
   * reached it. A held thumbstick reaches it every time.
   */

  test("nothing latched is no alert at all", () => {
    expect(alertFor(false, false)).toBe(null);
  });

  test("the hold latch says to release and push again", () => {
    const a = alertFor(true, false)!;
    expect(a.text).toContain("8");
    expect(a.hint).toContain("soltá");
    expect(a.hint).toContain("volvé");
  });

  test("an e-stop OUTRANKS the hold latch", () => {
    // Their release gestures are opposites: you clear a hold latch by pushing
    // AGAIN, and an e-stop by letting go and waiting. Showing the hold hint
    // during an e-stop would have the operator pushing into a stop.
    const both = alertFor(true, true)!;
    expect(both.text).toContain("PARADA");
    expect(both.hint).not.toContain("volvé a empujar");
    expect(both.hint).toContain("esperá");
  });

  test("an e-stop alone reads as an e-stop", () => {
    const a = alertFor(false, true)!;
    expect(a.text).toContain("PARADA");
  });

  test("the alert band is drawn on the panel when latched", () => {
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null, null, alertFor(true, false));
    expect(texts().some((t) => t.includes("8"))).toBe(true);
    expect(texts().some((t) => t.includes("soltá"))).toBe(true);
  });

  test("no alert band when nothing is latched", () => {
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null, null, null);
    expect(texts().some((t) => t.includes("soltá"))).toBe(false);
  });

  test("the list still renders with the band pushing it down", () => {
    // The band steals 64px. Every gesture must still be drawn, or a latched
    // robot also loses its menu.
    const { ctx, texts } = stub2d();
    paintMenu(ctx, REAL, 0, null, null, null, alertFor(true, false));
    for (const it of REAL) {
      expect(texts().some((t) => t.startsWith(it.label))).toBe(true);
    }
  });
});

describe("readinessFor — a bridge that was never connected", () => {
  /**
   * The state that stopped the 2026-08-24 session and that nothing reported.
   * `list_active_tasks` said `active_count: 0`: no teleop session had ever been
   * registered, so head yaw and the thumbstick had nowhere to go. The alert
   * band stayed blank, correctly — `alertFor` reports LATCHES, and "there is no
   * socket" is the absence of the thing a latch lives in, not a latch.
   */

  test("an unconnected bridge is reported even with a perfect posture", () => {
    const r = readinessFor("walk_waist", true, [], false);
    expect(r.ok).toBe(false);
    expect(r.text).toContain("Puente");
  });

  test("it outranks posture, so a ready robot never reads Listo without a socket", () => {
    // The single most misleading thing this banner could say.
    for (const p of ["walk", "walk_waist", "run"]) {
      expect(readinessFor(p, true, [], false).ok).toBe(false);
    }
  });

  test("but battery and link still outrank it", () => {
    // Ordering matters: a flat battery is not fixed by connecting a socket.
    expect(
      readinessFor("walk", true, ["low_battery_9pct"], false).text,
    ).toContain("9");
    expect(readinessFor("walk", false, [], false).text).toContain("conexión");
  });

  test("connected plus a walk program is the only Listo", () => {
    expect(readinessFor("walk_waist", true, [], true).ok).toBe(true);
  });

  test("the parameter defaults true, so existing callers are unchanged", () => {
    // A page that cannot tell must not claim the bridge is down.
    expect(readinessFor("walk_waist", true, []).ok).toBe(true);
  });

  test("posture problems still show when the bridge IS connected", () => {
    const r = readinessFor("zero_torque", true, [], true);
    expect(r.text).toContain("damp");
  });
});
