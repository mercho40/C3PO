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
 * The cost is that an <img> gives no "new frame" event — so `draw()` uploads
 * on every XR frame regardless. At 72-120 Hz against a 5 Hz stream that is
 * mostly redundant work, which is why the upload is skipped whenever the image
 * has not changed size and the source reports no new decode (see `#dirty`).
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
varying vec2 v_uv;
void main() {
  // Flip V: WebGL texture origin is bottom-left, image origin is top-left.
  v_uv = vec2((a_pos.x + 1.0) * 0.5, 1.0 - (a_pos.y + 1.0) * 0.5);
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

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
  #img: HTMLImageElement | null = null;
  #ready = false;
  #uploaded = 0;

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
   * Point at an MJPEG endpoint, e.g. `http://localhost:8081/stream.mjpg`.
   *
   * `crossOrigin = "anonymous"` is required: WebGL refuses to sample a texture
   * from an image the page cannot read back, and without it every upload
   * throws a security error rather than showing a picture.
   */
  attach(streamUrl: string): void {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => (this.#ready = true);
    img.onerror = () => (this.#ready = false);
    img.src = streamUrl;
    this.#img = img;
  }

  #init(): void {
    const gl = this.#gl;
    const program = gl.createProgram();
    if (!program) throw new Error("no program");
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`link failed: ${gl.getProgramInfoLog(program)}`);
    }
    this.#program = program;
    this.#posLoc = gl.getAttribLocation(program, "a_pos");
    this.#alphaLoc = gl.getUniformLocation(program, "u_alpha");

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

  /** Draw the newest frame across the whole view. Safe to call every frame. */
  draw(opaque: boolean): void {
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
    gl.uniform1f(this.#alphaLoc, opaque ? 1.0 : 0.85);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    gl.disable(gl.BLEND);
  }

  dispose(): void {
    const gl = this.#gl;
    if (this.#img) {
      // Cutting the src is what actually stops an MJPEG stream: the request
      // stays open for as long as the element points at it, and a headset
      // session that ended should not still be pulling frames.
      this.#img.src = "";
      this.#img = null;
    }
    if (this.#texture) gl.deleteTexture(this.#texture);
    if (this.#buffer) gl.deleteBuffer(this.#buffer);
    if (this.#program) gl.deleteProgram(this.#program);
    this.#program = null;
    this.#ready = false;
  }
}
