/**
 * The headset layout chosen on 2026-11-23 ("option A"): the camera picture is
 * never covered while driving. A status bar docks above it, the radar docks
 * inside its corner, and the gestures panel stays folded until A.
 *
 * What is pinned here is geometry and gating, not looks: the bar does not
 * overlap the picture, the radar stays on it, and a folded panel can neither
 * be seen nor fired. Whether it reads well through a lens still needs a
 * headset.
 */

import { describe, expect, test } from "bun:test";
import {
  cameraFrame,
  FILL,
  MENU_PLACEMENT,
  scanPlacement,
  statusPlacement,
} from "../src/lib/webxr/hud-layout";
import {
  MenuLayer,
  alertFor,
  type MenuItem,
} from "../src/lib/webxr/menu-layer";
import { paintStatus } from "../src/lib/webxr/status-layer";
import { statusHint } from "../src/lib/webxr/xr-teleop";

/** Half-height of a quad as placeQuad derives it: sx * k * aspect. */
const halfH = (sx: number, k: number, aspect: number) => sx * k * aspect;

// A square ideal eye, and a field a little wider and a little taller than
// square — a Quest's per-eye field is close to square but not exactly.
const FIELDS = [1, 0.9, 1.1];

describe("the status bar sits ON the picture's top edge, not over it", () => {
  for (const k of FIELDS) {
    test(`k=${k}: bottom edge above the picture, same width`, () => {
      const frame = cameraFrame(k);
      const bar = statusPlacement(k);
      const barBottom = bar.oy - halfH(bar.sx, k, bar.aspect);
      expect(barBottom).toBeGreaterThan(frame.sy);
      // Close to it, not floating away in the black.
      expect(barBottom - frame.sy).toBeLessThan(0.05);
      expect(bar.sx).toBeCloseTo(frame.sx, 6);
      expect(bar.ox).toBe(0);
    });
  }
});

describe("the radar sits inside the picture, not hanging off it", () => {
  for (const k of FIELDS) {
    test(`k=${k}: every edge within the picture`, () => {
      const frame = cameraFrame(k);
      const r = scanPlacement(k);
      const h = halfH(r.sx, k, r.aspect);
      expect(r.ox - r.sx).toBeGreaterThanOrEqual(-frame.sx);
      expect(r.ox + r.sx).toBeLessThanOrEqual(frame.sx);
      expect(r.oy - h).toBeGreaterThanOrEqual(-frame.sy);
      expect(r.oy + h).toBeLessThanOrEqual(frame.sy);
      // In the bottom-left quadrant.
      expect(r.ox).toBeLessThan(0);
      expect(r.oy).toBeLessThan(0);
    });
  }

  test("no further out than the picture's own edge (the corner was invisible)", () => {
    // 2026-08-27: a radar at x ~ -0.96 sat in the vignetted periphery.
    const r = scanPlacement(1);
    expect(r.ox - r.sx).toBeGreaterThanOrEqual(-FILL);
  });
});

describe("cameraFrame is the fit CameraLayer always drew", () => {
  test("a 4:3 picture on a square eye: full FILL width, letterboxed height", () => {
    const f = cameraFrame(1, 3 / 4);
    expect(f.sx).toBeCloseTo(FILL, 6);
    expect(f.sy).toBeCloseTo(FILL * 0.75, 6);
  });

  test("a tall picture gives up width, never exceeds the box", () => {
    const f = cameraFrame(1, 2);
    expect(f.sy).toBeCloseTo(FILL, 6);
    expect(f.sx).toBeLessThan(FILL);
  });

  test("nonsense input still yields a finite, visible frame", () => {
    for (const [k, a] of [
      [0, 0.75],
      [NaN, 0.75],
      [1, 0],
      [1, NaN],
    ]) {
      const f = cameraFrame(k, a);
      expect(Number.isFinite(f.sx) && f.sx > 0).toBe(true);
      expect(Number.isFinite(f.sy) && f.sy > 0).toBe(true);
    }
  });
});

describe("the opened panel stays inside the readable cone", () => {
  test("centred horizontally, and clear of the status bar", () => {
    expect(MENU_PLACEMENT.ox).toBe(0);
    const top =
      MENU_PLACEMENT.oy + halfH(MENU_PLACEMENT.sx, 1, MENU_PLACEMENT.aspect);
    const bar = statusPlacement(1);
    const barBottom = bar.oy - halfH(bar.sx, 1, bar.aspect);
    expect(top).toBeLessThan(barBottom);
  });
});

// --- the fold -------------------------------------------------------------------

const ITEMS: MenuItem[] = [
  { name: "voice:open", label: "Hablar con el robot", verified: true },
  { name: "wave", label: "Saludar", verified: true },
  { name: "dance", label: "Bailar", verified: false },
];

function menu(): MenuLayer {
  // Folding and selection never touch GL; only draw() does.
  const m = new MenuLayer({} as WebGLRenderingContext);
  m.setVisible(true);
  m.setItems(ITEMS);
  return m;
}

describe("the gestures panel is folded until opened", () => {
  test("starts folded, and a folded panel fires nothing", () => {
    // The trigger pulled at a panel nobody can see must not run whatever the
    // highlight was last left on.
    const m = menu();
    expect(m.expanded).toBe(false);
    expect(m.selectedItem).toBeNull();
  });

  test("open shows it and arms the highlight; close folds it again", () => {
    const m = menu();
    m.open();
    expect(m.expanded).toBe(true);
    expect(m.selectedItem?.name).toBe("voice:open");
    m.close();
    expect(m.expanded).toBe(false);
    expect(m.selectedItem).toBeNull();
  });

  test("an invisible menu stays folded even if opened", () => {
    const m = menu();
    m.setVisible(false);
    m.open();
    expect(m.expanded).toBe(false);
  });

  test("the voice panel forces itself open, and close does not fold it", () => {
    // A reply into a folded panel is a reply — and a Confirmar — nobody sees.
    const m = menu();
    m.setVoice([{ text: "hola", kind: "robot" }]);
    expect(m.expanded).toBe(true);
    m.close();
    expect(m.expanded).toBe(true);
    m.setVoice(null);
    expect(m.expanded).toBe(false);
  });
});

describe("the status bar's controls hint", () => {
  test("folded, it says A opens the gestures — the only place that does", () => {
    expect(statusHint(false, true)).toContain("A gestos");
    expect(statusHint(false, true)).toContain("PARAR");
  });

  test("open, it keeps only the stop chord", () => {
    expect(statusHint(true, true)).toBe("2 grips PARAR");
  });

  test("hand tracking: never advertise a chord that cannot fire", () => {
    expect(statusHint(false, false)).not.toContain("grips");
    expect(statusHint(true, false)).not.toContain("grips");
  });
});

// --- paintStatus ----------------------------------------------------------------

function stub2d() {
  const texts: string[] = [];
  const ctx = {
    clearRect() {},
    fillRect() {},
    strokeRect() {},
    fillText: (t: string) => texts.push(String(t)),
    measureText: () => ({ width: 80 }),
    font: "",
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    textBaseline: "",
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, texts };
}

describe("paintStatus", () => {
  test("readiness, the last outcome and the hint all show when calm", () => {
    const { ctx, texts } = stub2d();
    paintStatus(
      ctx,
      { text: "Listo", ok: true },
      null,
      { text: "Saludar: listo", kind: "ok" },
      "A gestos",
    );
    expect(texts).toContain("Listo");
    expect(texts).toContain("Saludar: listo");
    expect(texts).toContain("A gestos");
  });

  test("an alert REPLACES the rest — a green Listo beside it would contradict it", () => {
    const { ctx, texts } = stub2d();
    const alert = alertFor(true, false);
    paintStatus(
      ctx,
      { text: "Listo", ok: true },
      alert,
      { text: "Saludar: listo", kind: "ok" },
      "A gestos",
    );
    expect(texts).toContain(alert!.text);
    expect(texts.some((t) => t.includes(alert!.hint))).toBe(true);
    expect(texts).not.toContain("Listo");
    expect(texts).not.toContain("Saludar: listo");
  });

  test("nothing known yet is not a crash", () => {
    const { ctx } = stub2d();
    expect(() => paintStatus(ctx, null, null, null, "")).not.toThrow();
  });
});
