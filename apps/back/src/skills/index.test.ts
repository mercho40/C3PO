/**
 * Tests for the skill registry (`getSkill`/`listSkills`/`registry`) -- the
 * single source of truth `routes/skills.ts` and the agent's tool catalogue
 * both depend on. Had zero direct coverage before this.
 */
import { describe, expect, test } from "bun:test";

import { getSkill, listSkills, registry } from "./index";

describe("skill registry", () => {
  test("getSkill returns the definition for a known skill", () => {
    const skill = getSkill("stop_everything");
    expect(skill).toBeDefined();
    expect(skill?.name).toBe("stop_everything");
  });

  test("getSkill returns undefined for an unknown skill name", () => {
    expect(getSkill("not_a_real_skill")).toBeUndefined();
  });

  test("listSkills returns one catalogue entry per registered skill, no duplicate names", () => {
    const names = listSkills().map((s) => s.name);
    expect(names.length).toBeGreaterThan(0);
    expect(new Set(names).size).toBe(names.length);
    expect(names).toContain("walk_velocity");
    expect(names).toContain("stop_everything");
  });

  test("registry is frozen so it can't be mutated at runtime", () => {
    expect(Object.isFrozen(registry)).toBe(true);
  });

  test("every catalogue entry carries a dangerLevel and works flags", () => {
    for (const entry of listSkills()) {
      expect(["low", "medium", "high"]).toContain(entry.dangerLevel);
      expect(typeof entry.works.sim).toBe("boolean");
      expect(typeof entry.works.real).toBe("boolean");
    }
  });
});
