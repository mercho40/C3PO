/**
 * The `_meta.c3po` contract the bridge attaches to every MCP tool.
 *
 * This is the trust boundary. The catalogue now comes from another process, on
 * another machine, over the network — so it is validated once, here, and typed
 * everywhere after. `Static<typeof SkillMetaSchema>` gives the compiler the
 * same shape `Value.Check` enforces at runtime, so there is exactly one
 * definition of what valid metadata means rather than a schema and an interface
 * that can drift apart.
 *
 * Validation is not defensive decoration. A bridge running older code will
 * return tools whose metadata is missing or shaped differently, and the failure
 * we care about is `works.real` being absent and read as truthy — which would
 * mean the agent treating untested motion as safe. Anything that fails this
 * check is refused rather than defaulted.
 */

import { type Static, t } from "elysia";
import { Value } from "@sinclair/typebox/value";

export const SkillMetaSchema = t.Object({
  classification: t.Union([
    t.Literal("introspection"),
    t.Literal("locomotion"),
    t.Literal("posture"),
    t.Literal("gesture"),
    t.Literal("perception"),
    t.Literal("speech"),
    t.Literal("memory"),
    t.Literal("safety"),
    t.Literal("task"),
  ]),
  danger_level: t.Union([
    t.Literal("low"),
    t.Literal("medium"),
    t.Literal("high"),
  ]),
  status: t.Union([t.Literal("real"), t.Literal("stub"), t.Literal("planned")]),
  cancellable: t.Boolean(),
  expected_duration_s: t.Number(),
  /**
   * Whether the behaviour has actually been observed on that target. Not
   * "should work" — several skills are accepted by the firmware with rpc code 0
   * and have never been seen to do anything.
   */
  works: t.Object({ sim: t.Boolean(), real: t.Boolean() }),
  preconditions: t.Array(t.String()),
  typical_failure_modes: t.Array(t.String()),
});

export type SkillMeta = Static<typeof SkillMetaSchema>;

/** The envelope the bridge sends: namespaced so it cannot collide with MCP's own keys. */
export const ToolMetaSchema = t.Object({ c3po: SkillMetaSchema });

export class InvalidSkillMetaError extends Error {
  constructor(
    readonly toolName: string,
    readonly detail: string,
  ) {
    super(`invalid _meta for tool ${toolName}: ${detail}`);
    this.name = "InvalidSkillMetaError";
  }
}

/**
 * Validate and narrow a tool's `_meta`.
 *
 * Throws rather than returning a default. A tool whose metadata we cannot read
 * is a tool whose danger level we do not know, and guessing "low" is precisely
 * the wrong direction to guess for a machine that can walk into someone.
 */
export function parseSkillMeta(toolName: string, raw: unknown): SkillMeta {
  if (!Value.Check(ToolMetaSchema, raw)) {
    const first = [...Value.Errors(ToolMetaSchema, raw)][0];
    throw new InvalidSkillMetaError(
      toolName,
      first
        ? `${first.path || "/"}: ${first.message}`
        : "does not match schema",
    );
  }
  return raw.c3po;
}
