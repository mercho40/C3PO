/**
 * Posture and motion.
 *
 * The bug this pins: FSM 802 ("run") — read live on a robot standing perfectly
 * still — rendered as "CORRIENDO", cyan, with the stride animation. Locomotion
 * modes are controllers; motion is only claimed when the pose changes.
 */

import { describe, expect, test } from "bun:test";
import { fsmReadout, readPosture } from "../src/lib/robot/posture";
import {
  MotionTracker,
  angleDelta,
  classifyMotion,
} from "../src/lib/robot/motion";

describe("readPosture", () => {
  test("run is a controller, not a gait", () => {
    const p = readPosture("run", true, { motion: "still" });
    expect(p.label).toBe("Control de marcha");
    expect(p.detail).toContain("Controlador activo; puede dar pasos");
    expect(p.load).toBe("holding");
    expect(p.pose).toBe("stand");
    expect(p.locomotion).toBe(true);
  });

  test("moving is claimed only when the pose moves", () => {
    expect(readPosture("run", true, { motion: "moving" }).load).toBe("moving");
    expect(readPosture("walk", true, { motion: "moving" }).pose).toBe("walk");
    expect(readPosture("walk_waist", true, { motion: "unknown" }).load).toBe(
      "holding",
    );
  });

  test("callers that cannot see motion keep the conservative reading", () => {
    // The stop button asserts itself whenever a gait controller is loaded.
    expect(readPosture("run", true).load).toBe("moving");
    expect(readPosture("run", true).locomotion).toBe(true);
    expect(readPosture("damp", true).locomotion).toBe(false);
  });

  test("a stale FSM keeps the label but drops the load", () => {
    const p = readPosture("preparation", true, { staleS: 34.4 });
    expect(p.label).toBe("Preparado");
    expect(p.load).toBe("unknown");
    expect(p.stale).toBe(true);
    expect(p.detail).toContain("Última lectura hace 34 s");
    expect(p.pose).toBe("stand"); // the last shape we knew, drawn dimmed
  });

  test("unknown means the FSM is not answering, not an unrecognised mode", () => {
    const p = readPosture("unknown", true);
    expect(p.label).toBe("Sin lectura del FSM");
    expect(p.detail).toContain("reiniciá el servicio");
    const q = readPosture("unknown(803)", true);
    expect(q.label).toBe("Desconocido");
    expect(q.detail).toContain("803");
  });

  test("stub standing is mapped and says it is fabricated", () => {
    const p = readPosture("standing", true, { stub: true });
    expect(p.label).toBe("De pie");
    expect(p.detail).toContain("ficticio");
  });

  test("offline uses the shared link vocabulary", () => {
    const p = readPosture("run", false);
    expect(p.label).toBe("Sin conexión");
    expect(p.load).toBe("unknown");
    expect(p.locomotion).toBe(false);
  });

  test("offline says the same word as the topbar for the same fault", () => {
    expect(readPosture(null, false, { problem: "noBridge" }).label).toBe(
      "Sin puente",
    );
    expect(readPosture(null, false, { problem: "noApi" }).label).toBe(
      "Sin acceso a la API",
    );
    expect(readPosture(null, false, { problem: null }).label).toBe(
      "Sin conexión",
    );
  });

  test("no 'last reading' is claimed when none ever arrived", () => {
    const never = readPosture(null, false, {
      problem: "noBridge",
      hasReading: false,
    });
    expect(never.detail).toContain("todavía no recibió ningún estado");
    expect(never.detail).not.toContain("última lectura");
    expect(never.detail).toContain("puente"); // the actionable cause stays

    const had = readPosture("run", false, {
      problem: "noBridge",
      hasReading: true,
    });
    expect(had.detail).toContain("última lectura");
  });

  test("a stale reading keeps its age out of the announceable note", () => {
    const p = readPosture("preparation", true, { staleS: 34.4 });
    expect(/\d/.test(p.note)).toBe(false);
    expect(p.since).toBe("Última lectura hace 34 s");
    const q = readPosture("preparation", true);
    expect(q.note).toBe(q.detail);
    expect(q.since).toBeNull();
  });

  test("the fsm readout carries the index that produced the token", () => {
    expect(fsmReadout("run", 802)).toBe("fsm · run (802)");
    expect(fsmReadout("run", null)).toBe("fsm · run");
    expect(fsmReadout("unknown(803)", 803)).toBe("fsm · unknown(803)");
    expect(fsmReadout(null, 802)).toBeNull();
  });
});

describe("motion", () => {
  const s = (x: number, y: number, yaw: number, t: number) => ({
    x,
    y,
    yaw,
    t,
  });

  test("translation above the jitter gate is motion", () => {
    expect(classifyMotion(s(0, 0, 0, 0), s(0.3, 0, 0, 2000))).toBe("moving");
    expect(classifyMotion(s(0, 0, 0, 0), s(0.02, 0.01, 0, 2000))).toBe("still");
  });

  test("a turn in place is motion", () => {
    expect(classifyMotion(s(0, 0, 0, 0), s(0, 0, 0.4, 2000))).toBe("moving");
  });

  test("yaw wrap-around is not a spin", () => {
    expect(angleDelta(Math.PI - 0.01, -Math.PI + 0.01)).toBeCloseTo(0.02, 9);
    expect(
      classifyMotion(
        s(0, 0, Math.PI - 0.01, 0),
        s(0, 0, -Math.PI + 0.01, 2000),
      ),
    ).toBe("still");
  });

  test("a long gap says nothing about now", () => {
    expect(classifyMotion(s(0, 0, 0, 0), s(1, 0, 0, 20000))).toBe("unknown");
    expect(classifyMotion(null, s(1, 0, 0, 0))).toBe("unknown");
  });

  test("the tracker is idempotent per poll and forgets a frozen pose", () => {
    const m = new MotionTracker();
    const pose = (x: number) => ({
      x_meters_world: x,
      y_meters_world: 0,
      yaw_radians_world: 0,
    });
    expect(m.observe(pose(0), 1000)).toBe("unknown");
    expect(m.observe(pose(0.5), 3000)).toBe("moving");
    // Re-evaluating with the same poll must not invent a second sample.
    expect(m.observe(pose(0.5), 3000)).toBe("moving");
    expect(m.observe(pose(0.5), 5000)).toBe("still");
    // A stale pose reads as perfectly still — exactly the lie to refuse.
    expect(m.observe(pose(0.5), 7000, true)).toBe("unknown");
    expect(m.observe(null, 9000)).toBe("unknown");
  });
});
