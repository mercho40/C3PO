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
import { listTools } from "#back/bridge/client.js";
import { type SkillMeta, parseSkillMeta } from "./meta";

export interface CatalogueSnapshot {
  skills: SkillCatalogueEntry[];
  /** Where this answer came from, so callers never have to guess. */
  source: "bridge" | "cache";
  /** Seconds since the bridge actually answered. 0 when fresh. */
  ageSeconds: number;
  /** Present when the bridge could not be reached on this attempt. */
  error?: string;
  /**
   * Tools the bridge offered but whose `_meta` could not be read, so they
   * were excluded rather than served with a guessed danger level. Distinct
   * from `error`, which means the bridge itself was unreachable.
   */
  rejected?: string[];
}

let cached: {
  skills: SkillCatalogueEntry[];
  rejected: string[];
  at: number;
} | null = null;

/** How long a fresh answer is reused without asking the bridge again. */
const FRESH_MS = 5000;
let freshUntil = 0;
/** Set by `invalidateCatalogue`: the next `getSkill` waits for the bridge. */
let mustRefetch = false;
/** The `tools/list` in flight, shared by every caller that needs one. */
let inflight: Promise<CatalogueSnapshot> | null = null;

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
  // A very short TTL. The catalogue only changes when the bridge restarts, but
  // every /skills/:name/invoke reads it — and the VR walk loop invokes about
  // once a second, which was one bridge `tools/list` round trip per step.
  if (cached && Date.now() < freshUntil) {
    return {
      skills: cached.skills,
      source: "bridge",
      ageSeconds: Math.round((Date.now() - cached.at) / 1000),
      ...(cached.rejected.length ? { rejected: cached.rejected } : {}),
    };
  }
  return refreshCatalogue();
}

/**
 * One `tools/list` at a time. Without this, every invoke that found the cache
 * stale started its own, and they finished in whatever order the bridge
 * answered them.
 */
function refreshCatalogue(): Promise<CatalogueSnapshot> {
  inflight ??= fetchCatalogue().finally(() => {
    inflight = null;
  });
  return inflight;
}

async function fetchCatalogue(): Promise<CatalogueSnapshot> {
  try {
    const tools = await listTools();

    // Per-tool, deliberately. `parseSkillMeta` throws on unreadable metadata
    // and that is right — guessing "low" for a tool that can walk into
    // someone is the wrong direction to guess. But a single `tools.map()`
    // made that rejection batch-fatal: one new or version-skewed tool with a
    // malformed `_meta` threw, the catch below discarded EVERY skill, and
    // with nothing cached `getSkill("stop_everything")` returned undefined —
    // so the dashboard's PARAR button answered 404 while the bridge and the
    // e-stop were both perfectly healthy. Skip the unreadable tool; keep the
    // rest. Excluding it still never guesses its danger level.
    const skills: SkillCatalogueEntry[] = [];
    const rejected: string[] = [];
    for (const tool of tools) {
      try {
        skills.push(toEntry(tool));
      } catch (err) {
        rejected.push(tool.name);
        console.error(
          `[catalogue] dropping tool ${tool.name}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    }
    skills.sort((a, b) => a.name.localeCompare(b.name));

    cached = { skills, rejected, at: Date.now() };
    freshUntil = cached.at + FRESH_MS;
    mustRefetch = false;
    return {
      skills,
      source: "bridge",
      ageSeconds: 0,
      ...(rejected.length ? { rejected } : {}),
    };
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

/** Forget the fresh answer, so the next read asks the bridge. */
export function invalidateCatalogue(): void {
  freshUntil = 0;
  mustRefetch = true;
}

export async function listSkills(): Promise<SkillCatalogueEntry[]> {
  return (await getCatalogue()).skills;
}

/**
 * The lookup every `/skills/:name/invoke` makes, so it never waits on the
 * bridge when there is an answer to serve.
 *
 * It used to await `getCatalogue()`, which every 5 s meant a `tools/list` round
 * trip in front of the command. On the robot (2026-10-15) that held walk
 * presses for up to ~0.9 s, so a press and its 900 ms repeat reached the robot
 * 70 ms apart, and a command held there could arrive after the walk-pad stop
 * sent after it — walking on after the operator let go. A stale answer is
 * served at once and refreshed behind it; the bridge still validates every
 * call, so a tool that has since gone fails there. Only a cold start (nothing
 * cached) or an explicit `invalidateCatalogue()` waits.
 */
export async function getSkill(
  name: string,
): Promise<SkillCatalogueEntry | undefined> {
  if (cached && !mustRefetch) {
    if (Date.now() >= freshUntil) void refreshCatalogue();
    return cached.skills.find((s) => s.name === name);
  }
  return (await getCatalogue()).skills.find((s) => s.name === name);
}
