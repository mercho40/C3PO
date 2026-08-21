/**
 * The preset menu, drawn inside the headset.
 *
 * WHY THIS EXISTS AT ALL
 * ----------------------
 * `dom-overlay` does not composite under `immersive-vr` — that is why the
 * session prefers `immersive-ar`, and why plain VR has nothing to press. The
 * page's preset buttons are perfectly good HTML that the operator cannot see or
 * reach the moment they are actually immersed.
 *
 * WHY A CANVAS AND NOT WebGL TEXT
 * ------------------------------
 * Text in raw WebGL means a glyph atlas, kerning, and a layout pass — a few
 * hundred lines to render six Spanish labels. A 2D canvas already does all of
 * that correctly, including accents, and `texImage2D` accepts one directly. So
 * the menu is drawn with ordinary canvas calls and uploaded as a texture. It is
 * re-rendered only when something changes, not per frame: at 72-120 Hz a
 * canvas repaint plus upload would cost more than everything else here
 * combined, for a picture that changes when a thumb moves.
 *
 * SELECTION IS BY BUTTON, NOT BY POINTING
 * ---------------------------------------
 * No raycast, no cursor, no hit-testing. A press cycles, a trigger fires.
 * Pointing at a floating panel requires the operator to find it, aim at it, and
 * hold still — while wearing a headset, next to a robot they are driving. Two
 * buttons need none of that and cannot miss.
 *
 * WHAT IT REFUSES TO OFFER
 * ------------------------
 * Anything with `works.real === false` is drawn struck through and cannot be
 * selected. `dance` is the live example: accepted by the firmware, never once
 * observed to run on hardware, and labelled NEVER RUN ON HARDWARE in
 * `vr_smoke_test.py`. Putting it one press from an operator whose eyes are
 * covered is exactly the reasoning `works_real` exists to interrupt. Verified
 * skills appear; the rest are visible, explained, and inert.
 */

export type MenuItem = {
  /** Skill name, as the bridge knows it. */
  name: string;
  /** What the operator reads. */
  label: string;
  /** From the catalogue's `works.real`. False renders inert. */
  verified: boolean;
};

/** Status line under the list — the result of the last thing fired. */
export type MenuStatus = { text: string; kind: "ok" | "warn" | "error" } | null;

const W = 512;
const H = 320;

const VERT = `
attribute vec2 a_pos;
uniform vec2 u_scale;
uniform vec2 u_offset;
varying vec2 v_uv;
void main() {
  v_uv = vec2((a_pos.x + 1.0) * 0.5, 1.0 - (a_pos.y + 1.0) * 0.5);
  gl_Position = vec4(a_pos * u_scale + u_offset, 0.0, 1.0);
}`;

const FRAG = `
precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_alpha;
void main() {
  vec4 c = texture2D(u_tex, v_uv);
  gl_FragColor = vec4(c.rgb, c.a * u_alpha);
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
    throw new Error(`menu shader compile failed: ${log}`);
  }
  return sh;
}

/**
 * Paint the menu onto a 2D context. Exported and pure-ish so the layout is
 * testable without a GL context or a headset — the thing that matters here is
 * WHICH items are drawn as selectable, and that is a decision, not a pixel.
 */
export function paintMenu(
  ctx: CanvasRenderingContext2D,
  items: readonly MenuItem[],
  selected: number,
  status: MenuStatus,
  busy: string | null,
): void {
  ctx.clearRect(0, 0, W, H);

  // Dark, mostly-opaque panel. Under passthrough it sits over the room; in VR
  // it sits over the camera, and either way the text has to survive whatever
  // is behind it.
  ctx.fillStyle = "rgba(12, 14, 18, 0.86)";
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(255,255,255,0.18)";
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, W - 2, H - 2);

  ctx.textBaseline = "middle";
  ctx.font = "600 20px system-ui, -apple-system, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.62)";
  ctx.fillText("Gestos", 22, 30);

  ctx.font = "400 15px system-ui, -apple-system, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.38)";
  ctx.fillText("A · siguiente     gatillo · ejecutar", 22, 56);

  const top = 84;
  const rowH = 34;
  items.forEach((item, i) => {
    const y = top + i * rowH + rowH / 2;
    const isSel = i === selected;
    if (isSel) {
      ctx.fillStyle = item.verified
        ? "rgba(90, 170, 255, 0.28)"
        : "rgba(255, 120, 120, 0.20)";
      ctx.fillRect(12, top + i * rowH + 2, W - 24, rowH - 4);
    }
    ctx.font = "500 18px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = item.verified
      ? "rgba(255,255,255,0.95)"
      : "rgba(255,255,255,0.38)";
    const label = busy === item.name ? `${item.label} …` : item.label;
    ctx.fillText(label, 26, y);

    if (!item.verified) {
      // Struck through, not hidden. A capability the operator knows exists and
      // cannot find reads as a broken page; one they can see and cannot use
      // reads as a decision, which is what it is.
      const w = ctx.measureText(label).width;
      ctx.strokeStyle = "rgba(255,255,255,0.30)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(26, y);
      ctx.lineTo(26 + w, y);
      ctx.stroke();

      ctx.font = "400 13px system-ui, -apple-system, sans-serif";
      ctx.fillStyle = "rgba(255,150,150,0.75)";
      ctx.fillText("sin probar en real", W - 150, y);
    }
  });

  if (status) {
    ctx.font = "500 15px system-ui, -apple-system, sans-serif";
    ctx.fillStyle =
      status.kind === "ok"
        ? "rgba(130, 230, 160, 0.95)"
        : status.kind === "warn"
          ? "rgba(245, 205, 120, 0.95)"
          : "rgba(255, 140, 140, 0.95)";
    ctx.fillText(status.text.slice(0, 46), 22, H - 26);
  }
}

/**
 * Next selectable index, skipping unverified entries.
 *
 * Returns the current index unchanged when nothing is selectable, so a
 * catalogue where everything is unverified leaves the highlight where it is
 * instead of spinning. Pure, and tested — this is the gate.
 */
export function nextSelectable(
  items: readonly MenuItem[],
  from: number,
): number {
  if (items.length === 0) return 0;
  for (let step = 1; step <= items.length; step += 1) {
    const i = (from + step) % items.length;
    if (items[i].verified) return i;
  }
  return from;
}

/** First selectable index, or 0 when there is none. */
export function firstSelectable(items: readonly MenuItem[]): number {
  const i = items.findIndex((it) => it.verified);
  return i === -1 ? 0 : i;
}

export class MenuLayer {
  #gl: WebGLRenderingContext;
  #program: WebGLProgram | null = null;
  #buffer: WebGLBuffer | null = null;
  #texture: WebGLTexture | null = null;
  #posLoc = -1;
  #alphaLoc: WebGLUniformLocation | null = null;
  #scaleLoc: WebGLUniformLocation | null = null;
  #offsetLoc: WebGLUniformLocation | null = null;
  #canvas: HTMLCanvasElement | null = null;
  #ctx: CanvasRenderingContext2D | null = null;
  #dirty = true;
  #visible = false;

  #items: readonly MenuItem[] = [];
  #selected = 0;
  #status: MenuStatus = null;
  #busy: string | null = null;

  constructor(gl: WebGLRenderingContext) {
    this.#gl = gl;
  }

  get visible(): boolean {
    return this.#visible;
  }

  get selectedItem(): MenuItem | null {
    return this.#items[this.#selected] ?? null;
  }

  setVisible(v: boolean): void {
    if (v !== this.#visible) {
      this.#visible = v;
      this.#dirty = true;
    }
  }

  setItems(items: readonly MenuItem[]): void {
    this.#items = items;
    if (!items[this.#selected]?.verified) {
      this.#selected = firstSelectable(items);
    }
    this.#dirty = true;
  }

  setStatus(status: MenuStatus): void {
    this.#status = status;
    this.#dirty = true;
  }

  setBusy(name: string | null): void {
    this.#busy = name;
    this.#dirty = true;
  }

  /** Advance the highlight past any unverified entries. */
  advance(): void {
    this.#selected = nextSelectable(this.#items, this.#selected);
    this.#dirty = true;
  }

  #ensureCanvas(): void {
    if (this.#canvas || typeof document === "undefined") return;
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    this.#canvas = c;
    this.#ctx = c.getContext("2d");
  }

  #init(): void {
    const gl = this.#gl;
    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    const program = gl.createProgram();
    if (!program) throw new Error("no program");
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`menu link failed: ${gl.getProgramInfoLog(program)}`);
    }
    this.#program = program;
    this.#posLoc = gl.getAttribLocation(program, "a_pos");
    this.#alphaLoc = gl.getUniformLocation(program, "u_alpha");
    this.#scaleLoc = gl.getUniformLocation(program, "u_scale");
    this.#offsetLoc = gl.getUniformLocation(program, "u_offset");

    this.#buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );

    this.#texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    // Same non-power-of-two rules as the camera: CLAMP + LINEAR, or WebGL1
    // silently samples black.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  }

  /**
   * Draw the panel into the current viewport, low and centred so it does not
   * cover what the operator is driving by.
   */
  draw(vpWidth?: number, vpHeight?: number): void {
    if (!this.#visible) return;
    this.#ensureCanvas();
    const ctx = this.#ctx;
    const canvas = this.#canvas;
    if (!ctx || !canvas) return;
    if (!this.#program) this.#init();
    if (!this.#program) return;
    const gl = this.#gl;

    if (this.#dirty) {
      paintMenu(ctx, this.#items, this.#selected, this.#status, this.#busy);
      this.#dirty = false;
      gl.bindTexture(gl.TEXTURE_2D, this.#texture);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        canvas,
      );
    } else {
      gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    }

    gl.useProgram(this.#program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.enableVertexAttribArray(this.#posLoc);
    gl.vertexAttribPointer(this.#posLoc, 2, gl.FLOAT, false, 0, 0);

    // A third of the eye's width, bottom-centre, aspect-correct. Same lesson as
    // the camera quad: scaling to clip space without accounting for the
    // viewport's shape is what makes a rendered thing look wrong in a headset.
    //
    // On screen the panel measures `sx * vpWidth` by `sy * vpHeight`, and we
    // want that to be W:H. Solving for sy gives the line below. The fallback
    // assumes a square viewport, which is wrong but close — and wrong in
    // proportion rather than wrong by the inverse, which is what writing
    // `sx * (W / H)` here would do: a panel 2.5x too tall on a 1.6:1 canvas.
    const sx = 0.34;
    const vpAspect =
      vpWidth && vpHeight && vpWidth > 0 && vpHeight > 0
        ? vpWidth / vpHeight
        : 1;
    const sy = sx * (H / W) * vpAspect;
    gl.uniform2f(this.#scaleLoc, sx, sy);
    gl.uniform2f(this.#offsetLoc, 0, -1 + sy + 0.08);
    gl.uniform1f(this.#alphaLoc, 1.0);

    gl.enable(gl.BLEND);
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
    if (this.#texture) gl.deleteTexture(this.#texture);
    if (this.#buffer) gl.deleteBuffer(this.#buffer);
    if (this.#program) gl.deleteProgram(this.#program);
    this.#texture = null;
    this.#buffer = null;
    this.#program = null;
    this.#canvas = null;
    this.#ctx = null;
    this.#dirty = true;
  }
}
