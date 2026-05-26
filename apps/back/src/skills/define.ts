import { t, type TSchema } from "elysia";

/**
 * Severity hint for the LLM / agent. Low-danger skills can be invoked
 * autonomously; medium gets a confirmation prompt; high requires explicit
 * dry-run + confirm flow (Phase 3 of plan).
 */
export type DangerLevel = "low" | "medium" | "high";

/**
 * Coarse grouping used by the supervisor UI to bucket skills in the catalogue
 * sidebar. Add new values as we expand the catalogue — keep the set small.
 */
export type SkillClassification =
  | "introspection"
  | "locomotion"
  | "posture"
  | "gesture"
  | "perception"
  | "speech"
  | "memory"
  | "safety"
  | "task";

/**
 * Where the skill stands in our implementation pipeline. The supervisor UI
 * surfaces this so operators (and the agent) know which tools are real.
 */
export type SkillStatus = "real" | "stub" | "planned";

export interface SkillDefinition<TParams extends TSchema> {
  name: string;
  description: string;
  parameters: TParams;
  /** Human-readable preconditions; surfaced to the LLM in the system prompt. */
  preconditions?: readonly string[];
  /** Best-guess runtime; lets schedulers and the LLM set reasonable timeouts. */
  expectedDurationSeconds: number;
  /** True if the skill exposes a cancel path (task_id + cancel_task). */
  cancellable: boolean;
  /** What happens when the cancel signal is received mid-flight. */
  cancellationEffect?: string;
  /** Typical failure modes to surface in catalogue docs + error messages. */
  typicalFailureModes?: readonly string[];
  classification: SkillClassification;
  dangerLevel: DangerLevel;
  status: SkillStatus;
  /**
   * Whether this skill maps onto the running bridge today. Some skills
   * (damp, prepare, wave, …) only work on a real G1 because the sim scene
   * doesn't subscribe to `rt/api/sport/request` etc. Catalogue UI greys
   * these out when SIM_MODE != real.
   */
  works: {
    sim: boolean;
    real: boolean;
  };
}

/**
 * Helper to declare a skill with full type inference on the parameters
 * schema. Use TypeBox (`t.Object({...})`) for the params — same validation
 * Elysia uses for routes, and it round-trips cleanly to JSON Schema for the
 * catalogue endpoint.
 */
export function defineSkill<TParams extends TSchema>(
  def: SkillDefinition<TParams>,
): SkillDefinition<TParams> {
  return def;
}

/**
 * Generic shape for the catalogue endpoint — same fields as the definition
 * but with TypeBox unwrapped so consumers (UI, MCP adapter) see plain JSON
 * Schema instead of opaque TypeBox refs.
 */
export interface SkillCatalogueEntry {
  name: string;
  description: string;
  parameters: unknown; // serialised JSON Schema
  preconditions: readonly string[];
  expectedDurationSeconds: number;
  cancellable: boolean;
  cancellationEffect?: string;
  typicalFailureModes: readonly string[];
  classification: SkillClassification;
  dangerLevel: DangerLevel;
  status: SkillStatus;
  works: { sim: boolean; real: boolean };
}

/**
 * Project a SkillDefinition into the wire shape returned by GET /skills.
 * TypeBox schemas are JSON-Schema-compatible at runtime, so they serialise
 * directly when an Elysia handler returns them.
 */
export function toCatalogueEntry<TParams extends TSchema>(
  def: SkillDefinition<TParams>,
): SkillCatalogueEntry {
  return {
    name: def.name,
    description: def.description,
    parameters: def.parameters,
    preconditions: def.preconditions ?? [],
    expectedDurationSeconds: def.expectedDurationSeconds,
    cancellable: def.cancellable,
    cancellationEffect: def.cancellationEffect,
    typicalFailureModes: def.typicalFailureModes ?? [],
    classification: def.classification,
    dangerLevel: def.dangerLevel,
    status: def.status,
    works: def.works,
  };
}

// Re-export `t` for skill files so they have one canonical import path.
export { t };
