/**
 * Regression test for a real bug: the system prompt used to hardcode
 * "the robot is currently the Isaac Sim emulation" and claim "walk_to,
 * turn... are real" — both false once the bridge runs against real
 * hardware (where walk_to/turn actually fail with no_pose). An agent
 * given that prompt would confidently assert things it can't back up.
 *
 * `listSkills` now fetches the catalogue live from the bridge (see
 * `../skills/catalogue.ts`), so it's mocked here with a small fixed
 * catalogue rather than needing a real bridge connection for a prompt-text
 * test.
 */
import { describe, expect, test, mock } from "bun:test";
import type { SkillCatalogueEntry } from "@back/skills";

const FIXTURE_SKILLS: SkillCatalogueEntry[] = [
  {
    name: "walk_to",
    description: "Walk to a world-frame position.",
    parameters: {},
    preconditions: [],
    expectedDurationSeconds: 15,
    cancellable: true,
    typicalFailureModes: [],
    classification: "locomotion",
    dangerLevel: "medium",
    status: "real",
    works: { sim: true, real: false },
  },
  {
    name: "walk_velocity",
    description: "Open-loop body-frame velocity command.",
    parameters: {},
    preconditions: [],
    expectedDurationSeconds: 3,
    cancellable: true,
    typicalFailureModes: [],
    classification: "locomotion",
    dangerLevel: "medium",
    status: "real",
    works: { sim: false, real: true },
  },
];

mock.module("@back/skills", () => ({
  listSkills: mock(async () => FIXTURE_SKILLS),
}));

const { buildSystemPrompt } = await import("./runtime");

describe("buildSystemPrompt", () => {
  test("does not hardcode a specific sim/real environment", async () => {
    const prompt = await buildSystemPrompt();
    expect(prompt).not.toContain("currently the Isaac Sim emulation");
    expect(prompt).not.toContain("Locomotion\n(walk_to, turn) and get_state are real");
  });

  test("tells the agent to check env at runtime via get_state", async () => {
    const prompt = await buildSystemPrompt();
    expect(prompt.toLowerCase()).toContain("get_state");
    expect(prompt).toContain("env");
  });

  test("every skill is listed with its actual sim/real availability tag", async () => {
    const prompt = await buildSystemPrompt();
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
