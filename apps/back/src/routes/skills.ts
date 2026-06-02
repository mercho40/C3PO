/**
 * /skills routes — catalogue + invoke + dry-run.
 *
 * GET /skills and GET /skills/:name return the catalogue. POST
 * /skills/:name/invoke validates the body against the skill's TypeBox schema
 * and dispatches to the bridge over MCP (`../bridge/client`). POST
 * /skills/:name/dry-run is still a placeholder (501) — simulated results land
 * with the confirmation flow (SCRUM-32).
 */

import { Elysia, t } from "elysia";
import { Value } from "@sinclair/typebox/value";

import { getSkill, listSkills } from "../skills";
import {
  callTool,
  BridgeUnavailableError,
  BridgeToolError,
} from "../bridge/client";

export const skillsRoutes = new Elysia({ prefix: "/skills" })
  .get(
    "/",
    () => ({
      count: listSkills().length,
      skills: listSkills(),
    }),
    {
      detail: {
        summary: "List the full skill catalogue.",
        tags: ["skills"],
      },
    },
  )
  .get(
    "/:name",
    ({ params: { name }, status }) => {
      const skill = getSkill(name);
      if (!skill) return status(404, { error: "skill_not_found", name });
      return skill;
    },
    {
      params: t.Object({ name: t.String() }),
      detail: {
        summary: "Get a single skill definition.",
        tags: ["skills"],
      },
    },
  )
  .post(
    "/:name/invoke",
    async ({ params: { name }, body, status }) => {
      const skill = getSkill(name);
      if (!skill) return status(404, { error: "skill_not_found", name });

      // Schema validation (SCRUM-57, layer 1): fill declared defaults, then
      // check the body against the skill's TypeBox params before dispatch.
      const args = Value.Default(skill.parameters, {
        ...(body as Record<string, unknown>),
      }) as Record<string, unknown>;
      if (!Value.Check(skill.parameters, args)) {
        return status(422, {
          error: "invalid_params",
          name,
          issues: [...Value.Errors(skill.parameters, args)].map((e) => ({
            path: e.path,
            message: e.message,
          })),
        });
      }

      try {
        return await callTool(name, args);
      } catch (err) {
        if (err instanceof BridgeUnavailableError)
          return status(502, { error: "bridge_unavailable", name });
        if (err instanceof BridgeToolError)
          return status(502, { error: "tool_error", name, detail: err.detail });
        return status(502, { error: "bridge_error", name });
      }
    },
    {
      params: t.Object({ name: t.String() }),
      body: t.Record(t.String(), t.Any()),
      detail: {
        summary: "Invoke a skill on the bridge (typed, validated dispatch).",
        tags: ["skills"],
      },
    },
  )
  .post(
    "/:name/dry-run",
    ({ params: { name }, status }) => {
      const skill = getSkill(name);
      if (!skill) return status(404, { error: "skill_not_found", name });
      return status(501, {
        error: "not_implemented",
        message: "Dry-run dispatch lands with the bridge WS in Phase 3.",
        skill: name,
      });
    },
    {
      params: t.Object({ name: t.String() }),
      detail: {
        summary:
          "Dry-run a skill (placeholder — returns 501 until bridge WS lands).",
        tags: ["skills"],
      },
    },
  );
