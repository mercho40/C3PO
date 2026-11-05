/**
 * What the robot camera's state MEANS, in operator Spanish.
 *
 * `mjpeg-camera.ts` reports a state and a short detail token ("network",
 * "cors", "HTTP 503: …", "sin cuadros hace 3.2 s"). Two pages render that feed
 * — /live-camera and the camera mirror in /vr-control — and the mirror used to
 * print the raw token ("Sin señal — network") while /live-camera translated it.
 * One translation, here, keeps the two from drifting again.
 *
 * The distinctions this module exists to keep apart, because each sends the
 * operator to a different layer:
 *
 *  - a picture that is real but OLD (stale, frames > 0)   → "Congelado"
 *  - a camera that never produced a frame (frames == 0)   → "Sin señal", not
 *    "congelado": there is nothing frozen on screen, and calling an empty
 *    panel frozen implies a picture that was never there
 *  - nobody answered (network)  vs  somebody answered but the browser hid it
 *    (CORS)  vs  the browser refused to even ask (mixed content)
 *  - /status says live but the <img> failed to load (`broken`) — a blank
 *    canvas must never wear an EN VIVO badge
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import type {
  RobotCamSource,
  RobotCamState,
  RobotCamStatus,
} from "./mjpeg-camera";
// The one table of known bridge sentences — /health shows the same camera
// hints, and two translations of one sentence drift apart.
import { translateBridgeText } from "./bridge-text";

/** The badge vocabulary, shared with the simulator's feeds. */
export type CamBadge = "live" | "connecting" | "stale" | "down";

export const CAM_BADGE_LABEL: Record<CamBadge, string> = {
  live: "EN VIVO",
  connecting: "Conectando",
  stale: "Congelado",
  down: "Sin señal",
};

/** Text + dot classes per badge. Live is `ok`, not cyan: cyan means motion. */
export const CAM_BADGE_TONE: Record<CamBadge, { text: string; dot: string }> = {
  live: { text: "border-ok/30 bg-ok/10 text-ok", dot: "bg-ok" },
  connecting: { text: "border-warn/30 bg-warn/10 text-warn", dot: "bg-warn" },
  stale: { text: "border-warn/30 bg-warn/10 text-warn", dot: "bg-warn" },
  down: {
    text: "border-danger/30 bg-danger/10 text-danger-soft",
    dot: "bg-danger",
  },
};

/**
 * The relay's two upstream servers, named for an operator: WHICH camera feed,
 * not which program. The program names ("videohub", "apps/perception") are
 * for whoever debugs the robot and live in `SOURCE_TECH`, shown as a
 * secondary readout — they used to BE the label.
 */
export const SOURCE_LABEL: Record<RobotCamSource, string> = {
  videohub: "cámara del fabricante",
  vision: "cámara de visión",
  none: "ninguna",
};

/** The technical name and native size behind each source, for a readout. */
export const SOURCE_TECH: Record<RobotCamSource, string | null> = {
  videohub: "videohub · 1920×1080",
  vision: "apps/perception · 640×480",
  none: null,
};

/** A body is prose with the occasional literal (a hostname, a command). */
export type CopyPart = string | { code: string };

export type CamOverlay =
  | { kind: "connecting" }
  | {
      kind: "message";
      title: string;
      body: CopyPart[];
      /** The bridge's own sentence, English and verbatim, when it gave one. */
      bridgeText: string | null;
      /**
       * `bridgeText` in Spanish when it is a sentence we know — shown FIRST,
       * with the English kept underneath. Null for unknown sentences, which
       * stay verbatim rather than get a guessed translation.
       */
      bridgeEs: string | null;
      /** True when the (old) picture stays visible under the overlay. */
      seeThrough: boolean;
    };

export type RobotCamCopy = {
  badge: CamBadge;
  /** Null exactly when the picture is live and loading. */
  overlay: CamOverlay | null;
};

export type DescribeInput = {
  state: RobotCamState;
  detail: string | null;
  status?: RobotCamStatus | null;
  /** The stream URL the page mounted, or null before one exists. */
  url: string | null;
  /** The <img> fired `error` for the current URL. */
  broken?: boolean;
  /** `location.protocol` / `location.origin`; absent during SSR. */
  pageProtocol?: string | null;
  pageOrigin?: string | null;
};

/** `http://h:8001/camera/stream.mjpg?c=3` → `http://h:8001/camera`. */
export function cameraBaseFromUrl(url: string | null): string | null {
  if (!url) return null;
  return url.replace(/\/stream\.mjpg(\?.*)?$/, "").replace(/\/+$/, "");
}

/**
 * An HTTPS console cannot fetch or embed an HTTP camera. The browser blocks
 * it before any request leaves, so to the client it looks exactly like an
 * unreachable host — and the operator goes and checks the robot's network.
 */
export function isMixedContent(
  pageProtocol: string | null | undefined,
  url: string | null | undefined,
): boolean {
  return pageProtocol === "https:" && !!url && /^http:/i.test(url);
}

/** `"HTTP 503: no frame yet"` → `{ status: 503, words: "no frame yet" }`. */
export function parseHttpDetail(
  detail: string | null | undefined,
): { status: number; words: string | null } | null {
  const m = detail?.match(/^HTTP (\d{3})(?::\s*(.+))?$/s);
  if (!m) return null;
  return { status: Number(m[1]), words: m[2]?.trim() || null };
}

/** The badge alone, for pages that only need the word. */
export function robotCamBadge(
  state: RobotCamState,
  status: RobotCamStatus | null | undefined,
  broken = false,
): CamBadge {
  if (state === "connecting") return "connecting";
  if (state === "live") return broken ? "down" : "live";
  // Frozen only if something was ever on screen to be frozen.
  if (state === "stale" && (status?.frames ?? 0) > 0 && !broken) return "stale";
  return "down";
}

/** Prose → plain text, for tests and aria-labels. */
export function plainText(parts: CopyPart[]): string {
  return parts.map((p) => (typeof p === "string" ? p : p.code)).join("");
}

/** Everything the overlay says, from the client's state. */
export function describeRobotCam(input: DescribeInput): RobotCamCopy {
  const { state, detail, status, url, broken = false } = input;
  const base = cameraBaseFromUrl(url) ?? "la cámara";
  const mixed = isMixedContent(input.pageProtocol, url);
  const badge = robotCamBadge(state, status, broken);
  const bridgeHint = status?.hint?.trim() || null;

  if (state === "connecting") return { badge, overlay: { kind: "connecting" } };

  if (state === "live" && !broken) return { badge, overlay: null };

  if (state === "live" && broken) {
    // /status answers and says frames are flowing, but the <img> could not
    // open stream.mjpg. The page retries on its own; say so, and name the
    // layer most likely at fault.
    return {
      badge,
      overlay: {
        kind: "message",
        title: "El video no carga",
        body: mixed
          ? MIXED_CONTENT_BODY
          : [
              "El puente informa cuadros nuevos, pero el navegador no pudo abrir el flujo MJPEG. Reintentando solo; si persiste, puede que el relé del puente no llegue a la fuente.",
            ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
    };
  }

  if (state === "stale") {
    const frames = status?.frames ?? 0;
    const source =
      status?.source && status.source !== "none" ? status.source : null;
    if (frames > 0) {
      // With the <img> broken there is no last frame on screen either, so it
      // is not "frozen" — it is a camera that stopped, over an empty panel.
      return {
        badge,
        overlay: {
          kind: "message",
          title: broken
            ? "La cámara dejó de entregar cuadros"
            : "La imagen está congelada",
          body: [
            source
              ? `La ${SOURCE_LABEL[source]} dejó de entregar cuadros`
              : "La cámara dejó de entregar cuadros",
            detail ? ` (${detail}).` : ".",
            broken
              ? ""
              : " Lo que se ve es el último cuadro, no la escena actual.",
          ],
          bridgeText: bridgeHint,
          bridgeEs: translateBridgeText(bridgeHint),
          seeThrough: !broken,
        },
      };
    }
    return {
      badge,
      overlay: {
        kind: "message",
        title: "Todavía no hay imagen",
        body: [
          "El puente responde, pero ninguna fuente entregó un cuadro todavía. Lo habitual es que otro proceso tenga la cámara: el feed del fabricante necesita ",
          { code: "videohub_pc4" },
          " y el de visión necesita ",
          { code: "c3po perception up perception" },
          " con la cámara tomada.",
        ],
        bridgeText: bridgeHint,
        bridgeEs: translateBridgeText(bridgeHint),
        seeThrough: false,
      },
    };
  }

  if (state === "closed") {
    return {
      badge: "down",
      overlay: {
        kind: "message",
        title: "Cámara en pausa",
        body: ["La consola cerró el video. Reconectá para volver a verlo."],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
    };
  }

  // state === "error"
  if (mixed) {
    return {
      badge,
      overlay: {
        kind: "message",
        title: "El navegador bloquea la cámara",
        body: MIXED_CONTENT_BODY,
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
    };
  }

  if (detail === "cors") {
    return {
      badge,
      overlay: {
        kind: "message",
        title: "El servidor de la cámara no autoriza a esta consola",
        body: [
          `${base} responde, pero el navegador oculta la respuesta por CORS. Normalmente la cámara pasa por apps/back: su `,
          { code: "WEB_URL" },
          " tiene que ser ",
          { code: input.pageOrigin ?? "el origen de esta consola" },
          ". Si la apuntaste directo al puente para depurar, eso no funciona desde el navegador (va con la cookie y el puente no la acepta): usá ",
          { code: "http://localhost:3000/camera" },
          ".",
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
    };
  }

  if (detail === "network") {
    return {
      badge,
      overlay: {
        kind: "message",
        title: "No hay video del robot",
        body: [
          `No se puede alcanzar ${base}. Verificá que apps/back esté corriendo (`,
          { code: "bun run dev" },
          "), que llegue al puente por el túnel (",
          { code: "ssh -L 8001:127.0.0.1:8001 c3po" },
          ", que resuelve ",
          { code: "g1-orin.local" },
          ") y que ",
          { code: "c3po up" },
          " esté activo en el robot.",
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
    };
  }

  if (detail === "timeout") {
    return {
      badge,
      overlay: {
        kind: "message",
        title: "La cámara no responde",
        body: [
          `${base} no contestó a tiempo. El puente puede estar sobrecargado o la red del robot, saturada.`,
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
    };
  }

  const http = parseHttpDetail(detail);
  if (http?.status === 404) {
    // The usual cause is the one the local .env shipped with: a URL that
    // already ends in /stream.mjpg, so the client asks for
    // /camera/stream.mjpg/status.
    return {
      badge,
      overlay: {
        kind: "message",
        title: "La dirección de la cámara no es la correcta",
        body: [
          `${base} no tiene /status. `,
          { code: "PUBLIC_ROBOT_CAM_URL" },
          " tiene que terminar en ",
          { code: "/camera" },
          ", sin ",
          { code: "/stream.mjpg" },
          ".",
        ],
        bridgeText: http.words,
        bridgeEs: translateBridgeText(http.words),
        seeThrough: false,
      },
    };
  }
  if (http) {
    return {
      badge,
      overlay: {
        kind: "message",
        title: "La cámara no está disponible",
        body: [`${base} respondió HTTP ${http.status}.`],
        bridgeText: http.words,
        bridgeEs: translateBridgeText(http.words),
        seeThrough: false,
      },
    };
  }

  return {
    badge,
    overlay: {
      kind: "message",
      title: "No hay video del robot",
      body: [`${base} respondió con un error.`],
      bridgeText: detail,
      bridgeEs: translateBridgeText(detail),
      seeThrough: false,
    },
  };
}

const MIXED_CONTENT_BODY: CopyPart[] = [
  "Esta consola se sirve por HTTPS y la cámara por HTTP, y el navegador bloquea ese contenido mixto antes de pedirlo. Abrí la consola por HTTP en la red del robot, o poné la cámara detrás de un proxy HTTPS.",
];

// --- the simulator's WebRTC feeds -------------------------------------------

/**
 * How long sim-camera.ts waits for the /offer answer before calling it
 * "timeout". Lives here so the overlay can say the number it waited.
 */
export const SIM_OFFER_TIMEOUT_MS = 5000;

/** Lifecycle of one sim feed — mirrors `SimCamState` in sim-camera.ts. */
export type SimFeedState = "connecting" | "live" | "stale" | "error" | "closed";

export function simCamBadge(state: SimFeedState): CamBadge {
  if (state === "live") return "live";
  if (state === "connecting") return "connecting";
  if (state === "stale") return "stale";
  return "down";
}

export type SimCamCopy = {
  badge: CamBadge;
  /** Short words for a small tile. */
  short: string;
  overlay: CamOverlay | null;
  /** Offer the per-port certificate links (only when it may be the cert). */
  certLinks: boolean;
};

/** The sim feed's overlay copy, from sim-camera.ts's state and detail. */
export function describeSimCam(
  state: SimFeedState,
  detail: string | null,
  where: string,
): SimCamCopy {
  const badge = simCamBadge(state);
  if (state === "live") {
    return {
      badge,
      short: CAM_BADGE_LABEL.live,
      overlay: null,
      certLinks: false,
    };
  }
  if (state === "connecting") {
    return {
      badge,
      short: CAM_BADGE_LABEL.connecting,
      overlay: { kind: "connecting" },
      certLinks: false,
    };
  }
  if (state === "stale") {
    return {
      badge,
      short: CAM_BADGE_LABEL.stale,
      overlay: {
        kind: "message",
        title: "La imagen está congelada",
        body: [
          detail === "sin cuadros"
            ? "La conexión sigue abierta pero no llegan cuadros."
            : "La conexión WebRTC se interrumpió.",
          " Lo que se ve es el último cuadro, no la escena actual.",
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: true,
      },
      certLinks: false,
    };
  }
  if (detail === "unreachable") {
    return {
      badge,
      short: "Sin acceso",
      overlay: {
        kind: "message",
        title: "No se pudo abrir la cámara",
        body: [
          `Certificado sin aceptar o simulador apagado en ${where}: desde el navegador no se distinguen. Si el simulador está corriendo, abrí los tres puertos una vez, aceptá el certificado en cada uno y volvé acá.`,
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
      certLinks: true,
    };
  }
  if (detail === "timeout") {
    // The /offer never answered. An unaccepted certificate fails at once
    // (the TypeError above), so a hang is a host that does not answer at
    // all: the sim is off, or this browser is not on its network.
    return {
      badge,
      short: "Sin respuesta",
      overlay: {
        kind: "message",
        title: "El simulador no responde",
        body: [
          `El servidor de cámara en ${where} no contestó en ${Math.round(SIM_OFFER_TIMEOUT_MS / 1000)} s. Revisá que el simulador esté corriendo y que este navegador esté en su misma red.`,
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
      certLinks: false,
    };
  }
  if (detail === "sin video") {
    return {
      badge,
      short: "Sin video",
      overlay: {
        kind: "message",
        title: "El servidor no envió cuadros",
        body: [
          `El servidor de cámara en ${where} respondió, pero no llegó ningún cuadro (probó H.264 y VP8). Revisá que la escena del simulador esté corriendo y no en pausa.`,
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
      certLinks: false,
    };
  }
  if (detail === "conexión perdida") {
    return {
      badge,
      short: CAM_BADGE_LABEL.down,
      overlay: {
        kind: "message",
        title: "Se perdió la conexión",
        body: [
          `La conexión con ${where} se cortó. Revisá que el simulador siga corriendo y reconectá.`,
        ],
        bridgeText: null,
        bridgeEs: null,
        seeThrough: false,
      },
      certLinks: false,
    };
  }
  return {
    badge,
    short: CAM_BADGE_LABEL.down,
    overlay: {
      kind: "message",
      title:
        state === "closed" ? "Cámara cerrada" : "No hay video del simulador",
      body: [
        detail
          ? `El servidor de cámara en ${where} respondió con un error.`
          : `No hay video de ${where}.`,
      ],
      bridgeText: detail,
      bridgeEs: translateBridgeText(detail),
      seeThrough: false,
    },
    certLinks: false,
  };
}

/**
 * A bridge hint from /status (a source's, or the relay's): Spanish when it is
 * a sentence we know, always with the verbatim English. Null for no hint.
 */
export function cameraHint(
  text: string | null | undefined,
): { es: string | null; en: string } | null {
  const en = text?.trim();
  return en ? { es: translateBridgeText(en), en } : null;
}

// --- snapshot failures ------------------------------------------------------

/** A failed snapshot, for a toast (Spanish) and a readout (verbatim). */
export type SnapshotFailure = {
  /** What happened, in operator Spanish. Always set. */
  es: string;
  /** The underlying words (often the bridge's English), or null. */
  raw: string | null;
};

/** The page throws this when the head <video> has no frame to draw. */
export const NO_FRAME = "no-frame";

/**
 * Why the snapshot failed. The toast used to show `err.message` raw — "Failed
 * to fetch", "signal timed out", "no frame yet" — English in a Spanish UI,
 * with no hint of which layer failed.
 */
export function snapshotFailure(err: unknown): SnapshotFailure {
  if (
    err instanceof DOMException &&
    (err.name === "TimeoutError" || err.name === "AbortError")
  ) {
    return { es: "La cámara no respondió a tiempo.", raw: null };
  }
  if (err instanceof TypeError) {
    return {
      es: "No se pudo alcanzar la cámara desde este navegador.",
      raw: err.message || null,
    };
  }
  const message = err instanceof Error ? err.message.trim() : "";
  if (message === NO_FRAME) {
    return { es: "Todavía no hay un cuadro para capturar.", raw: null };
  }
  const http = parseHttpDetail(message);
  if (http) {
    return {
      es: http.words
        ? (translateBridgeText(http.words) ??
          `La cámara respondió HTTP ${http.status}.`)
        : `La cámara respondió HTTP ${http.status}.`,
      raw: http.words,
    };
  }
  if (!message) return { es: "Error desconocido al capturar.", raw: null };
  return {
    es: translateBridgeText(message) ?? "La cámara respondió con un error.",
    raw: message,
  };
}
