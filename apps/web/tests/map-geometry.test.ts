/**
 * Map geometry. Pinned because the old map got every one of these wrong at
 * once: two scales for x and y, a heading wedge turned clockwise against a
 * counter-clockwise yaw, a compass offset 45°, and a scale bar labelled with
 * half the canvas width.
 */

import { describe, expect, test } from "bun:test";
import {
  compassCssRotation,
  costmapSvgRect,
  edgeMarker,
  fitView,
  gridLines,
  gridStep,
  headingScreenVector,
  headingSvgRotation,
  niceScale,
  panCenter,
  pxPerMeter,
  screenToWorld,
  viewBoxOf,
  worldToScreen,
  zoomAt,
  type View,
} from "../src/lib/robot/map-geometry";

const wide: View = {
  center: { x: 0, y: 0 },
  span: 10,
  width: 1000,
  height: 400,
};

describe("uniform scale", () => {
  test("one metre is the same number of pixels on both axes", () => {
    const o = worldToScreen(wide, { x: 0, y: 0 });
    const ex = worldToScreen(wide, { x: 1, y: 0 });
    const ey = worldToScreen(wide, { x: 0, y: 1 });
    expect(ex.left - o.left).toBeCloseTo(o.top - ey.top, 9);
    expect(ex.left - o.left).toBeCloseTo(pxPerMeter(wide), 9);
  });

  test("span is measured across the shorter side", () => {
    expect(pxPerMeter(wide)).toBeCloseTo(40, 9); // 400 px / 10 m
    const vb = viewBoxOf(wide);
    expect(vb.height).toBeCloseTo(10, 9);
    expect(vb.width).toBeCloseTo(25, 9); // same scale, wider canvas
  });

  test("+y is up on screen; the view centre is the canvas centre", () => {
    const view: View = { ...wide, center: { x: 3, y: -2 } };
    const c = worldToScreen(view, { x: 3, y: -2 });
    expect(c.left).toBeCloseTo(500, 9);
    expect(c.top).toBeCloseTo(200, 9);
    expect(worldToScreen(view, { x: 3, y: -1 }).top).toBeLessThan(200);
  });

  test("screenToWorld inverts worldToScreen", () => {
    const view: View = { ...wide, center: { x: 1.5, y: 2.25 }, span: 7 };
    const p = { x: -3.2, y: 4.7 };
    const s = worldToScreen(view, p);
    const back = screenToWorld(view, s.left, s.top);
    expect(back.x).toBeCloseTo(p.x, 9);
    expect(back.y).toBeCloseTo(p.y, 9);
  });
});

describe("heading (REP-103: yaw 0 = +x, positive = CCW)", () => {
  test("yaw 0 points right", () => {
    expect(headingSvgRotation(0) + 0).toBe(0);
    const v = headingScreenVector(0);
    expect(v.dx).toBeCloseTo(1, 9);
    expect(v.dy).toBeCloseTo(0, 9);
    // An up-pointing compass icon has to turn 90° clockwise to point right.
    expect(compassCssRotation(0)).toBeCloseTo(90, 9);
  });

  test("yaw +90° points UP on screen, not right", () => {
    const yaw = Math.PI / 2;
    // A wedge drawn toward +x, rotated by this in SVG (clockwise-positive on
    // a y-down canvas), ends up pointing up.
    expect(headingSvgRotation(yaw)).toBeCloseTo(-90, 9);
    const v = headingScreenVector(yaw);
    expect(v.dx).toBeCloseTo(0, 9);
    expect(v.dy).toBeCloseTo(-1, 9);
    expect(compassCssRotation(yaw)).toBeCloseTo(0, 9);
  });

  test("yaw −90° points down", () => {
    const yaw = -Math.PI / 2;
    expect(headingSvgRotation(yaw)).toBeCloseTo(90, 9);
    expect(headingScreenVector(yaw).dy).toBeCloseTo(1, 9);
    expect(compassCssRotation(yaw)).toBeCloseTo(180, 9);
  });
});

describe("scale bar and grid", () => {
  test("the bar's label is the length it is drawn at", () => {
    const { meters, px } = niceScale(48, 120);
    expect(meters).toBe(2);
    expect(px).toBeCloseTo(96, 9);
    expect(niceScale(300, 120).meters).toBe(0.5);
    expect(niceScale(5, 120).meters).toBe(20);
  });

  test("grid steps are round metres, at least minPx apart", () => {
    expect(gridStep(40, 28)).toBe(1);
    expect(gridStep(20, 28)).toBe(2);
    expect(gridStep(100, 28)).toBe(0.5);
  });

  test("grid lines are multiples of the step and cover the view", () => {
    const { xs, ys } = gridLines(wide, 1);
    expect(xs[0]).toBe(-12);
    expect(xs[xs.length - 1]).toBe(12);
    expect(ys).toContain(0);
    expect(ys[0]).toBe(-5);
    expect(ys[ys.length - 1]).toBe(5);
  });
});

describe("costmap placement", () => {
  test("origin is the bottom-left; the image's top edge is origin + height", () => {
    const r = costmapSvgRect({
      originXM: -12,
      originYM: -12,
      widthM: 24,
      heightM: 24,
    });
    expect(r).toEqual({ x: -12, y: -12, width: 24, height: 24 });
    const r2 = costmapSvgRect({
      originXM: 2,
      originYM: 1,
      widthM: 4,
      heightM: 3,
    });
    expect(r2.y).toBe(-4); // SVG y of the world's top edge (y = 4)
    expect(r2.width).toBe(4);
  });
});

describe("off-view robot", () => {
  test("inside the view, no edge marker", () => {
    expect(edgeMarker(wide, { x: 2, y: 1 })).toBeNull();
  });

  test("outside, it sits on the edge pointing at the robot, with its distance", () => {
    const m = edgeMarker(wide, { x: 30, y: 0 }, 20);
    expect(m).not.toBeNull();
    expect(m!.left).toBeCloseTo(980, 6);
    expect(m!.top).toBeCloseTo(200, 6);
    expect(m!.angleDeg).toBeCloseTo(0, 6);
    expect(m!.distanceM).toBeCloseTo(30, 9);

    const up = edgeMarker(wide, { x: 0, y: 30 }, 20);
    expect(up!.top).toBeCloseTo(20, 6);
    expect(up!.angleDeg).toBeCloseTo(-90, 6);
  });
});

describe("pan and zoom", () => {
  test("dragging right moves the view left (the world follows the pointer)", () => {
    const c = panCenter({ x: 0, y: 0 }, 40, 0, 40);
    expect(c.x).toBeCloseTo(-1, 9);
    const d = panCenter({ x: 0, y: 0 }, 0, 40, 40);
    expect(d.y).toBeCloseTo(1, 9);
  });

  test("zooming about an anchor keeps the point under it fixed", () => {
    const anchor = { left: 800, top: 100 };
    const before = screenToWorld(wide, anchor.left, anchor.top);
    const next = zoomAt(wide, 0.5, anchor);
    expect(next.span).toBeCloseTo(5, 9);
    const after = screenToWorld({ ...wide, ...next }, anchor.left, anchor.top);
    expect(after.x).toBeCloseTo(before.x, 9);
    expect(after.y).toBeCloseTo(before.y, 9);
  });

  test("zoom is clamped", () => {
    expect(zoomAt(wide, 100).span).toBe(60);
    expect(zoomAt(wide, 0.001).span).toBe(3);
  });
});

describe("fitView", () => {
  test("frames every point with a margin, at least minSpan", () => {
    const v = fitView(
      [
        { x: 0, y: 0 },
        { x: 10, y: 0 },
      ],
      400,
      400,
      {
        minSpan: 6,
        marginM: 1,
      },
    );
    expect(v.center.x).toBeCloseTo(5, 9);
    expect(v.span).toBeCloseTo(12, 9);
    expect(fitView([], 400, 400).span).toBe(6);
  });
});
