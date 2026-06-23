/**
 * /agent — drive the robot by talking to the internal Claude agent.
 *
 * POST /agent { "message": "..." } runs one operator turn and STREAMS the
 * result back as a UI message stream (`toUIMessageStreamResponse()`): Claude's
 * tokens, its tool calls (skills dispatched to the bridge), and tool results
 * arrive incrementally. The SvelteKit console's `useChat` consumes this format
 * directly; `curl -N` shows the raw SSE events.
 *
 * This is the backend taking over as the conversation host (SPEC §12.1) — the
 * same skill registry the external MCP path uses, now driven server-side.
 *
 * NOTE: like /skills and /state today, this is currently unguarded. Before real
 * use it should carry `{ auth: true }` (ideally admin/org-scoped) — it can both
 * move the robot and spend Anthropic tokens.
 */

import { Elysia, t } from "elysia";

import { runAgentStream } from "@back/agent/runtime";

export const agentRoutes = new Elysia({ prefix: "/agent" }).post(
  "/",
  ({ body }) =>
    runAgentStream(body.message).toUIMessageStreamResponse({
      // Surface the real cause (e.g. missing ANTHROPIC_API_KEY, rate limit)
      // instead of the default masked "An error occurred."
      onError: (error) => (error instanceof Error ? error.message : String(error)),
    }),
  {
    body: t.Object({
      message: t.String({ minLength: 1, maxLength: 4000 }),
    }),
    detail: {
      summary: "Run one internal-agent turn (Claude drives the skill registry), streamed.",
      tags: ["agent"],
    },
  },
);
