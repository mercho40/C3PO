/**
 * Drawing into the WebXR layer: every overlay once per eye, each layer failing
 * on its own, and the DOM overlay root's background taken away for the life of
 * the session and given back after.
 *
 * Pure helpers, moved out of `xr-teleop.ts` intact; `xr-teleop.ts` re-exports
 * them.
 */

import { eyeOffset, type EyePose } from "./stereo";

/**
 * Draw the camera once per eye, into each eye's own viewport.
 *
 * ONE VIEWPORT PER EYE, AND THIS IS WHY THE CAMERA WAS INVISIBLE.
 *
 * The canvas backing the XR context is created detached and never sized, so
 * its drawing buffer is the HTML default 300x150 — and GL seeds the viewport
 * from the drawing buffer at context creation. Nothing in the app ever called
 * `gl.viewport`. The XR layer's framebuffer is nothing like that shape: on a
 * Quest 3 it is roughly 2064x2208 PER EYE, both eyes side by side in one
 * buffer.
 *
 * `gl.clear` is scissor-bounded, so the transparent clear covered everything
 * and the view looked correct-but-empty. `drawArrays` is VIEWPORT-bounded, so
 * a full clip-space quad landed in a 300x150 patch in the corner of one eye.
 * In passthrough that reads as "the room, with a smear"; in VR as "black, with
 * a postage stamp". Both were reported as "no camera", and both sent us
 * looking at the perception stack and the SSH tunnel.
 *
 * The per-view loop WebXR requires was also absent entirely, and the draw ran
 * BEFORE `getViewerPose` — so the views did not exist yet and it could not
 * have been correct even with the viewport set. Hence the argument order here:
 * a pose is required to get one at all.
 *
 * Returns the error if drawing threw, so the caller can drop the camera
 * without losing the head pose it is in the middle of sampling.
 */
export type XrLayer = {
  draw(eye?: EyePose | null): void;
};

/**
 * Which layers threw this frame. `null` per layer means it drew.
 *
 * ONE OBJECT INSTEAD OF ONE ERROR, because the layers must fail
 * independently. See `drawPerEye`.
 */
export type LayerFailures = {
  camera: unknown | null;
  menu: unknown | null;
  scan: unknown | null;
};

export function drawPerEye(
  gl: WebGLRenderingContext,
  layer: XRWebGLLayer,
  pose: XRViewerPose,
  /**
   * Null when no camera is configured at all. The panels still draw — see the
   * failure-isolation note below; a headset with no picture is exactly when
   * an operator most needs to be told why.
   */
  camera: {
    draw(opaque: boolean, eye?: EyePose | null): void;
  } | null,
  opaque: boolean,
  /**
   * Drawn after the camera, into the same viewport, so it sits over the
   * picture rather than under it. Optional: sessions without a menu pass
   * nothing and this is a no-op.
   */
  menu?: XrLayer | null,
  /**
   * The lidar radar, drawn in the opposite corner from the menu. Optional for
   * the same reason: a session without perception passes nothing.
   */
  scan?: XrLayer | null,
): LayerFailures {
  // EACH LAYER GETS ITS OWN try, AND THIS IS THE POINT.
  //
  // One try around all three meant a camera shader failure took the menu and
  // the radar with it — and the caller, which nulls `#camera` on a failure,
  // then stopped calling this at all because the whole block was gated on the
  // camera existing. So the single most likely GL failure silently removed
  // the readiness banner that says why the robot will not move and the radar
  // that says what is behind it, at the exact moment the operator lost the
  // picture and needed both. Three layers, three failures, three decisions.
  const failures: LayerFailures = { camera: null, menu: null, scan: null };

  const attempt = (
    key: keyof LayerFailures,
    draw: () => void,
    what: string,
  ): void => {
    // Already broken this frame: do not call it again for the second eye.
    if (failures[key]) return;
    try {
      draw();
    } catch (err) {
      failures[key] = err ?? new Error(`${what} draw failed`);
    }
  };

  // The head's own frame, needed to express each eye's position RELATIVE TO
  // THE HEAD rather than to the room. Read once per frame, not once per eye:
  // it is the same head both times.
  const viewerFromRef = pose.transform?.inverse?.matrix;

  for (const view of pose.views) {
    const vp = layer.getViewport(view);
    if (!vp) continue;
    gl.viewport(vp.x, vp.y, vp.width, vp.height);

    // WHICH EYE THIS IS, not just how big it is.
    //
    // The layers used to get `vp.width`/`vp.height` and nothing else, which is
    // enough to know the SHAPE of the render target and not enough to know
    // where in the world the eye looking at it is. So every layer drew itself
    // at the same clip-space coordinate in both eyes, and the operator saw two
    // of each on 2026-08-27. The projection matrix carries the frustum's
    // asymmetry and the eye transform carries the parallax; together they are
    // what turns two images into one object. See `stereo.ts`.
    //
    // Null rather than a guess when the runtime gives us no matrix: `placeQuad`
    // falls back to the old monoscopic placement, which does not fuse but does
    // draw, and a HUD that does not fuse beats a HUD that is not there.
    const projection = view.projectionMatrix;
    const position = view.transform?.position;
    const eye: EyePose | null =
      projection && viewerFromRef && position
        ? { projection, offset: eyeOffset(viewerFromRef, position) }
        : null;

    if (camera) {
      // The camera IS the view in VR; in passthrough it is a heads-up layer
      // over the room, so it does not paint over what the operator can see.
      attempt("camera", () => camera.draw(opaque, eye), "camera");
    }
    if (menu) attempt("menu", () => menu.draw(eye), "menu");
    if (scan) attempt("scan", () => scan.draw(eye), "scan");
  }
  return failures;
}

/** What an overlay root looked like before the session took it over. */
export type OverlayStyle = { background: string; display: string };

/**
 * Strip the overlay root's opaque background for the life of the session.
 *
 * THE DOM OVERLAY COMPOSITES ON TOP OF THE WEBGL LAYER. Everything the
 * renderer draws — the camera quad, and under passthrough the room itself — is
 * *behind* this element. The page styles it `bg-background`, which resolves to
 * `#06090f`: fully opaque, edge to edge, because the UA promotes the overlay
 * root to `position: fixed; inset: 0`.
 *
 * So the camera was being drawn correctly and then painted over. That is a
 * second, independent cause of the same "no camera" symptom the viewport bug
 * produced — and it is worse in `immersive-ar`, the mode this now prefers,
 * because AR is exactly where the overlay composites. Fixing the viewport
 * alone would have shown the operator the same black field and sent them
 * looking at the renderer a third time.
 *
 * Done in script rather than CSS because it has to be tied to the session
 * lifetime and undone afterwards — a console left with a transparent body
 * shows the browser's background, not the app's. The panels inside keep their
 * own backgrounds, so the controls stay readable against the room, which is
 * what a heads-up overlay should look like.
 *
 * Returns the previous inline values so they can be put back exactly.
 */
export function stripOverlayBackground(root: HTMLElement): OverlayStyle {
  const previous: OverlayStyle = {
    background: root.style.background,
    display: root.style.display,
  };
  root.style.background = "transparent";
  // The UA also forces `display: block` on `:xr-overlay`, which collapses the
  // root's flex column. Asking for flex back is harmless where the UA wins.
  root.style.display = "flex";
  return previous;
}

/** Undo `stripOverlayBackground`, restoring exactly what was there before. */
export function restoreOverlayBackground(
  root: HTMLElement,
  previous: OverlayStyle,
): void {
  root.style.background = previous.background;
  root.style.display = previous.display;
}
