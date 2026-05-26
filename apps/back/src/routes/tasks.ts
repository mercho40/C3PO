/**
 * /tasks routes — cancel + list.
 *
 * Placeholder: returns 501 until the bridge↔back WS lands (Phase 3).
 * Today, drive these via MCP (`cancel_task`, `list_active_tasks`).
 */

import { Elysia, t } from "elysia";

export const tasksRoutes = new Elysia({ prefix: "/tasks" })
  .post(
    "/:task_id/cancel",
    ({ params: { task_id }, status }) =>
      status(501, {
        error: "not_implemented",
        message:
          "Cancel through Elysia not wired yet. Use mcp__c3po-bridge__cancel_task " +
          "in Claude Code for now; this endpoint becomes a thin WS proxy in Phase 3.",
        task_id,
      }),
    {
      params: t.Object({ task_id: t.String() }),
      body: t.Optional(
        t.Object({
          mode: t.Optional(
            t.Union([t.Literal("graceful"), t.Literal("estop")], {
              default: "graceful",
            }),
          ),
        }),
      ),
      detail: {
        summary:
          "Cancel a task (placeholder — returns 501 until bridge WS lands).",
        tags: ["tasks"],
      },
    },
  )
  .get(
    "/",
    ({ query, status }) =>
      status(501, {
        error: "not_implemented",
        message:
          "Use mcp__c3po-bridge__list_active_tasks for now; this becomes a thin WS proxy in Phase 3.",
        include_recent: query?.include_recent === "true",
      }),
    {
      query: t.Object({
        include_recent: t.Optional(
          t.String({ enum: ["true", "false"], default: "false" }),
        ),
      }),
      detail: {
        summary:
          "List active tasks (placeholder — returns 501 until bridge WS lands).",
        tags: ["tasks"],
      },
    },
  );
