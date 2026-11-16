import { describe, expect, test } from "bun:test";
import {
  resumeError,
  stopStanding,
  stoppedAgo,
} from "../src/lib/robot/stop-latch";

describe("PARAR latch view", () => {
  test("standing only when the bridge says so", () => {
    expect(stopStanding({ stop_standing: true })).toBe(true);
    expect(stopStanding({ stop_standing: false })).toBe(false);
    expect(stopStanding(null)).toBe(false);
    expect(stopStanding({})).toBe(false);
  });

  test("how long ago, from the bridge's unix stamp", () => {
    expect(stoppedAgo({ stopped_at: 100 }, 112_000)).toBe("hace 12 s");
    expect(stoppedAgo({ stopped_at: 100 }, 100_000 + 300_000)).toBe(
      "hace 5 min",
    );
    expect(stoppedAgo({ stopped_at: null }, 1)).toBeNull();
  });

  test("resume failures say what to do", () => {
    expect(resumeError(403, { error: "admin_required" })).toContain(
      "administrador",
    );
    expect(resumeError(503, {})).toContain("SAFETY_CONFIRM_SECRET");
    expect(resumeError(502, null)).toContain("puente");
    expect(resumeError(409, { error: "newer_stop" })).toContain(
      "PARAR más nuevo",
    );
    expect(resumeError(500, { error: "x" })).toBe(
      "No se pudo reanudar (500: x).",
    );
  });
});
