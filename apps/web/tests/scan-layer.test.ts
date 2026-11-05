/**
 * Tests for the in-headset lidar radar.
 *
 * The subject here is ORIENTATION and ABSENCE, because those are the two ways
 * this panel can be confidently wrong.
 *
 * A radar drawn with the sign of one axis flipped looks entirely plausible —
 * dots, rings, a robot in the middle — and tells an operator wearing a
 * blindfold-with-a-camera that the wall on their left is on their right. And a
 * ring with no dots in it looks identical whether the room is empty, the lidar
 * is unplugged, or the payload arrived in a frame nobody can draw. Those are
 * the assertions below; layout is not.
 */

import { describe, expect, test } from "bun:test";
import {
  dotColor,
  frameIsDrawable,
  nearestCm,
  paintScan,
  parseRing,
  ringDots,
  type ScanRing,
} from "../src/lib/webxr/scan-layer";

const BUCKETS = 120;

/** A ring with a single return at `bearingDeg`, everything else clear. */
function oneAt(bearingDeg: number, cm: number): ScanRing {
  const step = 360 / BUCKETS;
  const a0 = -180;
  const i = Math.round(((bearingDeg - a0) % 360) / step) % BUCKETS;
  const r_cm: (number | null)[] = new Array(BUCKETS).fill(null);
  r_cm[i] = cm;
  return {
    r_cm,
    a0_deg: a0,
    step_deg: step,
    max_cm: 1200,
    frame: "base_footprint",
  };
}

/** A copy of `ring` with every range put through `f`. `r_cm` is readonly. */
function withRanges(
  ring: ScanRing,
  f: (v: number | null) => number | null,
): ScanRing {
  return { ...ring, r_cm: ring.r_cm.map(f) };
}

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
    closePath: rec("closePath"),
    arc: rec("arc"),
    fill: rec("fill"),
    stroke: rec("stroke"),
    measureText: rec("measureText", { width: 80 }),
    font: "",
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  };
  const texts = () =>
    calls.filter((c) => c.fn === "fillText").map((c) => String(c.args[0]));
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, texts };
}

describe("ringDots — which way is which", () => {
  test("straight ahead is straight up", () => {
    const [dot] = ringDots(oneAt(0, 300));
    expect(dot.x).toBeCloseTo(0, 5);
    expect(dot.y).toBeGreaterThan(0);
  });

  test("the robot's LEFT draws on the LEFT", () => {
    // REP-103: +yaw is counterclockwise, so +90 degrees is the robot's left.
    // On a heading-up radar that belongs at negative screen x. Getting this
    // backwards is the failure this whole file exists for: it looks correct
    // and it steers an operator into the thing they were avoiding.
    const [dot] = ringDots(oneAt(90, 300));
    expect(dot.x).toBeLessThan(0);
    expect(dot.y).toBeCloseTo(0, 5);
  });

  test("the robot's right draws on the right", () => {
    const [dot] = ringDots(oneAt(-90, 300));
    expect(dot.x).toBeGreaterThan(0);
    expect(dot.y).toBeCloseTo(0, 5);
  });

  test("behind draws below", () => {
    const [dot] = ringDots(oneAt(180, 300));
    expect(dot.y).toBeLessThan(0);
  });

  test("a bearing offset in a0_deg still lands in the right direction", () => {
    // The publisher sends whatever `angle_min` the scan had. A radar that
    // assumed -180 would rotate every obstacle by the difference, silently.
    const ring = oneAt(90, 300);
    const shifted: ScanRing = { ...ring, a0_deg: ring.a0_deg + 90 };
    const [dot] = ringDots(shifted);
    // Same index, bearing now 90 degrees further round: left becomes behind.
    expect(dot.y).toBeLessThan(0);
  });
});

describe("ringDots — distance", () => {
  test("nearer is nearer the centre, further is further out", () => {
    const near = ringDots(oneAt(0, 100))[0];
    const far = ringDots(oneAt(0, 800))[0];
    expect(Math.hypot(near.x, near.y)).toBeLessThan(Math.hypot(far.x, far.y));
  });

  test("a close obstacle is not squashed into the robot's own dot", () => {
    // The reason the radius is a square root. Linear over a 12 m ceiling puts
    // 0.6 m at 5% of the radius, which is under the robot marker — and 0.6 m
    // is precisely the obstacle worth drawing.
    const [dot] = ringDots(oneAt(0, 60));
    expect(Math.hypot(dot.x, dot.y)).toBeGreaterThan(0.15);
  });

  test("a return past the ceiling is pinned to the rim, not dropped", () => {
    const ring = oneAt(0, 5000); // 50 m, well past max_cm
    const [dot] = ringDots(ring);
    expect(Math.hypot(dot.x, dot.y)).toBeCloseTo(1, 5);
  });

  test("nothing is drawn beyond the unit disc", () => {
    const ring: ScanRing = {
      r_cm: new Array(BUCKETS).fill(0).map((_, i) => i * 40),
      a0_deg: -180,
      step_deg: 3,
      max_cm: 1200,
      frame: "base_footprint",
    };
    for (const d of ringDots(ring)) {
      expect(Math.hypot(d.x, d.y)).toBeLessThanOrEqual(1.0000001);
    }
  });
});

describe("ringDots — absence is not a dot", () => {
  test("null bearings produce no dot at all", () => {
    const ring = oneAt(0, 300);
    expect(ringDots(ring).length).toBe(1);
  });

  test("an all-clear ring draws nothing rather than a ring of zeroes", () => {
    const clear: ScanRing = {
      r_cm: new Array(BUCKETS).fill(null),
      a0_deg: -180,
      step_deg: 3,
      max_cm: 1200,
      frame: "base_footprint",
    };
    expect(ringDots(clear).length).toBe(0);
  });

  test("a zero or negative range is refused, not drawn touching the robot", () => {
    // 0 would render as an obstacle against the chassis — the most alarming
    // possible reading of the safest possible state.
    const ring = withRanges(oneAt(0, 300), (v) => (v === null ? null : 0));
    expect(ringDots(ring).length).toBe(0);
  });

  test("NaN and Infinity are refused", () => {
    const base = oneAt(0, 300);
    const nan = withRanges(base, (v) => (v === null ? null : Number.NaN));
    expect(ringDots(nan).length).toBe(0);
    const inf = withRanges(base, (v) =>
      v === null ? null : Number.POSITIVE_INFINITY,
    );
    expect(ringDots(inf).length).toBe(0);
  });
});

describe("dotColor — one glance, one bit", () => {
  test("arm's length is the alarming colour, the room is not", () => {
    expect(dotColor(50)).not.toBe(dotColor(500));
    expect(dotColor(50)).toBe(dotColor(80));
    expect(dotColor(500)).toBe(dotColor(1100));
  });

  test("the bands are monotonic — nothing far is redder than something near", () => {
    const bands = [dotColor(50), dotColor(150), dotColor(900)];
    expect(new Set(bands).size).toBe(3);
  });
});

describe("nearestCm", () => {
  test("finds the closest return, ignoring the empty bearings", () => {
    const ahead = oneAt(0, 300);
    const left = oneAt(90, 120);
    const both: ScanRing = {
      ...ahead,
      r_cm: ahead.r_cm.map((v, i) => v ?? left.r_cm[i]),
    };
    expect(nearestCm(both)).toBe(120);
  });

  test("an all-clear ring is null, not zero", () => {
    const clear: ScanRing = {
      r_cm: new Array(BUCKETS).fill(null),
      a0_deg: -180,
      step_deg: 3,
      max_cm: 1200,
    };
    expect(nearestCm(clear)).toBeNull();
  });
});

describe("frameIsDrawable — a ring in the wrong frame is refused", () => {
  test("base frames are drawn", () => {
    expect(frameIsDrawable("base_footprint")).toBe(true);
    expect(frameIsDrawable("base_link")).toBe(true);
  });

  test("an absent frame is trusted — older publishers sent none", () => {
    expect(frameIsDrawable("")).toBe(true);
    expect(frameIsDrawable(undefined)).toBe(true);
  });

  test("the sensor's own frame is NOT silently drawn as the robot's", () => {
    // It differs by the Mid-360's mounting yaw, and nothing in the numbers
    // says so. Drawing it anyway rotates the whole room around the operator
    // with nothing looking wrong.
    expect(frameIsDrawable("livox_frame")).toBe(false);
    expect(frameIsDrawable("map")).toBe(false);
  });
});

describe("paintScan — an empty dial always says why", () => {
  test("no ring at all is labelled, not left blank", () => {
    const { ctx, texts } = stub2d();
    paintScan(ctx, null, "el contenedor nav no está corriendo");
    expect(texts().some((t) => t.includes("SIN LIDAR"))).toBe(true);
    expect(texts().some((t) => t.includes("nav"))).toBe(true);
  });

  test("an undrawable frame is named rather than drawn", () => {
    const { ctx, texts, calls } = stub2d();
    const ring = { ...oneAt(0, 300), frame: "livox_frame" };
    paintScan(ctx, ring);
    expect(texts().some((t) => t.includes("MARCO DESCONOCIDO"))).toBe(true);
    expect(texts().some((t) => t.includes("livox_frame"))).toBe(true);
    // And no obstacle dots: the range rings use stroke(), the dots use fill()
    // after an arc(). One arc is the ring at 1 m; none of them is an obstacle.
    const arcsAfterDots = calls.filter((c) => c.fn === "arc").length;
    const clearRun = (() => {
      const s = stub2d();
      paintScan(s.ctx, { ...ring, frame: "base_footprint", r_cm: [] });
      return s.calls.filter((c) => c.fn === "arc").length;
    })();
    expect(arcsAfterDots).toBe(clearRun);
  });

  test("a stale ring is still drawn, and said to be stale", () => {
    const { ctx, texts } = stub2d();
    paintScan(ctx, { ...oneAt(0, 300), stale: true, age_s: 4.2 });
    expect(texts().some((t) => t.includes("DESACTUALIZADO"))).toBe(true);
  });

  test("a live ring reports the nearest thing in metres", () => {
    const { ctx, texts } = stub2d();
    paintScan(ctx, oneAt(0, 137));
    expect(texts().some((t) => t.includes("1.37"))).toBe(true);
  });

  test("an all-clear ring says 'nada cerca' rather than showing nothing", () => {
    const { ctx, texts } = stub2d();
    paintScan(ctx, {
      r_cm: new Array(BUCKETS).fill(null),
      a0_deg: -180,
      step_deg: 3,
      max_cm: 1200,
      frame: "base_footprint",
    });
    expect(texts().some((t) => t.includes("nada cerca"))).toBe(true);
  });

  test("the range rings are labelled, because the radius is not linear", () => {
    const { ctx, texts } = stub2d();
    paintScan(ctx, oneAt(0, 300));
    for (const m of [1, 2, 4, 8]) {
      expect(texts()).toContain(`${m}m`);
    }
  });
});

describe("parseRing — a payload that cannot be drawn is refused here", () => {
  const good = {
    v: 1,
    frame: "base_footprint",
    stamp_s: 1,
    a0_deg: -180,
    step_deg: 3,
    max_cm: 1200,
    r_cm: [null, 200],
    age_s: 0.1,
    stale: false,
  };

  test("a well-formed ring comes through with its fields intact", () => {
    const ring = parseRing(good);
    expect(ring).not.toBeNull();
    expect(ring!.max_cm).toBe(1200);
    expect(ring!.frame).toBe("base_footprint");
    expect(ring!.stale).toBe(false);
    expect(ring!.age_s).toBe(0.1);
  });

  test("a zero max_cm is refused rather than dividing every radius by it", () => {
    expect(parseRing({ ...good, max_cm: 0 })).toBeNull();
    expect(parseRing({ ...good, max_cm: -1 })).toBeNull();
  });

  test("a missing step_deg is refused rather than piling every bearing on one", () => {
    expect(parseRing({ ...good, step_deg: "3" })).toBeNull();
    expect(parseRing({ ...good, a0_deg: null })).toBeNull();
  });

  test("anything that is not a ring is null, not a partial object", () => {
    expect(parseRing(null)).toBeNull();
    expect(parseRing("no")).toBeNull();
    expect(parseRing({ v: 1 })).toBeNull();
    expect(parseRing({ ...good, r_cm: "120 bearings" })).toBeNull();
  });

  test("a stale flag survives — the layer needs it to dim the dial", () => {
    expect(parseRing({ ...good, stale: true })!.stale).toBe(true);
  });

  test("a missing frame becomes empty, which frameIsDrawable trusts", () => {
    const ring = parseRing({ ...good, frame: undefined })!;
    expect(ring.frame).toBe("");
    expect(frameIsDrawable(ring.frame)).toBe(true);
  });
});
