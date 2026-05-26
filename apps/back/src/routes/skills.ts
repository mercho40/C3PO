/**
 * /skills routes — catalogue + invoke + dry-run + cancel.
 *
 * Today: GET /skills and GET /skills/:name return the catalogue.
 * Invocation endpoints are placeholders that return 501 until the
 * bridge↔back WS link lands (Phase 3 of plan — `bridge/client.ts`).
 */

import { Elysia, t } from "elysia";

import { getSkill, listSkills } from "../skills";

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
    ({ params: { name }, body, status }) => {
      const skill = getSkill(name);
      if (!skill) return status(404, { error: "skill_not_found", name });
      // Bridge↔back WS not yet wired. Phase 3 of plan.
      return status(501, {
        error: "not_implemented",
        message:
          "Direct invocation through Elysia is not wired yet. Drive the bridge via MCP " +
          "(`mcp__c3po-bridge__" +
          name +
          "`) for now; this endpoint becomes a thin WS proxy in Phase 3.",
        skill: name,
        params_received: body,
      });
    },
    {
      params: t.Object({ name: t.String() }),
      body: t.Record(t.String(), t.Any()),
      detail: {
        summary:
          "Invoke a skill (placeholder — returns 501 until bridge WS lands).",
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
