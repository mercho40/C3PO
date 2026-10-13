/**
 * The costmap's status line, in its distinguishable forms.
 *
 * "No map" has three causes with three different fixes — nothing is
 * publishing (start a nav2 stage), the console cannot reach back or the bridge
 * (check the stack), the map arrived unplaceable (a bridge bug) — and a map on
 * screen can additionally be current or old. The label used to check "is there
 * an image?" first, so once any map had been drawn every later failure kept the
 * old picture under a frozen "0.4 s". The retained-map forms below say what
 * went wrong AND how old the picture is.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import { formatAge } from "./format";
// The one table of known bridge sentences (health.ts) — /health shows the
// same costmap hint, and two translations of it would drift.
import { translateBridgeText } from "./bridge-text";
import type { CostmapPlacement, CostmapStatus } from "./costmap.svelte";

export type CostmapView = {
  loaded: boolean;
  status: CostmapStatus;
  url: string | null;
  placement: CostmapPlacement | null;
  ageS: number | null;
  bridgeStale: boolean;
  reason: string | null;
  hint: string | null;
};

export type CostmapLine = {
  /**
   * What state the map is in — words only, no numbers, so it is safe in a
   * live region. The ages used to be part of it, and a `role="status"` on
   * "mapa 0.10 m/celda · 0.4 s" re-announced the map every second.
   */
  label: string;
  /** Resolution and age: the part that ticks. Shown, never announced. */
  meta: string | null;
  tone: "ok" | "warn" | "mute";
  /** Spanish explanation, when there is something to explain. */
  detail: string | null;
  /** The bridge's own words (English, verbatim), for a disclosure. */
  hint: string | null;
  /** `hint` in Spanish when it is a sentence we know (else null). */
  hintEs: string | null;
};

export function describeCostmap(c: CostmapView): CostmapLine {
  const none = { meta: null, hint: null, hintEs: null };
  if (!c.loaded || c.status === "loading") {
    // Before the first answer nothing is known — "sin mapa" here would claim
    // nothing is publishing.
    return { label: "cargando mapa…", tone: "mute", detail: null, ...none };
  }

  const res = c.placement
    ? `${c.placement.resolutionM.toFixed(2)} m/celda`
    : null;
  const retained = c.url !== null && c.placement !== null;
  const last = retained ? `último ${formatAge(c.ageS)}` : null;

  if (c.status === "live" && retained) {
    if (c.bridgeStale) {
      return {
        label: "mapa desactualizado",
        meta: [res, formatAge(c.ageS)].filter(Boolean).join(" · "),
        tone: "warn",
        detail:
          "El puente marca este mapa como desactualizado: Nav2 dejó de actualizarlo.",
        hint: null,
        hintEs: null,
      };
    }
    return {
      label: "mapa en vivo",
      meta: [res, c.ageS === null ? "—" : `${c.ageS.toFixed(1)} s`]
        .filter(Boolean)
        .join(" · "),
      tone: "ok",
      detail: null,
      hint: null,
      hintEs: null,
    };
  }

  if (c.status === "unreachable") {
    return {
      label: "mapa no disponible",
      meta: last,
      tone: "warn",
      detail: c.reason,
      hint: null,
      hintEs: null,
    };
  }
  if (c.status === "noPlacement") {
    return {
      label: retained
        ? "mapa sin ubicación"
        : "mapa sin metadatos de ubicación",
      meta: last,
      tone: "warn",
      detail: c.reason,
      hint: null,
      hintEs: null,
    };
  }
  // noCostmap
  // The bridge's hint is English. A known one leads in Spanish; an unknown
  // one stays verbatim only — a guessed translation is worse than none.
  const hint = c.hint?.trim() || null;
  return {
    label: retained ? "sin mapa publicado" : "sin mapa",
    meta: last,
    tone: retained ? "warn" : "mute",
    detail:
      "Nada está publicando un costmap: casi siempre es que no hay ninguna etapa de nav2 corriendo.",
    hint,
    hintEs: translateBridgeText(hint),
  };
}
