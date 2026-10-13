/**
 * World ↔ canvas geometry for the robot map, in one place and under test.
 *
 * The map and the dashboard's mini-map used to project metres to PERCENT of
 * width on x and percent of height on y, drawn with
 * `preserveAspectRatio="none"`. On any canvas that was not square that is two
 * different scales: a 45° walk drew at ~31°, the 24 x 24 m costmap rendered as
 * a rectangle, the grid cells were not square, and the "scale bar" was a fixed
 * 80 px labelled with half the canvas width (5x off on a wide screen). The
 * heading wedge was rotated with CSS `rotate(yaw)`, which turns CLOCKWISE,
 * against a REP-103 yaw that turns counter-clockwise — so a robot facing up
 * was drawn facing right.
 *
 * The rules now, all enforced here:
 *  - ONE scale, `ppm` (pixels per metre), for both axes. The view is described
 *    by a centre and a `span` — metres across the canvas's SHORTER side — so a
 *    zoom level means the same thing on a phone and a wall display.
 *  - World frame is REP-103: +x right, +y UP, yaw 0 = +x, positive = CCW.
 *    SVG is y-down, so world (x, y) is drawn at SVG (x, -y), and every
 *    rotation that SVG/CSS apply clockwise is negated.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

export type XY = { x: number; y: number };

/** The visible window: world centre + zoom, and the measured canvas size. */
export type View = {
  center: XY;
  /** Metres across the shorter side of the canvas. */
  span: number;
  /** Canvas size in CSS px. */
  width: number;
  height: number;
};

export const SPAN_MIN = 3;
export const SPAN_MAX = 60;

export function clampSpan(span: number): number {
  return Math.min(SPAN_MAX, Math.max(SPAN_MIN, span));
}

/** Pixels per metre. Falls back to a nominal 300 px canvas before measuring. */
export function pxPerMeter(view: View): number {
  const side = Math.min(view.width, view.height);
  return (side > 0 ? side : 300) / view.span;
}

/** World metres → SVG user units (still metres, y flipped). */
export function toSvg(x: number, y: number): XY {
  return { x, y: -y };
}

/** The SVG viewBox, in metres, that shows exactly `view` at uniform scale. */
export function viewBoxOf(view: View): {
  minX: number;
  minY: number;
  width: number;
  height: number;
} {
  const ppm = pxPerMeter(view);
  const w = (view.width > 0 ? view.width : 300) / ppm;
  const h = (view.height > 0 ? view.height : 300) / ppm;
  return {
    minX: view.center.x - w / 2,
    minY: -view.center.y - h / 2,
    width: w,
    height: h,
  };
}

/** World metres → CSS px inside the canvas (origin top-left). */
export function worldToScreen(
  view: View,
  p: XY,
): { left: number; top: number } {
  const ppm = pxPerMeter(view);
  return {
    left: view.width / 2 + (p.x - view.center.x) * ppm,
    top: view.height / 2 - (p.y - view.center.y) * ppm,
  };
}

/** CSS px inside the canvas → world metres. Inverse of `worldToScreen`. */
export function screenToWorld(view: View, left: number, top: number): XY {
  const ppm = pxPerMeter(view);
  return {
    x: view.center.x + (left - view.width / 2) / ppm,
    y: view.center.y - (top - view.height / 2) / ppm,
  };
}

const DEG = 180 / Math.PI;

/**
 * SVG `rotate()` for a shape drawn pointing along +x (right).
 * SVG rotates clockwise on a y-down canvas; REP-103 yaw is counter-clockwise
 * on a y-up world. So the sign flips: yaw +90° (facing +y, up) → rotate(-90).
 */
export function headingSvgRotation(yawRad: number): number {
  return -yawRad * DEG;
}

/**
 * CSS `rotate()` for an icon drawn pointing UP (e.g. Lucide Navigation2).
 * Up is already +90° in REP-103, and CSS turns clockwise: 90 − yaw.
 */
export function compassCssRotation(yawRad: number): number {
  return 90 - yawRad * DEG;
}

/** Unit vector of the heading in SCREEN space (y down). */
export function headingScreenVector(yawRad: number): {
  dx: number;
  dy: number;
} {
  return { dx: Math.cos(yawRad), dy: -Math.sin(yawRad) };
}

/**
 * Where to draw an off-view point: on the edge of the canvas (inset by
 * `insetPx`), along the ray from the view centre, pointing at it.
 *
 * Returns null when the point is inside the inset frame. The old map clamped
 * the marker to 4-96 % instead, which pinned the robot to the edge on top of
 * map cells it was not on, with nothing saying it was out of view.
 */
export function edgeMarker(
  view: View,
  p: XY,
  insetPx = 24,
): { left: number; top: number; angleDeg: number; distanceM: number } | null {
  const s = worldToScreen(view, p);
  const halfW = Math.max(1, view.width / 2 - insetPx);
  const halfH = Math.max(1, view.height / 2 - insetPx);
  const dx = s.left - view.width / 2;
  const dy = s.top - view.height / 2;
  if (Math.abs(dx) <= halfW && Math.abs(dy) <= halfH) return null;
  const t = Math.min(
    dx === 0 ? Infinity : halfW / Math.abs(dx),
    dy === 0 ? Infinity : halfH / Math.abs(dy),
  );
  return {
    left: view.width / 2 + dx * t,
    top: view.height / 2 + dy * t,
    // Screen angle for an arrow drawn pointing right (CSS rotates clockwise,
    // and screen y is already down, so no flip here).
    angleDeg: Math.atan2(dy, dx) * DEG,
    distanceM: Math.hypot(p.x - view.center.x, p.y - view.center.y),
  };
}

const NICE = [0.5, 1, 2, 5, 10, 20, 50];

/**
 * A scale bar with a round length: the longest of 0.5/1/2/5/10/20/50 m that
 * fits in `maxPx`, and its measured width in px.
 */
export function niceScale(
  ppm: number,
  maxPx = 120,
): { meters: number; px: number } {
  let meters = NICE[0];
  for (const m of NICE) if (m * ppm <= maxPx) meters = m;
  return { meters, px: meters * ppm };
}

/** Grid spacing in metres: the smallest round step at least `minPx` apart. */
export function gridStep(ppm: number, minPx = 28): number {
  for (const m of NICE) if (m * ppm >= minPx) return m;
  return NICE[NICE.length - 1];
}

/** Grid line positions (world metres) covering the view, multiples of `step`. */
export function gridLines(
  view: View,
  step: number,
): { xs: number[]; ys: number[] } {
  const vb = viewBoxOf(view);
  const xs: number[] = [];
  const ys: number[] = [];
  // Bounded, so a degenerate span can never spin this.
  const maxLines = 400;
  for (
    let x = Math.ceil(vb.minX / step) * step;
    x <= vb.minX + vb.width && xs.length < maxLines;
    x += step
  ) {
    xs.push(round(x));
  }
  const yTop = -vb.minY;
  const yBottom = -(vb.minY + vb.height);
  for (
    let y = Math.ceil(yBottom / step) * step;
    y <= yTop && ys.length < maxLines;
    y += step
  ) {
    ys.push(round(y));
  }
  return { xs, ys };
}

function round(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}

/**
 * A costmap's rect in SVG units. `origin` is the grid's BOTTOM-LEFT corner in
 * the world, and PNG row 0 is its TOP edge (the encoder emits rows top-down),
 * so the image's SVG y is −(originY + height). No clamping: a rect with
 * extent must keep its size or it stops lining up with the marker.
 */
export function costmapSvgRect(p: {
  originXM: number;
  originYM: number;
  widthM: number;
  heightM: number;
}): { x: number; y: number; width: number; height: number } {
  return {
    x: p.originXM,
    y: -(p.originYM + p.heightM),
    width: p.widthM,
    height: p.heightM,
  };
}

/** Drag by (dx, dy) px: the world follows the pointer. */
export function panCenter(
  center: XY,
  dxPx: number,
  dyPx: number,
  ppm: number,
): XY {
  return { x: center.x - dxPx / ppm, y: center.y + dyPx / ppm };
}

/**
 * Zoom by `spanFactor` (>1 zooms out), keeping the world point under
 * `anchor` (canvas px) fixed — so a wheel or pinch zooms where the operator
 * is pointing. Without an anchor, zooms about the view centre.
 */
export function zoomAt(
  view: View,
  spanFactor: number,
  anchor?: { left: number; top: number },
): { center: XY; span: number } {
  const span = clampSpan(view.span * spanFactor);
  if (!anchor) return { center: view.center, span };
  const a = screenToWorld(view, anchor.left, anchor.top);
  const next: View = { ...view, span };
  const ppm = pxPerMeter(next);
  return {
    center: {
      x: a.x - (anchor.left - view.width / 2) / ppm,
      y: a.y + (anchor.top - view.height / 2) / ppm,
    },
    span,
  };
}

/**
 * The smallest view that holds every point plus a margin — the mini-map's
 * framing, so the whole session's path is on screen. `span` is clamped, so a
 * very long walk can still leave part of the path outside.
 */
export function fitView(
  points: XY[],
  width: number,
  height: number,
  opts: { minSpan?: number; marginM?: number } = {},
): { center: XY; span: number } {
  const minSpan = opts.minSpan ?? 6;
  const margin = opts.marginM ?? 1.5;
  if (points.length === 0) return { center: { x: 0, y: 0 }, span: minSpan };
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  const w = width > 0 ? width : 1;
  const h = height > 0 ? height : 1;
  const side = Math.min(w, h);
  // Span is measured on the SHORT side; scale each extent onto it.
  const need = Math.max(
    ((maxX - minX + 2 * margin) * side) / w,
    ((maxY - minY + 2 * margin) * side) / h,
  );
  return {
    center: { x: (minX + maxX) / 2, y: (minY + maxY) / 2 },
    span: clampSpan(Math.max(minSpan, need)),
  };
}
