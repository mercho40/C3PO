// Who the robot's camera sees right now, drawn as boxes over the picture.
//
// The bridge serves `GET /face/seen` (open, no cookie) next to its `/camera/*`
// routes, updated about once a second. Nothing here is stored or logged: the
// newest answer replaces the last one and is forgotten when it goes stale.
//
// One drawing routine, `drawFaceBoxes`, serves both the console overlay (a
// canvas over the <img>) and the headset (composed into the frame before the
// WebGL upload), so the two cannot disagree about a label or a colour.

export type SeenFace = {
  /** [x, y, w, h] in pixels of a `width` x `height` frame. */
  box: [number, number, number, number];
  name: string | null;
  role: "superadmin" | "operator" | null;
  known: boolean;
  /** false = liveness check failed (a photo?); null = not judged. */
  live: boolean | null;
};

export type FaceSeen = {
  stamp: number;
  width: number;
  height: number;
  age_s: number;
  faces: SeenFace[];
};

/** Older than this and a box is not drawn: a stale label must never linger. */
export const FACE_STALE_S = 2;
export const FACE_POLL_MS = 1000;
export const FACE_BACKOFF_MAX_MS = 10_000;
export const FACE_BOXES_KEY = "c3po.faceBoxes";

/** `http://h:8001/camera` → `http://h:8001/face/seen`: the same host as the picture. */
export function faceSeenUrl(camBase: string): string {
  return `${camBase
    .trim()
    .replace(/\/+$/, "")
    .replace(/\/camera$/, "")}/face/seen`;
}

/**
 * The faces still worth drawing. `elapsedS` is how long ago the answer was
 * received, so a poller that stopped (error, tab hidden) still lets the last
 * answer expire instead of freezing its labels on screen.
 */
export function freshFaces(seen: FaceSeen | null, elapsedS = 0): SeenFace[] {
  if (!seen || !Array.isArray(seen.faces)) return [];
  if (!(seen.age_s + elapsedS <= FACE_STALE_S)) return [];
  return seen.faces;
}

export type FaceLook = { text: string; color: string; dim: boolean };

const KNOWN = "rgb(74, 222, 128)";
const UNKNOWN = "rgb(251, 191, 36)";

export function faceLook(face: SeenFace): FaceLook {
  const base = face.known && face.name ? face.name : "Desconocido";
  const photo = face.live === false;
  return {
    text: photo ? `${base} · ¿foto?` : base,
    color: face.known && face.name ? KNOWN : UNKNOWN,
    dim: photo,
  };
}

export type Rect = { x: number; y: number; w: number; h: number };

/** Where an `iw` x `ih` picture lands inside a `bw` x `bh` box under object-fit: contain. */
export function containRect(
  bw: number,
  bh: number,
  iw: number,
  ih: number,
): Rect {
  if (!(bw > 0 && bh > 0 && iw > 0 && ih > 0))
    return { x: 0, y: 0, w: 0, h: 0 };
  const s = Math.min(bw / iw, bh / ih);
  const w = iw * s;
  const h = ih * s;
  return { x: (bw - w) / 2, y: (bh - h) / 2, w, h };
}

/** A face box (frame pixels) mapped into `rect`, the displayed picture. */
export function placeBox(
  box: SeenFace["box"],
  frameW: number,
  frameH: number,
  rect: Rect,
): Rect {
  const sx = frameW > 0 ? rect.w / frameW : 0;
  const sy = frameH > 0 ? rect.h / frameH : 0;
  return {
    x: rect.x + box[0] * sx,
    y: rect.y + box[1] * sy,
    w: box[2] * sx,
    h: box[3] * sy,
  };
}

type Ctx = Pick<
  CanvasRenderingContext2D,
  | "strokeRect"
  | "fillRect"
  | "fillText"
  | "measureText"
  | "strokeStyle"
  | "fillStyle"
  | "lineWidth"
  | "font"
  | "globalAlpha"
>;

/** Draw `faces` (from a `frameW` x `frameH` frame) into `rect` of `ctx`. */
export function drawFaceBoxes(
  ctx: Ctx,
  faces: SeenFace[],
  frameW: number,
  frameH: number,
  rect: Rect,
): void {
  // Text scales with the picture so it reads the same in a panel and a headset.
  const px = Math.max(12, Math.round(rect.h / 28));
  ctx.lineWidth = Math.max(2, px / 6);
  ctx.font = `600 ${px}px system-ui, sans-serif`;
  for (const face of faces) {
    const b = placeBox(face.box, frameW, frameH, rect);
    const look = faceLook(face);
    ctx.globalAlpha = look.dim ? 0.5 : 1;
    ctx.strokeStyle = look.color;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    const tw = ctx.measureText(look.text).width + px * 0.6;
    const ty = b.y >= px * 1.4 ? b.y - px * 1.4 : b.y + b.h;
    ctx.fillStyle = "rgba(8, 11, 17, 0.75)";
    ctx.fillRect(b.x, ty, tw, px * 1.4);
    ctx.fillStyle = look.color;
    ctx.fillText(look.text, b.x + px * 0.3, ty + px * 1.05);
  }
  ctx.globalAlpha = 1;
}

/** Remembered per browser; storage can throw (private mode), so it never matters. */
export function faceBoxesEnabled(): boolean {
  try {
    return localStorage.getItem(FACE_BOXES_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setFaceBoxesEnabled(on: boolean): void {
  try {
    localStorage.setItem(FACE_BOXES_KEY, on ? "on" : "off");
  } catch {
    // Not remembered; still applied for this page.
  }
}

export type FacePollDeps = {
  fetch?: typeof fetch;
  /** Skip polling while true (the tab is hidden). Omit to always poll. */
  hidden?: () => boolean;
  schedule?: (fn: () => void, ms: number) => unknown;
  cancel?: (t: unknown) => void;
};

/**
 * Poll `url` about once a second, handing each answer (or null on failure) to
 * `onSeen` together with when it arrived. No request is made while `hidden()`
 * says so, and failures back off up to FACE_BACKOFF_MAX_MS. Returns `stop`.
 */
export function pollFaceSeen(
  url: string,
  onSeen: (seen: FaceSeen | null, receivedAt: number) => void,
  deps: FacePollDeps = {},
): () => void {
  const doFetch = deps.fetch ?? fetch;
  const schedule = deps.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel =
    deps.cancel ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  let stopped = false;
  let failures = 0;
  let timer: unknown = null;

  async function tick() {
    timer = null;
    if (stopped) return;
    if (!deps.hidden?.()) {
      try {
        const res = await doFetch(url, {
          cache: "no-store",
          credentials: "omit",
          signal: AbortSignal.timeout(FACE_POLL_MS * 2),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const seen = (await res.json()) as FaceSeen;
        failures = 0;
        if (!stopped) onSeen(seen, Date.now());
      } catch {
        failures += 1;
        if (!stopped) onSeen(null, Date.now());
      }
    }
    if (stopped) return;
    const delay = Math.min(FACE_BACKOFF_MAX_MS, FACE_POLL_MS * 2 ** failures);
    timer = schedule(() => void tick(), delay);
  }

  void tick();
  return () => {
    stopped = true;
    if (timer !== null) cancel(timer);
  };
}
