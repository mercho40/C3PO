/**
 * Regression test for a real bug: the system prompt used to hardcode
 * "the robot is currently the Isaac Sim emulation" and claim "walk_to,
 * turn... are real" — both false once the bridge runs against real
 * hardware (where walk_to/turn actually fail with no_pose). An agent
 * given that prompt would confidently assert things it can't back up.
 *
 * Pure string generation, no network/API key needed — `bun test` runs
 * this directly (apps/back has no test runner configured yet otherwise;
 * this is the first).
 */
import { describe, expect, test } from "bun:test";

import { buildSystemPrompt } from "./runtime";

describe("buildSystemPrompt", () => {
  test("does not hardcode a specific sim/real environment", () => {
    const prompt = buildSystemPrompt();
    expect(prompt).not.toContain("currently the Isaac Sim emulation");
    expect(prompt).not.toContain("Locomotion\n(walk_to, turn) and get_state are real");
  });

  test("tells the agent to check env at runtime via get_state", () => {
    const prompt = buildSystemPrompt();
    expect(prompt.toLowerCase()).toContain("get_state");
    expect(prompt).toContain("env");
  });

  test("every skill is listed with its actual sim/real availability tag", () => {
    const prompt = buildSystemPrompt();
    // walk_to is sim-only (works.real: false) -- catalogue tag must say so,
    // not just leave it to the (removed) hardcoded claim above.
    const walkToLine = prompt.split("\n").find((l) => l.startsWith("- walk_to "));
    expect(walkToLine).toBeDefined();
    expect(walkToLine).toContain("sim-only");

    // walk_velocity is real-only (works.real: true, works.sim: false).
    const walkVelocityLine = prompt.split("\n").find((l) => l.startsWith("- walk_velocity "));
    expect(walkVelocityLine).toBeDefined();
    expect(walkVelocityLine).toContain("real-only");
  });
});
