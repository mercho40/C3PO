/**
 * /agent — chat with the internal LLM agent that drives the robot.
 *
 * POST /agent { id, messages: UIMessage[] } (the `@ai-sdk/svelte` Chat shape)
 * runs an operator turn and STREAMS the result back as a UI message stream
 * (`toUIMessageStreamResponse()`): the model's tokens, its tool calls (skills
 * dispatched to the bridge), and tool results arrive incrementally.
 *
 * Only the LAST message is taken from the client, reduced to user text; the
 * rest of the history comes from the database (see `trustedHistory`).
 *
 * The turn is also persisted. `id` is the chat id — generated client-side so
 * the first streamed turn and the row it writes to agree without a round-trip.
 * The incoming user message is saved before streaming starts (so an aborted or
 * failed turn still leaves a record of what was asked), and the assistant
 * message is saved from `onEnd` once the stream completes.
 *
 * This is the backend taking over as the conversation host (docs/ARCHITECTURE.md §3) — the
 * same skill registry the external MCP path uses, now driven server-side.
 *
 * Guarded by `{ auth: true }` at the composition root (src/index.ts). Chats are
 * scoped to the authenticated user: `ensureChat` refuses an id owned by someone
 * else rather than writing into their conversation.
 */

import { Elysia, t } from "elysia";
import { createIdGenerator, type UIMessage } from "ai";

import { runAgentChat } from "#back/agent/runtime.js";
import {
  appendMessage,
  ensureChat,
  getChatWithMessages,
  titleFromParts,
} from "#back/db/chats.js";
import { betterAuth } from "#back/lib/auth-plugin.js";

/**
 * Ids for the assistant messages we persist.
 *
 * Not cosmetic — without it `toUIMessageStreamResponse` leaves
 * `responseMessage.id` as the empty string ("no message ID will be set for the
 * response message"), and every assistant reply then upserts onto the same
 * primary key. One row, in whichever chat happened to finish first, silently
 * overwritten by every reply after it: reloading a conversation gave you back
 * your own messages and none of the robot's.
 *
 * Passing it also puts the id in the stream's `start` chunk, so the client's
 * copy of the message and the row on disk agree.
 *
 * That agreement is NOT enough to make a retry rewrite a reply in place:
 * `regenerate()` drops the failed assistant message on the client and the
 * server mints a new id, so the partial reply `onEnd` already saved stayed in
 * the history beside the retried one. The console therefore sends the failed
 * message's id as `replaceMessageId`, and the retry reuses it — `appendMessage`
 * upserts by id, scoped to this chat, so the row (and its place in the
 * sequence) is overwritten rather than duplicated.
 */
const generateMessageId = createIdGenerator({ prefix: "msg", size: 16 });

/** The first response id is `replaceId` when given; every later one is fresh. */
function messageIdsFor(replaceId: string | undefined): () => string {
  let pending = replaceId;
  return () => {
    const next = pending ?? generateMessageId();
    pending = undefined;
    return next;
  };
}

type Incoming = { id: string; role: string; parts: unknown[] };

/**
 * What a client may author: user text. Anything else in a message (tool parts,
 * files, provider metadata) is dropped, and a non-user role yields null.
 */
export function userText(m: Incoming | undefined): UIMessage | null {
  if (m?.role !== "user") return null;
  const parts = m.parts.flatMap((p) =>
    typeof p === "object" &&
    p !== null &&
    (p as { type?: unknown }).type === "text" &&
    typeof (p as { text?: unknown }).text === "string"
      ? [{ type: "text" as const, text: (p as { text: string }).text }]
      : [],
  );
  return parts.length > 0 ? { id: m.id, role: "user", parts } : null;
}

/**
 * The history a turn runs on. A client could otherwise forge assistant turns
 * or tool results (a "confirmed" result, a reply steering the model), so:
 * with a persisted chat it is the database's rows — the server wrote every
 * assistant row, and user rows are re-reduced to text because older rows were
 * stored verbatim from the client; without one, only the client's user text.
 * `replaceId` drops the failed reply a retry is about to overwrite, as the
 * client's `regenerate()` does.
 */
export function trustedHistory(
  client: Incoming[],
  stored: Incoming[] | null,
  replaceId?: string,
): UIMessage[] {
  if (!stored) return client.flatMap((m) => userText(m) ?? []);
  return stored.flatMap((m) => {
    if (m.id === replaceId) return [];
    if (m.role === "user") return userText(m) ?? [];
    return [{ id: m.id, role: m.role, parts: m.parts } as UIMessage];
  });
}

export const agentRoutes = new Elysia({ prefix: "/agent" })
  .use(betterAuth)
  .post(
    "/",
    async ({ body, user, session, status, request }) => {
      // 1.4: driving the robot through a model is an admin's job, like /invoke.
      if (user.role !== "admin")
        return status(403, { error: "admin_required" });
      // The one thing taken from the client's history: its new user message.
      const latest = userText(body.messages[body.messages.length - 1]);
      if (!latest) return status(400, { error: "last_message_not_user_text" });

      // Persistence is best-effort around the stream: a database problem should
      // degrade this to an unsaved conversation, not refuse to drive the robot.
      let chatId: string | null = null;
      let stored: Incoming[] | null = null;
      if (body.id) {
        const owned = await ensureChat({
          id: body.id,
          userId: user.id,
          // Better Auth's organization plugin tracks the active org on the
          // session, not the user — a user can belong to several.
          organizationId: session.activeOrganizationId ?? null,
          title: titleFromParts(latest.parts),
        });
        if (!owned) return status(403);
        // Voice transcripts are deliberately read-only in typed chat. Without
        // this server-side check a caller could bypass the UI and merge channels.
        if (owned.channel !== "text") return status(409);
        chatId = owned.id;

        await appendMessage({
          id: latest.id,
          chatId,
          role: "user",
          parts: latest.parts,
        });
        // Read after the append, so it ends in the message just saved.
        stored = (await getChatWithMessages(chatId, user.id))?.messages ?? [];
      }
      const messages = trustedHistory(
        body.messages,
        stored,
        chatId ? body.replaceMessageId : undefined,
      );

      // `userId` attributes the audit rows even when the chat is not persisted
      // (no chat id to look the owner up from).
      const result = await runAgentChat(messages, {
        chatId,
        userId: user.id,
        mode: body.mode === "voice" ? "voice" : "operator",
        // Aborts when the browser goes away (Stop, or leaving /chat). `onEnd`
        // below still persists what the aborted turn produced.
        abortSignal: request.signal,
      });

      return result.toUIMessageStreamResponse({
        originalMessages: messages,
        // Only meaningful with a persisted chat: without one there is no row
        // to overwrite, and a caller-chosen id buys nothing.
        generateMessageId: messageIdsFor(
          chatId ? body.replaceMessageId : undefined,
        ),
        // `onFinish` is the deprecated spelling of this callback in ai@7.
        onEnd: async ({ responseMessage, isAborted }) => {
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
      // messages, and TypeBox lets the extra fields through. Only the shape is
      // checked here: what is trusted is `trustedHistory`'s call.
      body: t.Object({
        id: t.Optional(t.String()),
        messages: t.Array(
          t.Object({
            id: t.String({ minLength: 1, maxLength: 64 }),
            role: t.Union([
              t.Literal("user"),
              t.Literal("assistant"),
              t.Literal("system"),
            ]),
            parts: t.Array(t.Unknown()),
          }),
          { minItems: 1 },
        ),
        // On a retry: the failed assistant message to overwrite (see
        // `messageIdsFor`). A foreign id is harmless — `appendMessage` refuses
        // to update another chat's row and writes a fresh one instead.
        replaceMessageId: t.Optional(t.String({ maxLength: 64 })),
        // The headset's push-to-talk sends "voice": transcribed speech gets the
        // voice-mode toolset (no high-danger T1, no damp). A client can only ask
        // for the stricter mode; anything else stays "operator".
        mode: t.Optional(t.String({ maxLength: 16 })),
      }),
      detail: {
        summary:
          "Stream an internal-agent chat turn (the model drives the skill registry).",
        tags: ["agent"],
      },
    },
  );
