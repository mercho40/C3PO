/**
 * What the headset's preset menu offers, and what observe-only is allowed to do.
 *
 * The menu and the page's gesture tiles read ONE list (`PRESETS`), so they
 * cannot drift into offering different things. Its `verified` flag decides
 * whether the trigger can fire an entry at all, so "the catalogue could not be
 * read" must come out as "nothing is verified", never as "everything is".
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { PRESETS, headsetMenu } from "../src/lib/robot/vr-logic";

describe("headsetMenu", () => {
  test("one entry per preset, in the page's order", () => {
    const menu = headsetMenu({}, false);
    expect(menu.map((m) => m.name)).toEqual(PRESETS.map((p) => p.name));
    expect(menu.map((m) => m.label)).toEqual(PRESETS.map((p) => p.label));
  });

  test("verified only where the catalogue says works.real", () => {
    const menu = headsetMenu({ wave: true, dance: false }, false);
    const byName = Object.fromEntries(menu.map((m) => [m.name, m.verified]));
    expect(byName.wave).toBe(true);
    expect(byName.dance).toBe(false);
    // Absent from the catalogue is not verified.
    expect(byName.hug).toBe(false);
  });

  test("an unreadable catalogue verifies nothing, even stale trues", () => {
    const all = Object.fromEntries(PRESETS.map((p) => [p.name, true]));
    expect(headsetMenu(all, true).every((m) => !m.verified)).toBe(true);
  });
});

// Asserted on source for the same reason as vr-session-lifecycle.test.ts: the
// controller needs `navigator.xr` and Svelte runes, which this harness lacks.
// What is pinned is that each of the three independent guards exists.
const controller = readFileSync(
  fileURLToPath(
    new URL("../src/lib/robot/vr-control.svelte.ts", import.meta.url),
  ),
  "utf8",
);

const body = (signature: string): string => {
  const start = controller.indexOf(signature);
  expect(start).toBeGreaterThan(-1);
  // Up to the next method at class indentation.
  const next = controller
    .slice(start + signature.length)
    .search(/\n  [#a-zA-Z].*\(.*\)[^\n]*\{\n/);
  return controller.slice(
    start,
    next < 0 ? undefined : start + signature.length + next,
  );
};

describe("observe-only commands nothing", () => {
  test("the local velocity loop refuses to run", () => {
    expect(body("#loopShouldRun(): boolean {")).toContain(
      "if (this.viewOnly) return false;",
    );
  });

  test("the teleop socket cannot be opened during the session", () => {
    expect(body("connectStream(): void {")).toContain(
      "if (this.viewOnly) return;",
    );
  });

  test("every frame the bridge pulls says observe-only", () => {
    expect(body("#buildTeleopFrame(): TeleopFramePayload {")).toContain(
      "observeOnly: this.viewOnly",
    );
  });

  test("the trigger fires nothing, and neither does the stick", () => {
    const menu = body(
      "#applyMenuButtons(b: { trigger: boolean; primary: boolean }): void {",
    );
    expect(menu.indexOf("if (this.viewOnly) return;")).toBeLessThan(
      menu.indexOf("this.runSkill("),
    );
    expect(body("#applyWalkAxis(axis: -1 | 0 | 1): void {")).toContain(
      "if (this.viewOnly) {",
    );
    expect(body("startWalking(dir: WalkDir): void {")).toContain(
      "if (this.viewOnly) return;",
    );
  });

  test("no tile runs for an observer — lockedOut is what every tile reads", () => {
    expect(body("lockedOut(key: string): boolean {")).toContain(
      "if (this.viewOnly) return true;",
    );
    // …and runSkill goes through it before dispatching anything.
    const run = body("async runSkill(");
    expect(run.indexOf("this.lockedOut(key)")).toBeGreaterThan(-1);
    expect(run.indexOf("this.lockedOut(key)")).toBeLessThan(
      run.indexOf("createApi("),
    );
  });

  test("it never outlives its session", () => {
    expect(body("onEnd: () => {")).toContain("this.viewOnly = false;");
  });
});

describe("a held stick cannot re-arm its own latch", () => {
  test("every reset of the stick demands a sample at neutral first", () => {
    const release = body("releaseAllControls(): void {");
    expect(release).toContain("this.#stickNeedsNeutral = true;");
    // Buttons are assumed HELD after a reset, so a trigger still down is not
    // a new press.
    expect(release).toContain("{ trigger: true, primary: true }");
    expect(body("#refreshReadouts(): void {")).toContain(
      "this.#stickNeedsNeutral = true;",
    );
  });

  test("a deflected stick starts nothing until it has been seen at neutral", () => {
    const axis = body("#applyWalkAxis(axis: -1 | 0 | 1): void {");
    const gate = axis.indexOf("if (this.#stickNeedsNeutral) {");
    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(axis.indexOf("this.startWalking(want);"));
  });

  test("the dead-man does not re-arm while the stick is still held", () => {
    expect(body("#tick(): void {")).toContain("this.#stickWalk === null &&");
  });
});
