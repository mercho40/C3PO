/**
 * Draws the robot's camera into the XR view, so VR is a picture and not a void.
 *
 * The first headset session showed black. The immediate cause was that
 * `dom-overlay` does not composite under `immersive-vr` (fixed separately by
 * preferring `immersive-ar`), but the deeper one is this: nothing was ever
 * drawn. The session bound a WebGL layer and cleared it transparent every
 * frame, because head-yaw teleop needed a layer to exist, not to show
 * anything. Wearing that is a void with controls you cannot see.
 *
 * What an operator expects on entering VR is the robot's view. This module is
 * that: one full-field textured quad, refreshed from the MJPEG stream
 * `apps/perception`'s vision container already serves.
 *
 * WHY AN <img> AND NOT fetch/WebCodecs
 * -----------------------------------
 * `multipart/x-mixed-replace` is a format browsers decode natively in an
 * `<img>` and essentially nowhere else. The element keeps showing the newest
 * frame with no JavaScript involved at all; we only ever ask WebGL to upload
 * whatever it is currently displaying. Decoding it by hand would mean
 * reimplementing multipart framing and JPEG decode to arrive at the same
 * pixels, slower.
 *
 * The cost is that an <img> gives no "new frame" event — so `draw()` uploads on
 * every XR frame regardless. At 72-120 Hz against a 5 Hz stream that is mostly
 * redundant work, and cheap enough not to matter.
 *
 * THIS CLASS DOES NOT OWN LIVENESS, DELIBERATELY
 * ----------------------------------------------
 * The vision server CLOSES the stream after ~1 s without a frame — that is its
 * only in-band way to say "no longer live". An <img> answers by freezing on its
 * last frame forever, with no event. So a layer that merely attaches once shows
 * the operator a stale picture indefinitely and calls it a view, which in a
 * headset is worse than showing nothing.
 *
 * Rather than reimplement the recovery, this takes its URL and its liveness
 * from `$lib/robot/mjpeg-camera.ts`, which already polls `/status`, tracks
 * live/stale, and reconnects with a cache-busted URL (the browser will happily
 * reuse a dead MJPEG response, so a retry that does not change the src is a
 * retry that does nothing). One implementation of that logic, used by both the
 * on-page panel and the headset.
 *
 * TRANSPARENCY MATTERS UNDER PASSTHROUGH
 * -------------------------------------
 * In `immersive-ar` the compositor blends our layer over the room. Drawing an
 * opaque full-field quad there would hide the passthrough entirely — which is
 * the wrong trade when the operator is in the same room as the robot. So the
 * quad is drawn at reduced opacity in AR and fully opaque in VR: in AR it is a
 * heads-up view over the real world, in VR it is the world.
 */

const VERT = `
attribute vec2 a_pos;
uniform vec2 u_scale;
varying vec2 v_uv;
void main() {
  // Flip V: WebGL texture origin is bottom-left, image origin is top-left.
  // UVs come from the UNSCALED attribute, so the image still maps 0..1 across
  // the quad however the quad is scaled — the scale letterboxes, it does not
  // crop or zoom.
  v_uv = vec2((a_pos.x + 1.0) * 0.5, 1.0 - (a_pos.y + 1.0) * 0.5);
  gl_Position = vec4(a_pos * u_scale, 0.0, 1.0);
}`;

/**
 * How much of the eye the picture fills, after aspect fitting.
 *
 * 1.0 puts the frame edge-to-edge, which is what the first real session got
 * and what the operator asked to back off: a 4:3 image stretched across the
 * full field sits too close to focus on comfortably, and the eye has nowhere
 * to rest. Insetting it makes the frame something you look AT rather than
 * something pressed against your face — the same reason a cinema screen is not
 * the whole room.
 *
 * Not a distance: this is a clip-space scale, so it changes the ANGLE the
 * picture subtends, which is the thing that actually reads as "further away".
 * There is no depth here to move it in — the quad is drawn in clip space, not
 * positioned in the scene.
 *
 * 0.72 was chosen to leave a visible black margin on all sides at the Quest 3's
 * aspect without shrinking the frame to a postage stamp. Reported from the
 * first session that could see anything at all, 2026-08-21.
 */
export const FILL = 0.72;

const FRAG = `
precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_alpha;
void main() {
  gl_FragColor = vec4(texture2D(u_tex, v_uv).rgb, u_alpha);
}`;

function compile(
  gl: WebGLRenderingContext,
  type: number,
  src: string,
): WebGLShader {
  const sh = gl.createShader(type);
  if (!sh) throw new Error("no shader");
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    gl.deleteShader(sh);
    throw new Error(`shader compile failed: ${log}`);
  }
  return sh;
}

export class CameraLayer {
  #gl: WebGLRenderingContext;
  #program: WebGLProgram | null = null;
  #buffer: WebGLBuffer | null = null;
  #texture: WebGLTexture | null = null;
  #posLoc = -1;
  #alphaLoc: WebGLUniformLocation | null = null;
  #scaleLoc: WebGLUniformLocation | null = null;
  #img: HTMLImageElement | null = null;
  #ready = false;
  #uploaded = 0;
  #url = "";
  #live = true;

  constructor(gl: WebGLRenderingContext) {
    this.#gl = gl;
  }

  /** Whether a frame has ever been decoded — the page can say "no signal". */
  get hasFrame(): boolean {
    return this.#ready;
  }

  get framesUploaded(): number {
    return this.#uploaded;
  }

  /**
   * Point at an MJPEG endpoint, e.g. `http://localhost:8081/stream.mjpg?c=3`.
   *
   * Safe to call every time the source reconnects: an unchanged URL is
   * ignored, and a changed one swaps the element. Cutting the old `src` first
   * matters — that is what closes the previous HTTP request, and without it a
   * session that reconnects a few times ends up holding several open MJPEG
   * streams at once, each still costing bandwidth through the SSH tunnel.
   *
   * `crossOrigin = "anonymous"` is required: WebGL refuses to sample a texture
   * from an image the page cannot read back, and without it every upload
   * throws a security error rather than showing a picture. The vision server
   * sets `Access-Control-Allow-Origin: *` on every response including the
   * stream, so this works — verified against its source, not assumed.
   */
  setStreamUrl(streamUrl: string): void {
    if (streamUrl === this.#url) return;
    this.#url = streamUrl;
    if (this.#img) {
      // Detach BEFORE cutting src. The old element's handlers close over
      // `this`, so an error event dispatched after the swap would set
      // `#ready = false` on a layer now owned by the NEW image — and if that
      // one had already decoded, the picture goes away and the "no signal"
      // banner fires with a perfectly good stream running.
      this.#img.onload = null;
      this.#img.onerror = null;
      this.#img.src = "";
      this.#img.remove();
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => (this.#ready = true);
    img.onerror = () => (this.#ready = false);
    img.src = streamUrl;

    // ATTACHED TO THE DOCUMENT, DELIBERATELY, DESPITE NEVER BEING LOOKED AT.
    //
    // An MJPEG stream in an <img> advances through the same machinery as an
    // animated image, and Blink pauses that for elements with no rendering
    // observer. A detached image is exactly that: `onload` fires for the first
    // decoded part, so `#ready` and `hasFrame` go true and everything looks
    // healthy — and then the bitmap may simply stop updating. The texture is
    // re-uploaded every XR frame from a picture that never changes, which
    // reads in the headset as a live camera showing one frozen moment.
    //
    // 1x1 and transparent rather than `display: none`, because a display:none
    // element has no rendering observer either and reintroduces the very
    // problem. `aria-hidden` keeps it out of the accessibility tree, since it
    // carries no information for anyone reading the page.
    if (typeof document !== "undefined") {
      img.width = 1;
      img.height = 1;
      img.setAttribute("aria-hidden", "true");
      img.style.cssText =
        "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none;";
      document.body.appendChild(img);
    }
    this.#img = img;
  }

  /**
   * Whether the source still considers the feed live.
   *
   * A stale feed is still DRAWN — an operator mid-motion should not have the
   * world vanish — but dimmed, so "this picture is old" is visible from inside
   * the headset without reading any text.
   */
  setLive(live: boolean): void {
    this.#live = live;
  }

  #init(): void {
    const gl = this.#gl;
    const program = gl.createProgram();
    if (!program) throw new Error("no program");
    // Deleted after linking: the program keeps its own reference, so these
    // would otherwise stay resident for the life of the context — and each
    // VR entry creates a fresh context.
    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`link failed: ${gl.getProgramInfoLog(program)}`);
    }
    this.#program = program;
    this.#posLoc = gl.getAttribLocation(program, "a_pos");
    this.#alphaLoc = gl.getUniformLocation(program, "u_alpha");
    this.#scaleLoc = gl.getUniformLocation(program, "u_scale");

    this.#buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    // Two triangles covering clip space.
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );

    this.#texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    // CLAMP + LINEAR because MJPEG frames are almost never power-of-two, and
    // WebGL1 will silently refuse to sample a non-POT texture with mipmaps or
    // REPEAT — it renders black, which is the exact symptom we are fixing.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  }

  /**
   * Draw the newest frame into the current viewport, preserving aspect ratio.
   * Safe to call every frame.
   *
   * `vpWidth`/`vpHeight` are the eye viewport this is being drawn into, and
   * they are not optional in practice. The quad used to cover clip space
   * outright, which stretches the frame to whatever shape the eye viewport is:
   * the D435i serves 640x480 (4:3) and a Quest 3 eye viewport is nothing like
   * 4:3, so the picture arrived visibly distorted — and in VR, where this quad
   * IS the world, that distortion is the entire view. Reported from the first
   * real session, 2026-08-21.
   *
   * Letterboxed, never cropped: the operator is driving a robot by this
   * picture, and silently discarding the edges of their field of view is a
   * worse failure than black bars. Callers that cannot supply a viewport get
   * the old full-field behaviour, which keeps the non-XR paths working.
   */
  draw(opaque: boolean, vpWidth?: number, vpHeight?: number): void {
    const gl = this.#gl;
    const img = this.#img;
    if (!img || !this.#ready || img.naturalWidth === 0) return;
    if (!this.#program) this.#init();
    if (!this.#program) return;

    gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    try {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, img);
      this.#uploaded += 1;
    } catch {
      // A tainted or half-decoded image throws here. Skip the frame rather
      // than tear down the session — the next one is 200ms away.
      return;
    }

    gl.useProgram(this.#program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.enableVertexAttribArray(this.#posLoc);
    gl.vertexAttribPointer(this.#posLoc, 2, gl.FLOAT, false, 0, 0);
    // Dim a stale feed rather than hide it: losing the picture entirely
    // mid-motion is worse than seeing an obviously-old one.
    const alpha = (opaque ? 1.0 : 0.85) * (this.#live ? 1.0 : 0.45);
    gl.uniform1f(this.#alphaLoc, alpha);

    // Fit the frame inside the viewport without distorting it. Shrink one axis
    // — never grow, so the quad always stays within clip space.
    // Every input is checked for > 0 before dividing. A zero or missing
    // dimension would make this NaN, and a NaN vertex position does not draw a
    // wrong picture — it draws NOTHING, which is the black view this whole
    // module exists to stop. Falling back to the inset gives the old
    // full-field behaviour scaled down: distorted, but visible, and visible
    // wins.
    let sx = FILL;
    let sy = FILL;
    const iw = img.naturalWidth;
    const ih = img.naturalHeight;
    if (
      vpWidth &&
      vpHeight &&
      vpWidth > 0 &&
      vpHeight > 0 &&
      iw > 0 &&
      ih > 0
    ) {
      const imgAspect = iw / ih;
      const vpAspect = vpWidth / vpHeight;
      if (imgAspect > vpAspect) {
        sy = FILL * (vpAspect / imgAspect); // bars top and bottom
      } else {
        sx = FILL * (imgAspect / vpAspect); // bars left and right
      }
    }
    gl.uniform2f(this.#scaleLoc, sx, sy);
    gl.enable(gl.BLEND);
    // Separate, because the straight `blendFunc` applies SRC_ALPHA to the
    // alpha channel as well — so the framebuffer alpha came out as the square
    // of what was asked for. A stale feed meant to sit at 0.45 landed at 0.20
    // and was nearly invisible under passthrough, which is the opposite of
    // "dimmed rather than hidden": losing the view mid-motion is worse than an
    // obviously old one.
    gl.blendFuncSeparate(
      gl.SRC_ALPHA,
      gl.ONE_MINUS_SRC_ALPHA,
      gl.ONE,
      gl.ONE_MINUS_SRC_ALPHA,
    );
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    gl.disable(gl.BLEND);
  }

  dispose(): void {
    const gl = this.#gl;
    if (this.#img) {
      // Cutting the src is what actually stops an MJPEG stream: the request
      // stays open for as long as the element points at it, and a headset
      // session that ended should not still be pulling frames. Detach first,
      // for the same reason as in setStreamUrl.
      this.#img.onload = null;
      this.#img.onerror = null;
      this.#img.src = "";
      // Attached in setStreamUrl — see the note there. Leaving it in the
      // document would keep a 1x1 element per session for the tab's lifetime.
      this.#img.remove();
      this.#img = null;
    }
    this.#url = "";
    if (this.#texture) gl.deleteTexture(this.#texture);
    if (this.#buffer) gl.deleteBuffer(this.#buffer);
    if (this.#program) gl.deleteProgram(this.#program);
    // Null the handles too — leaving them pointing at deleted objects means a
    // later draw() would bind garbage instead of re-initialising.
    this.#texture = null;
    this.#buffer = null;
    this.#program = null;
    this.#ready = false;
  }
}
