/**
 * The status bar on top of the camera picture.
 *
 * WHY IT IS ITS OWN LAYER
 * -----------------------
 * Readiness ("Robot blando — damp → prepare → 501"), the latch alerts ("LÍMITE
 * DE 8 s — soltá y volvé a empujar") and the result of the last action all used
 * to live inside the gestures panel. That panel sat over the top half of the
 * picture all the time, because those three things had to be always visible.
 *
 * Option A (2026-11-23) folds the panel away until A is pressed, so the things
 * that must ALWAYS be visible needed somewhere else to be: a thin bar docked to
 * the picture's top edge, outside it, inside the readable cone. The panel keeps
 * only what it is for — the list.
 *
 * THE ALERT TAKES THE WHOLE BAR
 * -----------------------------
 * When a latch is stopping motion the bar turns red and says only that and
 * what to do. Readiness and the last outcome are true and irrelevant at that
 * moment; the one thing the operator needs is the release gesture, and a bar
 * that also showed "Listo" in green would contradict it.
 *
 * Drawn the same way as the menu and the radar: a 2D canvas uploaded as a
 * texture only when something changed, one clip-space quad. A deliberate copy
 * of their GL boilerplate, for the reason scan-layer.ts gives.
 */

import { fovAspect, placeQuad, type EyePose } from "./stereo";
import { STATUS_H as H, STATUS_W as W, statusPlacement } from "./hud-layout";
import type { MenuAlert, MenuStatus, Readiness } from "./menu-layer";

const FONT = "system-ui, -apple-system, sans-serif";

/**
 * Paint the bar. Pure-ish and exported, like `paintMenu`: what matters — that
 * an alert REPLACES the rest rather than joining it — is a decision, and it is
 * testable without a GL context.
 */
export function paintStatus(
  ctx: CanvasRenderingContext2D,
  readiness: Readiness | null,
  alert: MenuAlert,
  status: MenuStatus,
  hint: string,
): void {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = alert ? "rgba(120, 20, 20, 0.94)" : "rgba(10, 12, 16, 0.88)";
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = alert
    ? "rgba(255, 130, 130, 0.90)"
    : "rgba(255, 255, 255, 0.18)";
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, W - 2, H - 2);
  ctx.textBaseline = "middle";
  const y = H / 2;

  if (alert) {
    ctx.font = `800 26px ${FONT}`;
    ctx.fillStyle = "rgba(255, 228, 228, 1)";
    ctx.fillText(alert.text, 22, y);
    const w = ctx.measureText(alert.text).width;
    ctx.font = `500 22px ${FONT}`;
    ctx.fillStyle = "rgba(255, 205, 205, 0.96)";
    ctx.fillText(`— ${alert.hint}`, 22 + w + 14, y);
    return;
  }

  let x = 12;
  if (readiness) {
    ctx.font = `700 21px ${FONT}`;
    const pw = ctx.measureText(readiness.text).width + 28;
    ctx.fillStyle = readiness.ok
      ? "rgba(80, 200, 130, 0.30)"
      : "rgba(255, 150, 90, 0.30)";
    ctx.fillRect(x, 12, pw, H - 24);
    ctx.fillStyle = readiness.ok
      ? "rgba(170, 250, 205, 1)"
      : "rgba(255, 210, 165, 1)";
    ctx.fillText(readiness.text, x + 14, y);
    x += pw + 18;
  }

  // The hint is measured first so the outcome can be cut to the space left
  // between them rather than running under it.
  ctx.font = `500 18px ${FONT}`;
  const hw = hint ? ctx.measureText(hint).width : 0;
  if (hint) {
    ctx.fillStyle = "rgba(255, 255, 255, 0.58)";
    ctx.fillText(hint, W - hw - 20, y);
  }

  if (status) {
    ctx.font = `600 20px ${FONT}`;
    ctx.fillStyle =
      status.kind === "ok"
        ? "rgba(150, 240, 180, 0.99)"
        : status.kind === "warn"
          ? "rgba(250, 215, 130, 0.99)"
          : "rgba(255, 150, 150, 0.99)";
    const room = W - hw - 40 - x;
    let text = status.text;
    while (text.length > 1 && ctx.measureText(text).width > room) {
      text = text.slice(0, -2) + "…";
    }
    if (room > 0) ctx.fillText(text, x, y);
  }
}

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
uniform sampler2D u_tex;
varying vec2 v_uv;
void main() {
  gl_FragColor = texture2D(u_tex, v_uv);
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
    throw new Error(`status shader failed: ${log}`);
  }
  return sh;
}

export class StatusLayer {
  #gl: WebGLRenderingContext;
  #program: WebGLProgram | null = null;
  #buffer: WebGLBuffer | null = null;
  #texture: WebGLTexture | null = null;
  #posLoc = -1;
  #scaleLoc: WebGLUniformLocation | null = null;
  #offsetLoc: WebGLUniformLocation | null = null;
  #canvas: HTMLCanvasElement | null = null;
  #ctx: CanvasRenderingContext2D | null = null;
  #dirty = true;

  #readiness: Readiness | null = null;
  #alert: MenuAlert = null;
  #status: MenuStatus = null;
  #hint = "";

  constructor(gl: WebGLRenderingContext) {
    this.#gl = gl;
  }

  // Every setter is guarded: these are pushed from reactive effects and from
  // the frame loop, and an unchanged string must not cost a repaint + upload.

  setReadiness(readiness: Readiness | null): void {
    const prev = this.#readiness;
    if (prev?.text === readiness?.text && prev?.ok === readiness?.ok) return;
    this.#readiness = readiness;
    this.#dirty = true;
  }

  setAlert(alert: MenuAlert): void {
    const prev = this.#alert;
    if (prev?.text === alert?.text && prev?.hint === alert?.hint) return;
    this.#alert = alert;
    this.#dirty = true;
  }

  setStatus(status: MenuStatus): void {
    const prev = this.#status;
    if (prev?.text === status?.text && prev?.kind === status?.kind) return;
    this.#status = status;
    this.#dirty = true;
  }

  setHint(hint: string): void {
    if (hint === this.#hint) return;
    this.#hint = hint;
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
      throw new Error(`status link failed: ${gl.getProgramInfoLog(program)}`);
    }
    this.#program = program;
    this.#posLoc = gl.getAttribLocation(program, "a_pos");
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
    // Non-power-of-two: CLAMP + LINEAR or WebGL1 samples black.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  }

  /** On the picture's top edge — see `statusPlacement` in hud-layout.ts. */
  draw(eye?: EyePose | null): void {
    this.#ensureCanvas();
    const ctx = this.#ctx;
    const canvas = this.#canvas;
    if (!ctx || !canvas) return;
    if (!this.#program) this.#init();
    if (!this.#program) return;
    const gl = this.#gl;

    gl.bindTexture(gl.TEXTURE_2D, this.#texture);
    if (this.#dirty) {
      paintStatus(ctx, this.#readiness, this.#alert, this.#status, this.#hint);
      this.#dirty = false;
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        canvas,
      );
    }

    gl.useProgram(this.#program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.#buffer);
    gl.enableVertexAttribArray(this.#posLoc);
    gl.vertexAttribPointer(this.#posLoc, 2, gl.FLOAT, false, 0, 0);

    const p = placeQuad(eye, statusPlacement(fovAspect(eye)));
    gl.uniform2f(this.#scaleLoc, p.sx, p.sy);
    gl.uniform2f(this.#offsetLoc, p.ox, p.oy);

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
