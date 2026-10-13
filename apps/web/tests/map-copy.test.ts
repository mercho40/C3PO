/**
 * The costmap status line. Pinned because once any map had been drawn, every
 * later failure kept it on screen under its last, frozen age — the README's
 * own rule ("showing an old map is fine, showing it as current is not") broken
 * by the order of an if-chain.
 */

import { describe, expect, test } from "bun:test";
import { describeCostmap, type CostmapView } from "../src/lib/robot/map-copy";

const placement = {
  frame: "odom",
  originXM: -12,
  originYM: -12,
  widthM: 24,
  heightM: 24,
  resolutionM: 0.1,
};

const base: CostmapView = {
  loaded: true,
  status: "live",
  url: "blob:x",
  placement,
  ageS: 0.4,
  bridgeStale: false,
  reason: null,
  hint: null,
};

describe("describeCostmap", () => {
  test("before the first answer, nothing is claimed", () => {
    expect(describeCostmap({ ...base, loaded: false }).label).toBe(
      "cargando mapa…",
    );
  });

  test("a current map shows its resolution and age", () => {
    const l = describeCostmap(base);
    expect(l.label).toBe("mapa en vivo");
    expect(l.meta).toBe("0.10 m/celda · 0.4 s");
    expect(l.tone).toBe("ok");
  });

  test("the announceable label never carries a number", () => {
    // It sits in a role="status": an age inside it was re-read every poll.
    const views: CostmapView[] = [
      base,
      { ...base, bridgeStale: true, ageS: 8 },
      { ...base, status: "unreachable", ageS: 42 },
      { ...base, status: "noCostmap", ageS: 12 },
      { ...base, status: "noPlacement", ageS: 3 },
    ];
    for (const v of views) {
      expect(/\d/.test(describeCostmap(v).label)).toBe(false);
    }
  });

  test("a retained map after a failure says so, with its age", () => {
    const l = describeCostmap({
      ...base,
      status: "unreachable",
      ageS: 42,
      reason: "El servidor responde, pero el puente del robot no.",
    });
    expect(l.label).toBe("mapa no disponible");
    expect(l.meta).toBe("último hace 42 s");
    expect(l.tone).toBe("warn");
    expect(l.detail).toContain("puente");
  });

  test("a retained map after a 503 is not 'mapa no disponible'", () => {
    const l = describeCostmap({
      ...base,
      status: "noCostmap",
      ageS: 12,
      hint: "run c3po perception up nav2",
    });
    expect(l.label).toBe("sin mapa publicado");
    expect(l.meta).toBe("último hace 12 s");
    // Unknown bridge sentence: verbatim only, no invented translation.
    expect(l.hint).toBe("run c3po perception up nav2");
    expect(l.hintEs).toBeNull();
  });

  test("the bridge's known costmap hint gets Spanish first, English kept", () => {
    const en =
      "the costmap comes from Nav2's global_costmap, so it exists only while a nav2 stage is up: `c3po perception up nav2-fake` (no sensors) or `c3po perception up nav2` (claims both sensors)";
    const l = describeCostmap({ ...base, status: "noCostmap", hint: en });
    expect(l.hint).toBe(en);
    expect(l.hintEs).toContain("global_costmap de Nav2");
  });

  test("the three no-map forms stay distinct", () => {
    const none = { ...base, url: null, placement: null, ageS: null };
    const a = describeCostmap({ ...none, status: "noCostmap" }).label;
    const b = describeCostmap({ ...none, status: "unreachable" }).label;
    const c = describeCostmap({ ...none, status: "noPlacement" }).label;
    expect(a).toBe("sin mapa");
    expect(b).toBe("mapa no disponible");
    expect(c).toBe("mapa sin metadatos de ubicación");
    // Nothing retained, so no "último hace …".
    expect(describeCostmap({ ...none, status: "unreachable" }).meta).toBeNull();
  });

  test("the bridge's own stale verdict is shown", () => {
    const l = describeCostmap({ ...base, bridgeStale: true, ageS: 8 });
    expect(l.label).toContain("desactualizado");
    expect(l.tone).toBe("warn");
  });
});
