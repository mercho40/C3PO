/**
 * /agent — chat with the internal Claude agent that drives the robot.
 *
 * POST /agent { "messages": UIMessage[] } (the `@ai-sdk/svelte` Chat / useChat
 * shape) runs an operator turn and STREAMS the result back as a UI message
 * stream (`toUIMessageStreamResponse()`): Claude's tokens, its tool calls
 * (skills dispatched to the bridge), and tool results arrive incrementally.
 *
 * This is the backend taking over as the conversation host (SPEC §12.1) — the
 * same skill registry the external MCP path uses, now driven server-side.
 *
 * NOTE: like /skills and /state today, this is currently unguarded. Before real
 * use it should carry `{ auth: true }` (ideally admin/org-scoped) — it can both
 * move the robot and spend Anthropic tokens.
 */

import { Elysia, t } from "elysia";
import type { UIMessage } from "ai";

import { runAgentChat } from "@back/agent/runtime";

export const agentRoutes = new Elysia({ prefix: "/agent" }).post(
  "/",
  async ({ body }) =>
    (await runAgentChat(body.messages as UIMessage[])).toUIMessageStreamResponse({
      // Surface the real cause (e.g. missing ANTHROPIC_API_KEY, rate limit)
      // instead of the default masked "An error occurred."
      onError: (error) =>
        error instanceof Error ? error.message : String(error),
    }),
  {
    // The chat client posts { messages, id, trigger, ... }; we only need
    // messages, and TypeBox lets the extra fields through.
    body: t.Object({ messages: t.Array(t.Any()) }),
    detail: {
      summary:
        "Stream an internal-agent chat turn (Claude drives the skill registry).",
      tags: ["agent"],
    },
  },
);
