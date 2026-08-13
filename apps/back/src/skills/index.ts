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
import { clap } from "./clap";
import { damp } from "./damp";
import { forgetLandmark } from "./forget-landmark";
import { getState } from "./get-state";
import { hug } from "./hug";
import { lieUp } from "./lie-up";
import { listActiveTasks } from "./list-active-tasks";
import { listLandmarks } from "./list-landmarks";
import { pointAt } from "./point-at";
import { prepare } from "./prepare";
import { recallLandmark } from "./recall-landmark";
import { releaseArm } from "./release-arm";
import { rememberLandmark } from "./remember-landmark";
import { say } from "./say";
import { shakeHand } from "./shake-hand";
import { sitG1 } from "./sit-g1";
import { squat } from "./squat";
import { startWalking } from "./start-walking";
import { stopEverything } from "./stop-everything";
import { turn } from "./turn";
import { walkTo } from "./walk-to";
import { walkVelocity } from "./walk-velocity";
import { wave } from "./wave";
import { zeroTorque } from "./zero-torque";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ALL: ReadonlyArray<SkillDefinition<any>> = [
  // introspection
  getState,
  // locomotion
  walkTo,
  turn,
  walkVelocity,
  // posture (G1 high-level)
  damp,
  zeroTorque,
  prepare,
  startWalking,
  sitG1,
  lieUp,
  squat,
  // gesture (G1 high-level)
  wave,
  pointAt,
  shakeHand,
  hug,
  clap,
  releaseArm,
  // speech
  say,
  // memory
  rememberLandmark,
  recallLandmark,
  listLandmarks,
  forgetLandmark,
  // safety
  stopEverything,
  // task
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
  clap,
  damp,
  forgetLandmark,
  getState,
  hug,
  lieUp,
  listActiveTasks,
  listLandmarks,
  pointAt,
  prepare,
  recallLandmark,
  releaseArm,
  rememberLandmark,
  say,
  shakeHand,
  sitG1,
  squat,
  startWalking,
  stopEverything,
  turn,
  walkTo,
  walkVelocity,
  wave,
  zeroTorque,
};
export type { SkillCatalogueEntry, SkillDefinition } from "./define";
