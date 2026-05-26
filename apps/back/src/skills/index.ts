/**
 * Skill registry — the single source of truth for what the robot can do,
 * expressed in TypeScript. Each skill carries a TypeBox parameter schema
 * (route-validation-ready), human-readable docs, FSM/precondition hints,
 * danger level, and a sim/real availability flag.
 *
 * The bridge's MCP server exposes a parallel surface (FastMCP tools with
 * equivalent Pydantic schemas); long-term these converge through a shared
 * generator. For now this is the canonical TS-side view consumed by:
 *   - GET /skills (catalogue endpoint)
 *   - Phase 3 internal agent (AI SDK tool definitions)
 *   - Supervisor UI skill catalog sidebar
 *   - MCP HTTP adapter (Phase 5)
 */

import type { SkillCatalogueEntry, SkillDefinition } from "./define";
import { toCatalogueEntry } from "./define";

import { cancelTask } from "./cancel-task";
import { damp } from "./damp";
import { getState } from "./get-state";
import { listActiveTasks } from "./list-active-tasks";
import { pointAt } from "./point-at";
import { prepare } from "./prepare";
import { say } from "./say";
import { stopEverything } from "./stop-everything";
import { turn } from "./turn";
import { walkTo } from "./walk-to";
import { wave } from "./wave";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ALL: ReadonlyArray<SkillDefinition<any>> = [
  getState,
  walkTo,
  turn,
  stopEverything,
  say,
  damp,
  prepare,
  wave,
  pointAt,
  cancelTask,
  listActiveTasks,
];

if (process.env.NODE_ENV !== "production") {
  const names = ALL.map((s) => s.name);
  const duplicates = names.filter((n, i) => names.indexOf(n) !== i);
  if (duplicates.length > 0) {
    throw new Error(
      `Duplicate skill names in registry: ${duplicates.join(", ")}`,
    );
  }
}

export const registry = Object.freeze(
  Object.fromEntries(ALL.map((s) => [s.name, s])),
) as Readonly<Record<string, SkillDefinition<any>>>; // eslint-disable-line @typescript-eslint/no-explicit-any

export function listSkills(): SkillCatalogueEntry[] {
  return ALL.map(toCatalogueEntry);
}

export function getSkill(name: string): SkillDefinition<any> | undefined {
  // eslint-disable-line @typescript-eslint/no-explicit-any
  return registry[name];
}

export {
  cancelTask,
  damp,
  getState,
  listActiveTasks,
  pointAt,
  prepare,
  say,
  stopEverything,
  turn,
  walkTo,
  wave,
};
export type { SkillCatalogueEntry, SkillDefinition } from "./define";
