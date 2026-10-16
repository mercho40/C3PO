/**
 * The lidar ring, drawn inside the headset.
 *
 * WHY THIS EXISTS
 * ---------------
 * The camera is a 69-degree window on a machine that can be walked into things
 * from any direction, and in surround mode everything outside that window is
 * black. An operator wearing the headset has no way to know there is a table
 * behind the robot's left hip. `/scan` has known all along — the Mid-360 sees
 * 360 degrees — and none of it reached the person driving.
 *
 * A TOP-DOWN RADAR, NOT A WORLD-LOCKED RING
 * -----------------------------------------
 * Every layer in this renderer is a clip-space quad; nothing here consumes a
 * view or projection matrix. Drawing dots pinned to the room would mean
 * introducing both, plus depth, plus a frame graph — a large change to the one
 * part of the stack that finally works in a headset. A heading-up radar in the
 * corner answers the same question ("what is around me, including behind")
 * with the machinery already proven by `menu-layer`.
 *
 * HEADING-UP, ROBOT-REFERENCED. Up is the robot's forward, left is the robot's
 * left. `scan_ring.encode` publishes bearings in whatever frame the scan came
 * in — `base_footprint` for `pointcloud_to_laserscan` — and the frame travels
 * with the payload precisely so this can refuse to draw one it does not
 * understand rather than rotating the world around the operator silently.
 *
 * SQUARE-ROOT RADIUS, WITH LABELLED RINGS
 * ---------------------------------------
 * Linear radius over a 12 m ceiling puts a table leg 0.6 m ahead at 5% of the
 * way out — indistinguishable from the robot's own dot, which is exactly the
 * obstacle that matters most. `sqrt` puts it at 22%. The distortion is real,
 * so it is made legible: range rings at 1, 2, 4 and 8 m are drawn and
 * labelled, and an operator reads distance off those rather than by eye.
 *
 * NEAR IS RED AND THAT IS THE WHOLE POINT. The dot ring is not a picture of
 * the room, it is a proximity warning that happens to have a shape.
 */

/** The payload from `/telemetry/scan`, as the bridge passes it through. */
import { placeQuad, type EyePose } from "./stereo";

export type ScanRing = {
  /** Centimetres per bearing; `null` means nothing was seen that way. */
  r_cm: readonly (number | null)[];
  /** Bearing of bucket 0, degrees, in `frame`. */
  a0_deg: number;
  /** Degrees between buckets. */
  step_deg: number;
  /** The ceiling `scan_ring` clipped to, in centimetres. */
  max_cm: number;
  /** The scan's own frame. Anything but a base frame is refused — see below. */
  frame?: string;
  /** Set by the bridge past SCAN_STALE_AFTER_S. */
  stale?: boolean;
  age_s?: number | null;
};

/** One dot, in a unit disc with +y = robot forward and +x = robot right. */
export type ScanDot = { x: number; y: number; cm: number };

const W = 320;
const H = 320;

/** Drawn and labelled, so the square-root radius stays readable. */
export const RANGE_RINGS_M = [1, 2, 4, 8] as const;

/**
 * Frames this radar knows how to draw heading-up.
 *
 * A scan in `livox_frame` differs from one in `base_footprint` by the sensor's
 * mounting yaw, and nothing in the numbers says which you have. Drawing the
 * second as the first rotates every obstacle around the operator by a fixed
 * angle with nothing looking wrong — the failure `scan_ring.encode` sends
 * `frame` along to prevent. So: known frames are drawn, an empty frame is
 * trusted (older publishers), and anything else is refused out loud.
 */
const BASE_FRAMES = ["base_footprint", "base_link", ""];

export function frameIsDrawable(frame: string | undefined | null): boolean {
  return BASE_FRAMES.includes(frame ?? "");
}

/**
 * Validate the shape before it reaches the renderer.
 *
 * Pure, and here rather than beside the fetch, so the wire contract is
 * tested without one. The
 * renderer treats `r_cm` as authoritative — a missing `max_cm` would divide
 * every radius by zero, and a missing `step_deg` would pile every bearing on
 * top of the first — so a payload that cannot be drawn is refused HERE, where
 * there is somewhere to put the reason.
 */
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function parseRing(body: unknown): ScanRing | null {
  if (typeof body !== "object" || body === null) return null;
  const b = body as Record<string, unknown>;
  if (!Array.isArray(b.r_cm)) return null;
  // `typeof`, not `Number()`. Coercion accepts `null`, `""` and `[]` as 0 —
  // so a payload with a missing `a0_deg` would be drawn as a ring pointing
  // straight ahead instead of being refused, which is the whole class of bug
  // this function exists to stop. JSON sends numbers; anything else here
  // means something upstream is wrong and should be said so.
  const a0 = num(b.a0_deg);
  const step = num(b.step_deg);
  const maxCm = num(b.max_cm);
  if (a0 === null || step === null) return null;
  if (maxCm === null || maxCm <= 0) return null;
  return {
    r_cm: b.r_cm as (number | null)[],
    a0_deg: a0,
    step_deg: step,
    max_cm: maxCm,
    frame: typeof b.frame === "string" ? b.frame : "",
    stale: b.stale === true,
    age_s: typeof b.age_s === "number" ? b.age_s : null,
  };
}

/**
 * Bearings to dots in a unit disc.
 *
 * `null` bearings produce NO dot — not a dot at the centre, not one at the
 * rim. Both encodings of "nothing there" are dangerous in opposite
 * directions and `scan_ring`'s docstring has the full argument.
 */
export function ringDots(ring: ScanRing): ScanDot[] {
  const out: ScanDot[] = [];
  const maxCm = ring.max_cm > 0 ? ring.max_cm : 1;
  for (let i = 0; i < ring.r_cm.length; i += 1) {
    const cm = ring.r_cm[i];
    if (cm === null || cm === undefined) continue;
    if (!Number.isFinite(cm) || cm <= 0) continue;
    const bearingDeg = ring.a0_deg + i * ring.step_deg;
    const th = (bearingDeg * Math.PI) / 180;
    // Clamped, not dropped: a return past the ceiling is still an obstacle,
    // and pinning it to the rim is honest about "at least this far".
    const r = Math.sqrt(Math.min(1, cm / maxCm));
    // REP-103: +x forward, +y left, yaw counterclockwise. On a heading-up
    // radar the robot's left belongs on the LEFT of the picture, which is
    // negative screen x — hence the minus.
    out.push({ x: -Math.sin(th) * r, y: Math.cos(th) * r, cm });
  }
  return out;
}

/**
 * Dot colour by distance. Three bands, not a gradient.
 *
 * A continuous ramp is prettier and unreadable at a glance through headset
 * optics. An operator glancing at this needs one bit — is anything close —
 * and bands give it to them without their having to compare shades.
 */
export function dotColor(cm: number): string {
  if (cm <= 80) return "rgba(255, 96, 96, 0.98)"; // arm's length: stop
  if (cm <= 200) return "rgba(250, 205, 120, 0.95)"; // a stride away
  return "rgba(130, 200, 255, 0.80)"; // the room
}

/** The nearest return in the ring, in cm, or null when it is all clear. */
export function nearestCm(ring: ScanRing): number | null {
  let best: number | null = null;
  for (const cm of ring.r_cm) {
    if (cm === null || cm === undefined || !Number.isFinite(cm) || cm <= 0) {
      continue;
    }
    if (best === null || cm < best) best = cm;
  }
  return best;
}

/**
 * Paint one frame of the radar.
 *
 * `ring` null means no scan has arrived, which is drawn as an EMPTY DIAL WITH
 * A REASON rather than a clear one. A circle of nothing reads as "nothing
 * around you" — the most dangerous sentence this display can say — so the
 * absent case says so in words.
 */
export function paintScan(
  ctx: CanvasRenderingContext2D,
  ring: ScanRing | null,
  reason?: string | null,
): void {
  const cx = W / 2;
  const cy = H / 2;
  const R = W / 2 - 26;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "rgba(10, 14, 22, 0.72)";
  ctx.fillRect(0, 0, W, H);

  const drawable = ring !== null && frameIsDrawable(ring.frame);
  const stale = ring?.stale === true;
  // Dimmed as a whole rather than hidden: an old ring is still the last known
  // shape of the room, and hiding it would have the display flicker between
  // "obstacle" and "nothing" as samples arrive late.
  const dim = stale ? 0.35 : 1;

  // --- range rings ---------------------------------------------------------
  const maxM = ring ? ring.max_cm / 100 : 12;
  ctx.lineWidth = 1;
  ctx.font = "500 11px system-ui, -apple-system, sans-serif";
  for (const m of RANGE_RINGS_M) {
    if (m > maxM) continue;
    const r = Math.sqrt(m / maxM) * R;
    ctx.strokeStyle = `rgba(255,255,255,${0.14 * dim})`;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = `rgba(255,255,255,${0.34 * dim})`;
    ctx.fillText(`${m}m`, cx + 3, cy - r - 3);
  }

  // --- the robot, and which way it is pointing -----------------------------
  ctx.fillStyle = `rgba(255,255,255,${0.9 * dim})`;
  ctx.beginPath();
  ctx.moveTo(cx, cy - 9);
  ctx.lineTo(cx - 6, cy + 6);
  ctx.lineTo(cx + 6, cy + 6);
  ctx.closePath();
  ctx.fill();

  // --- the dots ------------------------------------------------------------
  if (drawable && ring) {
    for (const dot of ringDots(ring)) {
      ctx.fillStyle = dotColor(dot.cm);
      ctx.globalAlpha = dim;
      ctx.beginPath();
      ctx.arc(cx + dot.x * R, cy - dot.y * R, 2.6, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  // --- what the operator is actually looking at ----------------------------
  ctx.font = "600 14px system-ui, -apple-system, sans-serif";
  if (!ring) {
    ctx.fillStyle = "rgba(255, 180, 120, 0.95)";
    ctx.fillText("SIN LIDAR", 14, 22);
    ctx.font = "500 12px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.fillText((reason ?? "no llega /scan").slice(0, 40), 14, H - 14);
    return;
  }
  if (!drawable) {
    // Refused, and named. "The lidar is in a frame I cannot draw" is a
    // fixable sentence; a silently rotated room is not.
    ctx.fillStyle = "rgba(255, 150, 150, 0.95)";
    ctx.fillText("MARCO DESCONOCIDO", 14, 22);
    ctx.font = "500 12px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.fillText(String(ring.frame ?? "").slice(0, 40), 14, H - 14);
    return;
  }

  const near = nearestCm(ring);
  ctx.fillStyle = stale
    ? "rgba(255, 180, 120, 0.95)"
    : "rgba(255,255,255,0.75)";
  ctx.fillText(stale ? "LIDAR DESACTUALIZADO" : "ALREDEDOR", 14, 22);

  ctx.font = "600 13px system-ui, -apple-system, sans-serif";
  if (near === null) {
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.fillText("nada cerca", 14, H - 14);
  } else {
    ctx.fillStyle = dotColor(near);
    ctx.fillText(`lo más cerca ${(near / 100).toFixed(2)} m`, 14, H - 14);
  }
}

// --- GL -------------------------------------------------------------------
//
// Identical in shape to MenuLayer's: a 2D canvas uploaded as a texture and
// stretched over one clip-space quad. Deliberately a copy rather than a shared
// base class — the two panels differ in size, corner and repaint cadence, and
// a base class parameterised by all three would be longer than both.

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
uniform float u_alpha;
varying vec2 v_uv;
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
    throw new Error(`scan shader failed: ${log}`);
  }
  return sh;
}

export class ScanLayer {
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

  #ring: ScanRing | null = null;
  #reason: string | null = null;

  constructor(gl: WebGLRenderingContext) {
    this.#gl = gl;
  }

  get visible(): boolean {
    return this.#visible;
  }

  setVisible(v: boolean): void {
    if (v !== this.#visible) {
      this.#visible = v;
      this.#dirty = true;
    }
  }

  /**
   * A new ring, or null when none is arriving.
   *
   * UNGUARDED, unlike the menu's setters: the whole content of this panel is
   * the thing that changed, so an equality check would be a 120-element
   * comparison to avoid a repaint we are about to need anyway. It is pushed
   * at the poll rate (~4 Hz), not per frame.
   */
  setRing(ring: ScanRing | null, reason?: string | null): void {
    this.#ring = ring;
    this.#reason = reason ?? null;
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
      throw new Error(`scan link failed: ${gl.getProgramInfoLog(program)}`);
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
    // Non-power-of-two: CLAMP + LINEAR or WebGL1 samples black. Same rule as
    // the camera and the menu.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  }

  /**
   * Lower-left of the field — the part the menu does not use.
   *
   * `eye` is the eye being drawn; see `MenuLayer.draw` and `stereo.ts`.
   */
  draw(eye?: EyePose | null): void {
    if (!this.#visible) return;
    this.#ensureCanvas();
    const ctx = this.#ctx;
    const canvas = this.#canvas;
    if (!ctx || !canvas) return;
    if (!this.#program) this.#init();
    if (!this.#program) return;
    const gl = this.#gl;

    if (this.#dirty) {
      paintScan(ctx, this.#ring, this.#reason);
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

    // LOWER-LEFT OF CENTRE, not the bottom-left corner. Smaller than the
    // menu: it is glanced at, not read.
    //
    // The corner placement was invisible in the headset — reported
    // 2026-08-27, "I cannot see the radar and the points". Nothing was broken:
    // at clip-space x ~ -0.96 it was rendering into the vignetted edge of the
    // eye, outside the cone the optics actually show. The same mistake as the
    // panel's top-right, one corner further out.
    //
    // Kept off-centre so it does not sit over the camera picture, and kept
    // below the panel so the two never overlap.
    //
    // Angles, not render-target positions — the same change the menu
    // documents. The canvas is square, and `aspect` plus the eye's own field
    // of view is what keeps it square in the headset; the old
    // viewport-pixel-shape approximation is gone.
    const p = placeQuad(eye, { ox: -0.42, oy: -0.44, sx: 0.2, aspect: H / W });
    gl.uniform2f(this.#scaleLoc, p.sx, p.sy);
    gl.uniform2f(this.#offsetLoc, p.ox, p.oy);
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
