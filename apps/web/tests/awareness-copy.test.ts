/**
 * Awareness words. The sign is the thing pinned: D7 bearings are POSITIVE
 * LEFT, and getting that backwards puts every obstacle on the wrong side.
 */

import { describe, expect, test } from "bun:test";
import {
  bearingWords,
  formatConfidence,
  formatRange,
  sceneNote,
  sourceTone,
  sourceWord,
  translateBridge,
} from "../src/lib/robot/awareness-copy";

describe("bearingWords", () => {
  test("positive is LEFT", () => {
    expect(bearingWords(30)).toBe("30° a la izquierda");
    expect(bearingWords(-30)).toBe("30° a la derecha");
  });

  test("ahead and behind bands", () => {
    expect(bearingWords(0)).toBe("adelante");
    expect(bearingWords(15)).toBe("adelante");
    expect(bearingWords(-14.6)).toBe("adelante");
    expect(bearingWords(170)).toBe("atrás");
    expect(bearingWords(-180)).toBe("atrás");
  });
});

describe("readouts", () => {
  test("confidence and range", () => {
    expect(formatConfidence(0.834)).toBe("83 %");
    expect(formatConfidence(undefined)).toBeNull();
    expect(formatRange(1.26)).toBe("1.3 m");
    expect(formatRange(null)).toBe("—");
  });
});

describe("source pills", () => {
  test("a scene that is not current cannot vouch for its sources", () => {
    expect(sourceTone("ok", true)).toBe("ok");
    expect(sourceTone("ok", false)).toBe("neutral");
    expect(sourceTone("offline", true)).toBe("danger");
    // The shared link vocabulary, not a fourth synonym.
    expect(sourceWord("stale")).toBe("desactualizado");
  });
});

describe("bridge sentences", () => {
  test("the note that matters most reaches the operator in Spanish", () => {
    const en =
      "Object detection is OFFLINE — this is not an empty scene. Do not assume the path is clear.";
    const t = sceneNote(en);
    expect(t.es).toContain("NO es una escena vacía");
    expect(t.en).toBe(en); // verbatim kept for the secondary readout
  });

  test("counted notes keep their number", () => {
    expect(
      sceneNote("3 more object(s) detected but not listed (nearest shown).").es,
    ).toContain("3 objeto(s) más");
    expect(
      sceneNote(
        "2 detection(s) arrived malformed and could not be read; they are counted as omitted, not discarded.",
      ).es,
    ).toContain("2 detección(es)");
  });

  test("an unknown sentence stays verbatim, with no guessed translation", () => {
    const t = sceneNote("Perception restarted 4 s ago.");
    expect(t.es).toBeNull();
    expect(t.en).toBe("Perception restarted 4 s ago.");
  });

  test("rules are prefix-anchored, never matched mid-sentence", () => {
    const t = translateBridge("note: LiDAR is OFFLINE", [
      { match: /^LiDAR is OFFLINE\b/, es: "x" },
    ]);
    expect(t.es).toBeNull();
  });
});
