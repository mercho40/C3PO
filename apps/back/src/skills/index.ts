/**
 * The skill catalogue — now DERIVED from the bridge, not duplicated here.
 *
 * This used to be the "single source of truth for what the robot can do,
 * expressed in TypeScript": 28 hand-written skill files restating the bridge's
 * names, descriptions and parameter schemas. Its own header already admitted
 * the problem — "the bridge's MCP server exposes a parallel surface … long-term
 * these converge through a shared generator". No generator was needed in the
 * end. MCP is already a capability-catalogue protocol: `listTools()` returns
 * the name, description and JSON Schema, and the safety metadata rides
 * alongside on `_meta`.
 *
 * The duplication was not harmless. It drifted into the LLM's prompt: a `voice`
 * parameter the bridge did not accept (silently discarded, returning success),
 * defaulted parameters marked required so the model invented the timeouts that
 * govern how long a humanoid walks, and `say` advertised as a non-functional
 * stub for hours after real TTS shipped.
 *
 * Consumers: `GET /skills` and the internal agent's tool set + system prompt.
 * Both are async now, because the catalogue lives on the other side of a
 * network hop.
 */

export {
  getCatalogue,
  getSkill,
  listSkills,
  type CatalogueSnapshot,
} from "./catalogue";

export {
  InvalidSkillMetaError,
  SkillMetaSchema,
  ToolMetaSchema,
  parseSkillMeta,
  type SkillMeta,
} from "./meta";

export type {
  DangerLevel,
  SkillCatalogueEntry,
  SkillClassification,
  SkillStatus,
} from "./define";
