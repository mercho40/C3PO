/**
 * Chat persistence — conversations with the internal agent, and the audit
 * trail of every skill those conversations dispatched to the robot.
 *
 * Two things are stored per turn and they are deliberately not the same data:
 *
 *   `chat_message.parts` is the AI SDK `UIMessage.parts` array verbatim, so
 *   reloading a chat reproduces exactly what was streamed — text, reasoning
 *   and tool-call cards in order — rather than a flattened transcript.
 *
 *   `tool_call_log` is a queryable record of what the robot was actually told
 *   to do. It exists separately because "what did the robot do at 14:05, and
 *   did it fail" is a question you ask *without* knowing which chat caused it,
 *   and because MCP clients can drive skills with no chat behind them at all.
 */

import { and, desc, eq, sql } from "drizzle-orm";

import { db } from "./drizzle";
import { chat, chatMessage, toolCallLog } from "./schema";

/** Longest chat title we derive from the opening message. */
const TITLE_MAX = 80;

/** Derive a human-readable title from the first user message's text parts. */
export function titleFromParts(parts: unknown[]): string | null {
  const text = (Array.isArray(parts) ? parts : [])
    .filter(
      (p): p is { type: "text"; text: string } =>
        typeof p === "object" &&
        p !== null &&
        (p as { type?: unknown }).type === "text" &&
        typeof (p as { text?: unknown }).text === "string",
    )
    .map((p) => p.text)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return null;
  return text.length > TITLE_MAX ? `${text.slice(0, TITLE_MAX - 1)}…` : text;
}

/**
 * Return the caller's chat, or create it with the given id.
 *
 * The client generates the chat id so the first streamed turn and the row it
 * persists to agree without a round-trip. Ownership is re-checked on every
 * call: a caller passing someone else's id must not silently start writing
 * into their conversation, so we scope by `userId` and create only when the id
 * is genuinely unused.
 */
export async function ensureChat(opts: {
  id: string;
  userId: string;
  organizationId?: string | null;
  title?: string | null;
  channel?: "text" | "voice";
}): Promise<{ id: string; created: boolean; channel: "text" | "voice" } | null> {
  const existing = await db.query.chat.findFirst({
    where: eq(chat.id, opts.id),
    columns: { id: true, userId: true, channel: true },
  });

  if (existing) {
    // Someone else's chat (or an id collision) — refuse rather than write into it.
    return existing.userId === opts.userId
      ? { id: existing.id, created: false, channel: existing.channel }
      : null;
  }

  await db.insert(chat).values({
    id: opts.id,
    userId: opts.userId,
    organizationId: opts.organizationId ?? null,
    title: opts.title ?? null,
    channel: opts.channel ?? "text",
  });
  return { id: opts.id, created: true, channel: opts.channel ?? "text" };
}

/**
 * Append a message, assigning the next sequence number for the chat.
 *
 * Ordering uses `seq` rather than `created_at` because a turn's user and
 * assistant rows can land on the same timestamp, which would render history
 * out of order. Upserts on the message id so a retried stream doesn't
 * duplicate a turn.
 *
 * The upsert only ever rewrites a row belonging to *this* chat. It used to
 * rewrite any row with a matching id, which is how a caller passing a blank or
 * borrowed id could silently redirect a reply into someone else's
 * conversation — the message vanished from the chat that produced it and
 * corrupted the one that didn't. A conflict outside this chat falls through to
 * a fresh id instead, so the worst case is a duplicate rather than a loss.
 */
export async function appendMessage(opts: {
  id?: string;
  chatId: string;
  role: "user" | "assistant" | "system";
  parts: unknown[];
}): Promise<string> {
  // `||`, not `??`: an empty string is a missing id, not an id. The AI SDK
  // hands one out for the response message unless the stream is given a
  // `generateMessageId` (see routes/agent.ts).
  const id = opts.id || crypto.randomUUID();

  const write = (messageId: string) =>
    db
      .insert(chatMessage)
      .values({
        id: messageId,
        chatId: opts.chatId,
        role: opts.role,
        parts: opts.parts,
        // Computed in the INSERT so concurrent appends can't both read the same
        // max and collide; the unique (chat_id, seq) index is the backstop.
        seq: sql`(select coalesce(max(${chatMessage.seq}), 0) + 1 from ${chatMessage} where ${chatMessage.chatId} = ${opts.chatId})`,
      })
      .onConflictDoUpdate({
        target: chatMessage.id,
        set: { parts: opts.parts },
        setWhere: eq(chatMessage.chatId, opts.chatId),
      })
      .returning({ id: chatMessage.id });

  // No row back means the id exists but belongs to another chat, so `setWhere`
  // held the update back. Keep the message; give it an id of our own.
  let finalId = id;
  if ((await write(id)).length === 0) {
    finalId = crypto.randomUUID();
    await write(finalId);
  }

  await db
    .update(chat)
    .set({ updatedAt: new Date() })
    .where(eq(chat.id, opts.chatId));

  return finalId;
}

/** Record one dispatched skill. Never throws — auditing must not break a turn. */
export async function logToolCall(opts: {
  chatId?: string | null;
  messageId?: string | null;
  skillName: string;
  params?: Record<string, unknown>;
  result?: unknown;
  status: "ok" | "error";
  error?: string | null;
  durationMs?: number;
}): Promise<void> {
  try {
    await db.insert(toolCallLog).values({
      chatId: opts.chatId ?? null,
      messageId: opts.messageId ?? null,
      skillName: opts.skillName,
      params: opts.params ?? null,
      result: (opts.result ?? null) as never,
      status: opts.status,
      error: opts.error ?? null,
      durationMs: opts.durationMs ?? null,
    });
  } catch (err) {
    // A failed audit write must not abort a robot command that already ran.
    console.error("tool_call_log insert failed", err);
  }
}

/** The caller's chats, most recently updated first. */
export async function listChats(userId: string, limit = 50) {
  return db
    .select({
      id: chat.id,
      title: chat.title,
      channel: chat.channel,
      createdAt: chat.createdAt,
      updatedAt: chat.updatedAt,
    })
    .from(chat)
    .where(eq(chat.userId, userId))
    .orderBy(desc(chat.updatedAt))
    .limit(limit);
}

/** One chat with its messages in order, or null if it isn't the caller's. */
export async function getChatWithMessages(id: string, userId: string) {
  const row = await db.query.chat.findFirst({
    where: and(eq(chat.id, id), eq(chat.userId, userId)),
    columns: { id: true, title: true, channel: true, createdAt: true, updatedAt: true },
  });
  if (!row) return null;

  const messages = await db
    .select({
      id: chatMessage.id,
      role: chatMessage.role,
      parts: chatMessage.parts,
      createdAt: chatMessage.createdAt,
    })
    .from(chatMessage)
    .where(eq(chatMessage.chatId, id))
    .orderBy(chatMessage.seq);

  return { ...row, messages };
}

/** Delete a chat the caller owns. Messages and tool calls cascade. */
export async function deleteChat(id: string, userId: string): Promise<boolean> {
  const deleted = await db
    .delete(chat)
    .where(and(eq(chat.id, id), eq(chat.userId, userId)))
    .returning({ id: chat.id });
  return deleted.length > 0;
}
