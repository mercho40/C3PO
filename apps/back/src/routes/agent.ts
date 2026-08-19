/**
 * /agent — chat with the internal LLM agent that drives the robot.
 *
 * POST /agent { id, messages: UIMessage[] } (the `@ai-sdk/svelte` Chat shape)
 * runs an operator turn and STREAMS the result back as a UI message stream
 * (`toUIMessageStreamResponse()`): the model's tokens, its tool calls (skills
 * dispatched to the bridge), and tool results arrive incrementally.
 *
 * The turn is also persisted. `id` is the chat id — generated client-side so
 * the first streamed turn and the row it writes to agree without a round-trip.
 * The incoming user message is saved before streaming starts (so an aborted or
 * failed turn still leaves a record of what was asked), and the assistant
 * message is saved from `onFinish` once the stream completes.
 *
 * This is the backend taking over as the conversation host (docs/ARCHITECTURE.md §3) — the
 * same skill registry the external MCP path uses, now driven server-side.
 *
 * Guarded by `{ auth: true }` at the composition root (src/index.ts). Chats are
 * scoped to the authenticated user: `ensureChat` refuses an id owned by someone
 * else rather than writing into their conversation.
 */

import { Elysia, t } from "elysia";
import type { UIMessage } from "ai";

import { runAgentChat } from "@back/agent/runtime";
import { appendMessage, ensureChat, titleFromParts } from "@back/db/chats";
import { betterAuth } from "@back/lib/auth-plugin";

export const agentRoutes = new Elysia({ prefix: "/agent" })
  .use(betterAuth)
  .post(
    "/",
    async ({ body, user, session, status }) => {
      const messages = body.messages as UIMessage[];
      const latest = messages[messages.length - 1];

      // Persistence is best-effort around the stream: a database problem should
      // degrade this to an unsaved conversation, not refuse to drive the robot.
      let chatId: string | null = null;
      if (body.id && latest) {
        const owned = await ensureChat({
          id: body.id,
          userId: user.id,
          // Better Auth's organization plugin tracks the active org on the
          // session, not the user — a user can belong to several.
          organizationId: session.activeOrganizationId ?? null,
          title: titleFromParts(latest.parts ?? []),
        });
        if (!owned) return status(403);
        chatId = owned.id;

        if (latest.role === "user") {
          await appendMessage({
            id: latest.id,
            chatId,
            role: "user",
            parts: latest.parts ?? [],
          });
        }
      }

      const result = await runAgentChat(messages, { chatId });

      return result.toUIMessageStreamResponse({
        originalMessages: messages,
        onFinish: async ({ responseMessage, isAborted }) => {
          // An aborted turn still persists what was generated — a half-finished
          // sequence of tool calls is exactly the history an operator needs.
          if (!chatId) return;
          await appendMessage({
            id: responseMessage.id,
            chatId,
            role: "assistant",
            parts: responseMessage.parts ?? [],
          });
          if (isAborted) console.warn("agent turn aborted", { chatId });
        },
        // Surface the real cause (e.g. missing AGENT_API_KEY, an unreachable
        // gateway) instead of the default masked "An error occurred."
        onError: (error) =>
          error instanceof Error ? error.message : String(error),
      });
    },
    {
      auth: true,
      // The chat client posts { id, messages, trigger, ... }; we need id and
      // messages, and TypeBox lets the extra fields through.
      body: t.Object({
        id: t.Optional(t.String()),
        messages: t.Array(t.Any()),
      }),
      detail: {
        summary:
          "Stream an internal-agent chat turn (the model drives the skill registry).",
        tags: ["agent"],
      },
    },
  );
