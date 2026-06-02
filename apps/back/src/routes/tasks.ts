/**
 * /tasks routes — cancel + list, proxied to the bridge over MCP.
 *
 * POST /tasks/:task_id/cancel  → bridge `cancel_task` (graceful).
 * GET  /tasks                  → bridge `list_active_tasks`.
 */

import { Elysia, t } from "elysia";

import {
  callTool,
  BridgeUnavailableError,
  BridgeToolError,
} from "../bridge/client";

export const tasksRoutes = new Elysia({ prefix: "/tasks" })
  .post(
    "/:task_id/cancel",
    async ({ params: { task_id }, status }) => {
      try {
        return await callTool("cancel_task", { task_id });
      } catch (err) {
        if (err instanceof BridgeUnavailableError)
          return status(502, { error: "bridge_unavailable", task_id });
        if (err instanceof BridgeToolError)
          return status(502, {
            error: "tool_error",
            task_id,
            detail: err.detail,
          });
        return status(502, { error: "bridge_error", task_id });
      }
    },
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
        summary: "Request graceful cancellation of an in-flight task.",
        tags: ["tasks"],
      },
    },
  )
  .get(
    "/",
    async ({ query, status }) => {
      try {
        return await callTool("list_active_tasks", {
          include_recent: query?.include_recent === "true",
        });
      } catch (err) {
        if (err instanceof BridgeUnavailableError)
          return status(502, { error: "bridge_unavailable" });
        return status(502, { error: "bridge_error" });
      }
    },
    {
      query: t.Object({
        include_recent: t.Optional(
          t.String({ enum: ["true", "false"], default: "false" }),
        ),
      }),
      detail: {
        summary: "List active tasks (and optionally recently-completed ones).",
        tags: ["tasks"],
      },
    },
  );
