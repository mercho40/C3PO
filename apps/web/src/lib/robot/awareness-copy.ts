/**
 * Words for what the robot sees and hears.
 *
 * Bearings are egocentric and D7's sign convention is POSITIVE = LEFT (0°
 * ahead). Rendering that sign backwards puts every obstacle on the wrong side
 * of the robot, so the words are derived here, in one tested place, rather
 * than at each call site.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import { LINK } from "./link-copy";
import type { SourceStatus } from "./awareness.svelte";

/** `0` → "adelante", `30` → "30° a la izquierda", `-30` → "30° a la derecha". */
export function bearingWords(bearingDeg: number): string {
  const b = Math.round(bearingDeg);
  if (Math.abs(b) <= 15) return "adelante";
  if (Math.abs(b) >= 165) return "atrás";
  return b > 0
    ? `${Math.abs(b)}° a la izquierda`
    : `${Math.abs(b)}° a la derecha`;
}

/** Detector confidence 0..1 as a whole percent. */
export function formatConfidence(c: number | null | undefined): string | null {
  return c == null || Number.isNaN(c) ? null : `${Math.round(c * 100)} %`;
}

/** Metres to one decimal. */
export function formatRange(m: number | null | undefined): string {
  return m == null || Number.isNaN(m) ? "—" : `${m.toFixed(1)} m`;
}

const SOURCE_NAME: Record<string, string> = {
  detector: "detector",
  lidar: "LiDAR",
  pose: "posición",
};

export function sourceName(key: string): string {
  return SOURCE_NAME[key] ?? key;
}

/** "neutral" is for when the console cannot vouch for the source at all. */
export type SourceTone = "ok" | "warn" | "danger" | "neutral";

export function sourceWord(status: SourceStatus | undefined): string {
  if (status === "ok") return "ok";
  // The shared word for "the reading stopped updating" — "viejo" was a
  // fourth synonym for the same condition (link-copy.ts).
  if (status === "stale") return LINK.stale.toLowerCase();
  if (status === "offline") return "fuera de línea";
  return "sin dato";
}

/**
 * A source's pill tone. When the scene is unreachable or degraded the pills
 * describe a snapshot that is no longer current, so they go neutral: a green
 * "detector: ok" beside "puente inalcanzable" was the panel contradicting
 * itself.
 */
export function sourceTone(
  status: SourceStatus | undefined,
  current: boolean,
): SourceTone {
  if (!current) return "neutral";
  if (status === "ok") return "ok";
  if (status === "stale") return "warn";
  if (status === "offline") return "danger";
  return "neutral";
}

/** The four LiDAR sectors in reading order, with their Spanish names. */
export const FREE_SPACE_SECTORS = [
  ["ahead_m", "adelante"],
  ["left_m", "izquierda"],
  ["right_m", "derecha"],
  ["behind_m", "atrás"],
] as const;

// --- the bridge's own sentences ---------------------------------------------
//
// The bridge speaks English to the model, and its sentences reach this console
// verbatim: D7's notes ("Object detection is OFFLINE — this is not an empty
// scene"), the costmap proxy's hint, the camera relay's hints. They are the
// most important text on their panels — and an operator reading Spanish got
// them only in English.
//
// The fix is NOT to paraphrase whatever arrives: an unknown sentence stays
// verbatim, because a guess at its meaning is worse than the original. The
// KNOWN sentences (copied from apps/bridge — world_model.py, mcp_server.py,
// sdk/camera_relay.py, sdk/videohub.py) get a Spanish line shown first, with
// the English kept underneath as a secondary `lang="en"` readout so whoever
// greps the bridge for it still finds it. Prefix matches, so a reworded tail
// on the bridge side degrades to "verbatim only", never to a wrong
// translation of a different sentence.

/** A bridge sentence: Spanish when it is one we know, plus the original. */
export type BridgeText = {
  /** Operator Spanish, or null when the sentence is not a known one. */
  es: string | null;
  /** The bridge's words, verbatim (English). */
  en: string;
};

/** A known sentence: an anchored prefix pattern and its Spanish. */
export type BridgeRule = {
  match: RegExp;
  es: string | ((m: RegExpMatchArray) => string);
};

/** Translate `text` if a rule knows it; otherwise keep it verbatim only. */
export function translateBridge(
  text: string,
  rules: readonly BridgeRule[],
): BridgeText {
  const en = text.trim();
  for (const rule of rules) {
    const m = en.match(rule.match);
    if (m)
      return { es: typeof rule.es === "string" ? rule.es : rule.es(m), en };
  }
  return { es: null, en };
}

/** D7's notes (apps/bridge/src/bridge/world_model.py). */
export const SCENE_NOTE_RULES: readonly BridgeRule[] = [
  {
    match: /^Object detection is OFFLINE\b/,
    es: "La detección de objetos está fuera de línea: esto NO es una escena vacía. No supongas que el camino está libre.",
  },
  {
    match: /^Object detection is stale\b/,
    es: "La detección de objetos está desactualizada: tratá los obstáculos como inciertos.",
  },
  {
    match: /^LiDAR is OFFLINE\b/,
    es: "El LiDAR está fuera de línea: las distancias de espacio libre no están disponibles, no son infinitas.",
  },
  {
    match: /^Pose is unavailable\b/,
    es: "No hay posición: el movimiento relativo no se puede verificar.",
  },
  {
    match: /^(\d+) more object\(s\) detected but not listed\b/,
    es: (m) =>
      `${m[1]} objeto(s) más detectado(s) que no se listan (se muestran los más cercanos).`,
  },
  {
    match: /^Detector payload had a non-list `objects` field\b/,
    es: "El detector mandó un campo «objects» inválido y se ignoró.",
  },
  {
    match: /^(\d+) detection\(s\) arrived malformed\b/,
    es: (m) =>
      `${m[1]} detección(es) llegaron mal formadas y no se pudieron leer: cuentan como omitidas, no como descartadas.`,
  },
];

export function sceneNote(text: string): BridgeText {
  return translateBridge(text, SCENE_NOTE_RULES);
}
