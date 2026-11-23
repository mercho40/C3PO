/**
 * The read side of `tool_call_log`, for GET /audit (routes/audit.ts).
 */
import { and, desc, eq, lt, sql, type SQL } from "drizzle-orm";

import type { AuditDbRow, AuditQuery } from "#back/routes/audit.js";
import type { Db } from "./drizzle";
import { chat, toolCallLog, user as userTable } from "./schema";

export function createAuditReader(db: Pick<Db, "select">) {
  /** Read one page from the database. */
  async function read(q: AuditQuery): Promise<AuditDbRow[]> {
    const where: SQL[] = [];
    if (q.skill) where.push(eq(toolCallLog.skillName, q.skill));
    if (q.status) where.push(eq(toolCallLog.status, q.status));
    if (q.source) where.push(eq(toolCallLog.source, q.source));
    if (q.before?.kind === "time") {
      // `lt`, not raw SQL, so the column maps the Date to its driver value.
      where.push(lt(toolCallLog.createdAt, q.before.at));
    } else if (q.before?.kind === "id") {
      // Row-value comparison against the cursor row itself, in SQL: the
      // timestamp never round-trips through a JS Date (millisecond precision
      // against Postgres's microseconds), so a page boundary cannot skip rows
      // that share the cursor's millisecond.
      where.push(
        sql`(${toolCallLog.createdAt}, ${toolCallLog.id}) < (select "cursor"."created_at", "cursor"."id" from "tool_call_log" as "cursor" where "cursor"."id" = ${q.before.id})`,
      );
    }

    // Attribution: the row's own user_id, or — for rows written before that
    // column existed — the owner of the chat that dispatched them. `ensureChat`
    // has always refused to let anyone write into a chat that is not theirs, so
    // the chat's owner IS who ran that turn.
    const owner = sql`coalesce(${toolCallLog.userId}, ${chat.userId})`;

    return db
      .select({
        id: toolCallLog.id,
        createdAt: toolCallLog.createdAt,
        skillName: toolCallLog.skillName,
        params: toolCallLog.params,
        result: toolCallLog.result,
        status: toolCallLog.status,
        error: toolCallLog.error,
        durationMs: toolCallLog.durationMs,
        chatId: toolCallLog.chatId,
        source: toolCallLog.source,
        userId: userTable.id,
        userName: userTable.name,
        userEmail: userTable.email,
      })
      .from(toolCallLog)
      .leftJoin(chat, eq(chat.id, toolCallLog.chatId))
      .leftJoin(userTable, eq(userTable.id, owner))
      .where(where.length ? and(...where) : undefined)
      .orderBy(desc(toolCallLog.createdAt), desc(toolCallLog.id))
      .limit(q.limit + 1) as Promise<AuditDbRow[]>;
  }

  /** Whether a cursor id names a row. An unknown cursor is a 400, not an empty page that reads as "no more". */
  async function cursorExists(id: string): Promise<boolean> {
    const rows = await db
      .select({ id: toolCallLog.id })
      .from(toolCallLog)
      .where(eq(toolCallLog.id, id))
      .limit(1);
    return rows.length > 0;
  }

  return { read, cursorExists };
}

export type AuditReader = ReturnType<typeof createAuditReader>;
