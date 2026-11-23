/**
 * Where everything in the headset sits, in one place.
 *
 * WHY ONE FILE
 * ------------
 * The layout is now relational: the status bar sits on the camera frame's top
 * edge and the radar sits inside its bottom-left corner. Each layer used to
 * carry its own magic numbers, which was fine while they were independent and
 * is wrong now — move the camera and the bar would float in the black, or the
 * radar would hang off the picture's corner (which is exactly how it looked
 * before this file: half on the picture, half off it, lined up with nothing).
 *
 * THE LAYOUT ("option A", chosen 2026-11-23)
 * -----------------------------------------
 * - The camera picture, centred, as before (`FILL` in camera-layer.ts).
 * - A thin status bar just above the picture: readiness, the last outcome,
 *   and — when a latch is stopping motion — the alert, in red. Always shown.
 * - The radar, docked inside the picture's bottom-left corner.
 * - The gestures / voice panel, FOLDED until A is pressed. Nothing covers the
 *   picture while the operator is driving by it.
 *
 * All numbers are clip-space, in the units `placeQuad` reads: `ox`/`oy` the
 * centre, `sx` the half-width. Vertical extents are multiplied by `k`, the
 * eye's own width/height field ratio (`fovAspect`), because that is how
 * `placeQuad` turns a half-width into a half-height. On an ideal square eye
 * `k` is 1 and these are the literal numbers.
 */

import type { QuadSpec } from "./stereo";

/**
 * How much of the eye the picture's width fills. See camera-layer.ts for why
 * this is 0.72 and not 1 (reported from the first session that could see).
 */
export const FILL = 0.72;

/**
 * The picture's height over its width when nothing better is known.
 *
 * The D435i serves 640x480. The camera layer fits to the REAL frame size once
 * one has decoded; the bar and the radar use this, so a different sensor
 * shape moves them a little relative to the picture rather than breaking
 * anything. Stated, not guessed: the alternative is the panels re-laying-out
 * the first time a frame decodes, which in a headset reads as a jump.
 */
export const CAMERA_ASPECT = 480 / 640;

/** Gap between the picture and things docked to it, in clip units. */
export const DOCK_GAP = 0.02;

/** The status bar's canvas, a long thin strip. */
export const STATUS_W = 1000;
export const STATUS_H = 64;

/** The radar's half-width. Smaller than before (0.2): it now sits ON the
 * picture, so every bit of size costs view. */
export const SCAN_HALF = 0.16;

/** The gestures / voice panel's canvas (menu-layer.ts paints at this size). */
export const MENU_W = 640;
export const MENU_H = 430;

/**
 * The camera picture's half-extents for a frame of `aspect` (height/width) in
 * an eye of field ratio `k`. Letterboxed inside a FILL x FILL box, never
 * cropped — the same fit CameraLayer.draw has always done, moved here so the
 * docked panels can find the frame's edges.
 */
export function cameraFrame(
  k: number,
  aspect: number = CAMERA_ASPECT,
): { sx: number; sy: number } {
  const a = aspect > 0 && Number.isFinite(aspect) ? aspect : 1;
  const kk = k > 0 && Number.isFinite(k) ? k : 1;
  let sx = FILL;
  // Over the box means the picture is the taller shape: height binds, width
  // gives way.
  const impliedSy = FILL * kk * a;
  if (impliedSy > FILL) sx = FILL * (FILL / impliedSy);
  return { sx, sy: sx * kk * a };
}

/** The status bar: as wide as the picture, sitting on its top edge. */
export function statusPlacement(k: number): QuadSpec {
  const frame = cameraFrame(k);
  const aspect = STATUS_H / STATUS_W;
  const halfH = frame.sx * k * aspect;
  return { ox: 0, oy: frame.sy + DOCK_GAP + halfH, sx: frame.sx, aspect };
}

/**
 * The radar: inside the picture's bottom-left corner, inset by DOCK_GAP.
 *
 * Not further out than this. The original bottom-left placement (x ~ -0.96)
 * was invisible in the headset — vignetted periphery, 2026-08-27. This keeps
 * its outer edge on the picture's own edge, which the operator can see.
 */
export function scanPlacement(k: number): QuadSpec {
  const frame = cameraFrame(k);
  return {
    ox: -frame.sx + SCAN_HALF + DOCK_GAP,
    oy: -frame.sy + SCAN_HALF * k + DOCK_GAP,
    sx: SCAN_HALF,
    aspect: 1,
  };
}

/**
 * The opened gestures / voice panel: centred, a touch above the middle.
 *
 * Centred because a panel at the side of the field is unreadable (the
 * top-right placement, 2026-08-27). It now covers the picture only while it
 * is open, and the picture is dimmed behind it so the text wins.
 */
export const MENU_PLACEMENT: QuadSpec = {
  ox: 0,
  oy: 0.06,
  sx: 0.44,
  aspect: MENU_H / MENU_W,
};

/**
 * How long an opened menu stays open with nothing pressed. It is a list to
 * pick from, not a place to live: left open it is the old layout again.
 */
export const MENU_IDLE_CLOSE_MS = 10_000;
