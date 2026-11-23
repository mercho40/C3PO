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
// What is pinned is that each of the three independent guards exists. The
// controller is a facade over units (see vr-control.svelte.ts's header), so
// each guard is looked for in the unit that holds it.
const source = (name: string): string =>
  readFileSync(
    fileURLToPath(new URL(`../src/lib/robot/${name}`, import.meta.url)),
    "utf8",
  );
const controller = source("vr-control.svelte.ts");
const walk = source("vr-walk.svelte.ts");
const teleop = source("vr-teleop-link.svelte.ts");
const presets = source("vr-presets.svelte.ts");
const stop = source("vr-stop.svelte.ts");

const body = (file: string, signature: string): string => {
  const start = file.indexOf(signature);
  expect(start).toBeGreaterThan(-1);
  // Up to the next method at class indentation.
  const next = file
    .slice(start + signature.length)
    .search(/\n  [#a-zA-Z].*\(.*\)[^\n]*\{\n/);
  return file.slice(
    start,
    next < 0 ? undefined : start + signature.length + next,
  );
};

describe("observe-only commands nothing", () => {
  // The local velocity loop refusing to run is pinned by behaviour in
  // vr-control-runtime.test.ts.
  test("the teleop socket cannot be opened during the session", () => {
    expect(body(controller, "connectStream(): Promise<void> {")).toContain(
      "return this.#teleop.connect();",
    );
    const connect = body(teleop, "async connect(): Promise<void> {");
    expect(connect).toContain("if (this.#host.viewOnly()) return;");
    // Re-checked after the token await: observe-only may start meanwhile.
    expect(connect).toContain("this.#host.viewOnly() ||");
  });

  test("every frame the bridge pulls says observe-only", () => {
    expect(
      body(controller, "#buildTeleopFrame(): TeleopFramePayload {"),
    ).toContain("observeOnly: this.viewOnly");
  });

  test("the trigger fires nothing, and neither does the stick", () => {
    const menu = body(controller, "#applyMenuButtons(b: XrButtons): void {");
    expect(menu.indexOf("if (this.viewOnly) return;")).toBeLessThan(
      menu.indexOf("this.runSkill("),
    );
    expect(body(walk, "applyWalkAxis(axis: -1 | 0 | 1): void {")).toContain(
      "if (this.#host.viewOnly()) {",
    );
    expect(body(walk, "startWalking(dir: WalkDir): void {")).toContain(
      "if (this.#host.viewOnly()) return;",
    );
  });

  test("the in-headset PARAR has no observer guard, needs a 300 ms hold, and runs first", () => {
    const chord = body(stop, "applyChord(grips: number, now: number): void {");
    expect(chord).toContain("estop.press()");
    expect(chord).toContain("STOP_CHORD_MS");
    expect(chord).not.toContain("viewOnly");
    // First in onSample: nothing that can throw (walk axis, menu) runs before it.
    const sample = controller.slice(controller.indexOf("onSample: (s) => {"));
    expect(sample.indexOf("this.#stop.applyChord(")).toBeLessThan(
      sample.indexOf("this.#walk.applyWalkAxis("),
    );
    expect(sample.indexOf("this.#stop.applyChord(")).toBeLessThan(
      sample.indexOf("this.#walk.yawErrorRadians"),
    );
    // Talk and voice items stay behind the observer guard.
    const menu = body(controller, "#applyMenuButtons(b: XrButtons): void {");
    const guard = menu.indexOf("if (this.viewOnly) return;");
    expect(menu).not.toContain("estop.press()");
    expect(menu.indexOf("this.#voice?.onTalk(")).toBeGreaterThan(guard);
    expect(menu.indexOf("this.#voice?.onItem(")).toBeGreaterThan(guard);
  });

  test("no tile runs for an observer — lockedOut is what every tile reads", () => {
    expect(body(presets, "lockedOut(key: string): boolean {")).toContain(
      "if (this.#host.viewOnly()) return true;",
    );
    // …and runSkill goes through it before dispatching anything.
    const run = body(presets, "async runSkill(");
    expect(run.indexOf("this.lockedOut(key)")).toBeGreaterThan(-1);
    expect(run.indexOf("this.lockedOut(key)")).toBeLessThan(
      run.indexOf("createApi("),
    );
  });

  test("it never outlives its session", () => {
    expect(body(controller, "onEnd: () => {")).toContain(
      "this.viewOnly = false;",
    );
  });
});

describe("a held stick cannot re-arm its own latch", () => {
  test("every reset of the stick demands a sample at neutral first", () => {
    const release = body(controller, "releaseAllControls(): void {");
    expect(release).toContain("this.#walk.forgetStick();");
    expect(body(walk, "forgetStick(): void {")).toContain(
      "this.#stickNeedsNeutral = true;",
    );
    // Buttons are assumed HELD after a reset, so a trigger still down is not
    // a new press.
    expect(/trigger: true,\s*primary: true/.test(release)).toBe(true);
    expect(body(controller, "#refreshReadouts(): void {")).toContain(
      "this.#walk.noteTracking(this.trackingStale);",
    );
    expect(
      body(walk, "noteTracking(trackingStale: boolean): void {"),
    ).toContain("this.#stickNeedsNeutral = true;");
  });

  test("a deflected stick starts nothing until it has been seen at neutral", () => {
    const axis = body(walk, "applyWalkAxis(axis: -1 | 0 | 1): void {");
    const gate = axis.indexOf("if (this.#stickNeedsNeutral) {");
    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(axis.indexOf("this.startWalking(want);"));
  });

  test("the dead-man does not re-arm while the stick is still held", () => {
    expect(body(walk, "#tick(): void {")).toContain(
      "this.#stickWalk === null &&",
    );
  });
});

// The headset PARAR down the teleop socket (C6), the per-connect token (X15),
// the chord's 300 ms, observe-only sending nothing but PARAR, PARAR dropping
// the arms, and the stale-pose stick release are pinned by behaviour in
// vr-control-runtime.test.ts.
