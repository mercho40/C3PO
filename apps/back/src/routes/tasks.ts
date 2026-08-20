/**
 * /tasks routes — cancel + list, proxied to the bridge over MCP.
 *
 * POST /tasks/:task_id/cancel  → bridge `cancel_task` (graceful), or
 *   `stop_everything` (global e-stop) when `mode: "estop"` is requested —
 *   `cancel_task` itself has no estop variant, so `mode: "estop"` used to be
 *   silently downgraded to a plain graceful cancel. `stop_everything` is a
 *   safe superset (it cancels every task, including this one, plus the
 *   real-hardware damp fallback) so routing estop through it is correct even
 *   though it's coarser than a hypothetical per-task estop.
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
    async ({ params: { task_id }, body, status }) => {
      const mode = body?.mode ?? "graceful";
      try {
        if (mode === "estop") {
          const raw = await callTool("stop_everything", {});
          // Bridge tools always return a dict today, but nothing at the
          // type level guarantees it -- spreading a non-object (e.g. a bare
          // string) into a response object silently produces garbage
          // indexed keys instead of erroring, so guard explicitly.
          const result =
            raw !== null && typeof raw === "object" && !Array.isArray(raw)
              ? (raw as Record<string, unknown>)
              : { raw };
          return { mode, task_id, ...result };
        }
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
