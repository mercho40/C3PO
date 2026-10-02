/**
 * /chats — conversation history for the operator console.
 *
 * Every route is scoped to the authenticated user at the query level, not by
 * filtering after the fetch: `getChatWithMessages` and `deleteChat` both take
 * the user id into their WHERE clause, so an id belonging to someone else is
 * indistinguishable from one that doesn't exist. That's deliberate — a 404
 * rather than a 403 avoids confirming that another user's chat exists.
 *
 * Guarded by `{ auth: true }` at the composition root (src/index.ts).
 */

import { Elysia, t } from "elysia";

import {
  deleteChat,
  getChatWithMessages,
  listChats,
  renameChat,
} from "@back/db/chats";
import { betterAuth } from "@back/lib/auth-plugin";

export const chatsRoutes = new Elysia({ prefix: "/chats" })
  .use(betterAuth)
  .get(
    "/",
    async ({ user, query }) => ({
      chats: await listChats(user.id, query.limit ?? 50, query.channel),
    }),
    {
      auth: true,
      query: t.Object({
        limit: t.Optional(t.Number({ minimum: 1, maximum: 200 })),
        // /voice lists only spoken sessions. Filtering in the query rather than
        // in the browser is what lets it say "no hay" truthfully: a client-side
        // filter over the last N chats of any kind could miss older ones.
        channel: t.Optional(t.Union([t.Literal("text"), t.Literal("voice")])),
      }),
      detail: {
        summary: "List the caller's chats, most recently updated first.",
        tags: ["chats"],
      },
    },
  )
  .get(
    "/:id",
    async ({ params, user, status }) => {
      const chat = await getChatWithMessages(params.id, user.id);
      if (!chat) return status(404);
      return chat;
    },
    {
      auth: true,
      params: t.Object({ id: t.String() }),
      detail: {
        summary:
          "Load one chat with its messages in order (UIMessage parts, ready to rehydrate).",
        tags: ["chats"],
      },
    },
  )
  .patch(
    "/:id",
    async ({ params, user, body, status }) => {
      const title = body.title.trim();
      if (!title) return status(422, { error: "empty_title" });
      const row = await renameChat(params.id, user.id, title);
      if (!row) return status(404);
      return row;
    },
    {
      auth: true,
      params: t.Object({ id: t.String() }),
      body: t.Object({ title: t.String({ minLength: 1, maxLength: 120 }) }),
      detail: {
        summary: "Rename one of the caller's chats.",
        tags: ["chats"],
      },
    },
  )
  .delete(
    "/:id",
    async ({ params, user, status }) => {
      const ok = await deleteChat(params.id, user.id);
      if (!ok) return status(404);
      return { deleted: true };
    },
    {
      auth: true,
      params: t.Object({ id: t.String() }),
      detail: {
        summary: "Delete a chat and, by cascade, its messages and tool calls.",
        tags: ["chats"],
      },
    },
  );
