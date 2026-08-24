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

/** Whether the robot can act at all, and what to do if not. */
export type Readiness = { text: string; ok: boolean };

/**
 * A latch that is currently stopping motion, and the gesture that clears it.
 *
 * Both of these are ALREADY on the page — `teleopStatus.deadman_tripped` and
 * `.stopped_by_estop` arrive twice a second — and neither ever reached the
 * headset. The hold latch is the one that matters in practice: it fires at 8 s
 * of continuous motion, which a tapped DOM button never reached and a held
 * thumbstick reaches every time, so walking stops dead and the operator has no
 * way to know a release-and-push is all it wants.
 */
export type MenuAlert = { text: string; hint: string } | null;

export function alertFor(
  deadmanTripped: boolean,
  stoppedByEstop: boolean,
): MenuAlert {
  // e-stop first: it outranks, and its release gesture is the opposite of the
  // hold latch's — you clear a stop by letting go and WAITING, not by pushing
  // again. Showing the wrong one would have the operator fighting it.
  if (stoppedByEstop) {
    return {
      text: "PARADA DE EMERGENCIA",
      hint: "soltá todo y esperá un segundo",
    };
  }
  if (deadmanTripped) {
    return { text: "LÍMITE DE 8 s", hint: "soltá y volvé a empujar" };
  }
  return null;
}

/**
 * What the robot's posture means for the things a headset can ask of it.
 *
 * THIS EXISTS BECAUSE OF A REAL SESSION, 2026-08-21. The operator put the
 * headset on and reported three separate failures: gestures "said something
 * about the arm rejecting it", the walk buttons did nothing, and the robot did
 * not turn with their head. All three were the same fact — the robot was limp
 * in `zero_torque`, with no walk program and no arm-capable FSM — and nothing
 * in the headset said so. Three symptoms, one cause, and an operator with
 * their eyes covered has no way to find it.
 *
 * The page has known this all along: `canGesture` is derived from exactly this
 * posture. It simply never reached the one place the operator was looking.
 *
 * Phrased as what to DO, not as what is wrong. "FSM 0" is true and useless in
 * a headset; "hacé damp → prepare → 501" is the same information the operator
 * can act on without taking it off.
 */
const GESTURE_POSTURES = ["walk", "walk_waist", "run"];

export function readinessFor(
  posture: string | null | undefined,
  online: boolean,
  faults: readonly string[] | null | undefined,
  /**
   * Whether the teleop socket is open and the bridge is reading from it.
   *
   * The state that stopped the 2026-08-24 session and that NOTHING reported.
   * `list_active_tasks` said `active_count: 0` — no session had ever been
   * registered — so head yaw and the thumbstick had nowhere to go. The panel
   * showed no alert, correctly: `alertFor` reports LATCHES, and "there is no
   * socket" is not a latch, it is the absence of the thing a latch would live
   * in.
   *
   * Defaults true so callers that do not know stay unchanged. A page that
   * cannot tell should not claim the bridge is down.
   */
  bridgeConnected: boolean = true,
): Readiness {
  // A low battery is worth saying over everything else: it is the one state
  // that gets WORSE while the operator reads the message, and the one whose
  // remedy is not a command.
  const low = (faults ?? []).find((f) => f.startsWith("low_battery"));
  if (low) {
    const pct = low.replace(/[^0-9]/g, "");
    return { text: `BATERÍA ${pct}% — no operar, poner a cargar`, ok: false };
  }
  if (!online) {
    return { text: "Sin conexión con el robot", ok: false };
  }
  // Before posture. A perfectly-posed robot with no socket to command it
  // through is still a robot that will not move, and saying "Listo" there is
  // the single most misleading thing this banner could do.
  if (!bridgeConnected) {
    return { text: "Puente sin conectar — no comanda", ok: false };
  }
  if (!posture || posture === "unknown" || posture === "no_data_yet") {
    // The signature of a stripped motion controller: the FSM getters answer
    // nothing, so every command returns rpc_code 0 and does nothing at all.
    // The remedy is a script, not a button, so name the script.
    return {
      text: "Sin controlador — correr select_motion_mode.py",
      ok: false,
    };
  }
  if (posture === "zero_torque" || posture === "damp") {
    return { text: "Robot blando — damp → prepare → 501", ok: false };
  }
  if (posture === "preparation") {
    // FSM 4: arms are permitted here, walking is not. Saying only "not ready"
    // would be wrong in the direction that wastes the operator's time.
    return { text: "Gestos sí · caminar necesita 501", ok: false };
  }
  if (GESTURE_POSTURES.includes(posture)) {
    return { text: "Listo", ok: true };
  }
  return { text: `Postura ${posture} — 501 para operar`, ok: false };
}

// Bigger than it started. The first session could read it but called the
// gestures hard to see, and a panel drawn at a comfortable angular size is
// cheaper to read than one the operator leans toward.
const W = 640;
const H = 430;

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
  readiness: Readiness | null = null,
  alert: MenuAlert = null,
): void {
  ctx.clearRect(0, 0, W, H);

  // Dark, mostly-opaque panel. Under passthrough it sits over the room; in VR
  // it sits over the camera, and either way the text has to survive whatever
  // is behind it.
  ctx.fillStyle = "rgba(10, 12, 16, 0.90)";
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(255,255,255,0.20)";
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, W - 2, H - 2);

  ctx.textBaseline = "middle";

  // --- header: title left, readiness right -------------------------------
  ctx.font = "600 26px system-ui, -apple-system, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.92)";
  ctx.fillText("Gestos", 26, 36);

  if (readiness) {
    ctx.font = "600 17px system-ui, -apple-system, sans-serif";
    const w = ctx.measureText(readiness.text).width;
    ctx.fillStyle = readiness.ok
      ? "rgba(80, 200, 130, 0.26)"
      : "rgba(255, 150, 90, 0.26)";
    ctx.fillRect(W - w - 42, 18, w + 24, 36);
    ctx.fillStyle = readiness.ok
      ? "rgba(160, 250, 200, 0.99)"
      : "rgba(255, 205, 160, 0.99)";
    ctx.fillText(readiness.text, W - w - 30, 36);
  }

  // --- alert band: only when a latch is actually stopping motion ----------
  //
  // Full width and loud, because this is the difference between "the robot is
  // broken" and "let go and push again". It cost a session before it existed.
  let top = 78;
  if (alert) {
    ctx.fillStyle = "rgba(255, 90, 90, 0.30)";
    ctx.fillRect(14, top, W - 28, 52);
    ctx.strokeStyle = "rgba(255, 130, 130, 0.75)";
    ctx.lineWidth = 2;
    ctx.strokeRect(14, top, W - 28, 52);
    ctx.font = "700 20px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "rgba(255, 215, 215, 0.99)";
    ctx.fillText(alert.text, 28, top + 18);
    ctx.font = "500 16px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "rgba(255, 190, 190, 0.92)";
    ctx.fillText(alert.hint, 28, top + 38);
    top += 64;
  }

  // --- the list ------------------------------------------------------------
  const rowH = 40;
  items.forEach((item, i) => {
    const y = top + i * rowH + rowH / 2;
    const isSel = i === selected;
    if (isSel) {
      ctx.fillStyle = item.verified
        ? "rgba(90, 170, 255, 0.32)"
        : "rgba(255, 120, 120, 0.20)";
      ctx.fillRect(14, top + i * rowH + 3, W - 28, rowH - 6);
      // A caret as well as a fill: the fill alone is a colour difference, and
      // colour alone is the one cue a headset's optics and an operator's eyes
      // both degrade at the edge of the field.
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      ctx.font = "700 20px system-ui, -apple-system, sans-serif";
      ctx.fillText("\u25B8", 24, y);
    }
    ctx.font = "500 21px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = item.verified
      ? "rgba(255,255,255,0.97)"
      : "rgba(255,255,255,0.40)";
    const label = busy === item.name ? item.label + " \u2026" : item.label;
    ctx.fillText(label, 48, y);

    if (!item.verified) {
      // Struck through, not hidden. A capability the operator knows exists and
      // cannot find reads as a broken page; one they can see and cannot use
      // reads as a decision, which is what it is.
      const w = ctx.measureText(label).width;
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(48, y);
      ctx.lineTo(48 + w, y);
      ctx.stroke();

      ctx.font = "500 14px system-ui, -apple-system, sans-serif";
      ctx.fillStyle = "rgba(255,150,150,0.85)";
      ctx.fillText("sin probar en real", W - 176, y);
    }
  });

  // --- footer: controls, then the last outcome ---------------------------
  const footY = H - 52;
  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(14, footY - 12);
  ctx.lineTo(W - 14, footY - 12);
  ctx.stroke();

  ctx.font = "500 16px system-ui, -apple-system, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  ctx.fillText(
    "A  siguiente     gatillo  ejecutar     joystick  caminar",
    26,
    footY + 4,
  );

  if (status) {
    ctx.font = "600 16px system-ui, -apple-system, sans-serif";
    ctx.fillStyle =
      status.kind === "ok"
        ? "rgba(140, 240, 175, 0.99)"
        : status.kind === "warn"
          ? "rgba(250, 215, 130, 0.99)"
          : "rgba(255, 150, 150, 0.99)";
    ctx.fillText(status.text.slice(0, 52), 26, footY + 28);
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
  #readiness: Readiness | null = null;
  #alert: MenuAlert = null;

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

  setAlert(alert: MenuAlert): void {
    const prev = this.#alert;
    if (prev?.text === alert?.text && prev?.hint === alert?.hint) return;
    this.#alert = alert;
    this.#dirty = true;
  }

  setReadiness(readiness: Readiness | null): void {
    // Guarded: this is pushed from a reactive effect on live state that ticks
    // a few times a second, and repainting the canvas plus re-uploading the
    // texture for an unchanged string is the one cost this panel was designed
    // to avoid.
    const prev = this.#readiness;
    if (prev?.text === readiness?.text && prev?.ok === readiness?.ok) return;
    this.#readiness = readiness;
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
      paintMenu(
        ctx,
        this.#items,
        this.#selected,
        this.#status,
        this.#busy,
        this.#readiness,
        this.#alert,
      );
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
    const sx = 0.4;
    const vpAspect =
      vpWidth && vpHeight && vpWidth > 0 && vpHeight > 0
        ? vpWidth / vpHeight
        : 1;
    const sy = sx * (H / W) * vpAspect;
    gl.uniform2f(this.#scaleLoc, sx, sy);
    // TOP-RIGHT, not bottom-centre.
    //
    // Bottom-centre put it under the camera quad, in the part of the field an
    // operator uses to watch where the robot is walking — so it competed with
    // the view instead of annexing unused space, and reading it meant looking
    // away from the thing being driven. Asked for directly on 2026-08-24:
    // "should be to the right and up".
    //
    // The margins are in clip space, so they hold at any eye aspect: the panel
    // is inset by its own half-size plus a small gap from each edge.
    const margin = 0.04;
    gl.uniform2f(this.#offsetLoc, 1 - sx - margin, 1 - sy - margin);
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
