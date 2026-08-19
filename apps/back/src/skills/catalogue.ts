/**
 * The skill catalogue, derived from the bridge rather than duplicated here.
 *
 * This used to be 28 hand-written TypeScript files that restated the bridge's
 * tool names, descriptions and parameter schemas. They drifted — silently, and
 * into the LLM's prompt. `say` declared a `voice` parameter the bridge did not
 * accept (the argument evaporated and returned success); `walk_to` and `turn`
 * marked their defaulted parameters required, so the model was told to invent
 * the timeouts that decide how long a humanoid keeps walking; and `say` was
 * still described as a non-functional stub after real TTS shipped. A name-only
 * drift test caught none of it.
 *
 * So the bridge is now the single source of truth: it owns the names, the
 * descriptions, the parameter schemas, and — via `_meta` — the safety metadata
 * that was the only real reason a second catalogue existed. That metadata is
 * knowledge only the bridge has anyway: preconditions are FSM rules,
 * `cancellable` describes how a skill loop is written, `works.real` records
 * whether a human has watched the thing run.
 *
 * The cost is that the catalogue now depends on a process on another machine,
 * reached over Wi-Fi, on a robot that gets power-cycled. That is handled by
 * caching the last good answer and serving it with its age attached — never by
 * inventing a default, because a fabricated catalogue is worse than an absent
 * one.
 */

import type { SkillCatalogueEntry } from "./define";
import { listTools } from "@back/bridge/client";
import { type SkillMeta, parseSkillMeta } from "./meta";

export interface CatalogueSnapshot {
  skills: SkillCatalogueEntry[];
  /** Where this answer came from, so callers never have to guess. */
  source: "bridge" | "cache";
  /** Seconds since the bridge actually answered. 0 when fresh. */
  ageSeconds: number;
  /** Present when the bridge could not be reached on this attempt. */
  error?: string;
}

let cached: { skills: SkillCatalogueEntry[]; at: number } | null = null;

/** Turn one MCP tool into the catalogue shape the rest of `back` already speaks. */
function toEntry(tool: {
  name: string;
  description?: string;
  inputSchema: unknown;
  _meta?: unknown;
}): SkillCatalogueEntry {
  const meta: SkillMeta = parseSkillMeta(tool.name, tool._meta);
  return {
    name: tool.name,
    description: tool.description ?? "",
    parameters: tool.inputSchema,
    preconditions: meta.preconditions,
    expectedDurationSeconds: meta.expected_duration_s,
    cancellable: meta.cancellable,
    typicalFailureModes: meta.typical_failure_modes,
    classification: meta.classification,
    dangerLevel: meta.danger_level,
    status: meta.status,
    works: meta.works,
  };
}

/**
 * Fetch the catalogue, falling back to the last good answer.
 *
 * Deliberately does not throw when the bridge is down: an operator console that
 * goes blank because the robot is asleep is less useful than one that shows
 * what the robot could do, clearly marked stale.
 */
export async function getCatalogue(): Promise<CatalogueSnapshot> {
  try {
    const tools = await listTools();
    const skills = tools
      .map(toEntry)
      .sort((a, b) => a.name.localeCompare(b.name));
    cached = { skills, at: Date.now() };
    return { skills, source: "bridge", ageSeconds: 0 };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    if (cached) {
      return {
        skills: cached.skills,
        source: "cache",
        ageSeconds: Math.round((Date.now() - cached.at) / 1000),
        error: detail,
      };
    }
    // Nothing cached and no bridge: report emptiness honestly rather than
    // serving a guess. A caller that cannot tell "no tools" from "unknown"
    // will eventually treat one as the other.
    return { skills: [], source: "bridge", ageSeconds: 0, error: detail };
  }
}

export async function listSkills(): Promise<SkillCatalogueEntry[]> {
  return (await getCatalogue()).skills;
}

export async function getSkill(
  name: string,
): Promise<SkillCatalogueEntry | undefined> {
  return (await getCatalogue()).skills.find((s) => s.name === name);
}
