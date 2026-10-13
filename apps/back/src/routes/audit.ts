/**
 * /audit — the command log (`tool_call_log`), read-only, admin-only.
 *
 * `tool_call_log` has been called "the audit trail" since it was created, but
 * nothing could read it: rows were written by the agent and by realtime voice,
 * the direct /invoke path (every VR button, PARAR) wrote nothing, and there was
 * no endpoint, so even the rows that existed were invisible to the people who
 * would need them after something went wrong. This is the read side.
 *
 *   GET /audit?skill=&status=ok|error&source=agent|voice|direct&before=&limit=
 *     -> { rows: AuditRow[], nextCursor: string | null }   newest first
 *
 * ADMIN-ONLY, because it shows every operator's commands and their parameters,
 * not just the caller's. Non-admins get 403 `admin_required` — the same body
 * /skills/:name/invoke uses, so the console has one way to say it.
 *
 * Mounted beside index.ts's outer guard, not inside it, for the reason
 * skills.ts documents: this module reads a typed `user` through its own guard,
 * and nesting would resolve the session twice per request.
 *
 * PAGINATION is a cursor, not an offset. The table grows while an admin reads
 * it — a VR walk loop writes a row every ~0.9 s — so "page 2" by offset would
 * shift under them and repeat rows. `nextCursor` is the last row's id; `before`
 * also accepts an ISO timestamp, for "what happened before 14:05".
 *
 * Query parameters are validated in the handler rather than by an Elysia
 * schema on purpose: schema validation runs BEFORE the auth resolver, so a bad
 * `status=` would answer an anonymous caller 422 instead of 401. The gate goes
 * first; then the query.
 */

import { Elysia, t } from "elysia";
import { and, desc, eq, lt, sql, type SQL } from "drizzle-orm";

import { db } from "@back/db/drizzle";
import {
  chat,
  toolCallLog,
  user as userTable,
  type ToolCallSource,
} from "@back/db/schema";
import { betterAuth } from "@back/lib/auth-plugin";

export const AUDIT_DEFAULT_LIMIT = 50;
export const AUDIT_MAX_LIMIT = 200;

const STATUSES = ["ok", "error"] as const;
const SOURCES = [
  "agent",
  "voice",
  "direct",
] as const satisfies readonly ToolCallSource[];

/** A cursor: rows strictly older than an instant, or than a known row. */
export type AuditCursor =
  | { kind: "time"; at: Date }
  | { kind: "id"; id: string };

export type AuditQuery = {
  skill: string | null;
  status: (typeof STATUSES)[number] | null;
  source: ToolCallSource | null;
  before: AuditCursor | null;
  limit: number;
};

export type AuditQueryResult =
  | { ok: true; value: AuditQuery }
  | { ok: false; message: string };

/** ISO-8601 date prefix. A row id is a UUID, whose first group is 8 hex chars, so the two cannot collide. */
const ISO_PREFIX = /^\d{4}-\d{2}-\d{2}/;

/**
 * Turn raw query strings into a validated read. Pure, so it is tested directly.
 *
 * `limit` is clamped rather than refused: asking for 1000 rows and getting 200
 * is what anyone typing a number meant. Unknown filter values ARE refused — a
 * typo in `status=` silently ignored would show every row under a filter the
 * admin believes is applied.
 */
export function parseAuditQuery(
  raw: Record<string, string | undefined>,
): AuditQueryResult {
  const blank = (v: string | undefined) => (v ?? "").trim() || null;

  const skill = blank(raw.skill);
  if (skill && skill.length > 128) {
    return { ok: false, message: "skill is too long" };
  }

  const status = blank(raw.status);
  if (status && !(STATUSES as readonly string[]).includes(status)) {
    return {
      ok: false,
      message: `status must be one of ${STATUSES.join(", ")}`,
    };
  }

  const source = blank(raw.source);
  if (source && !(SOURCES as readonly string[]).includes(source)) {
    return {
      ok: false,
      message: `source must be one of ${SOURCES.join(", ")}`,
    };
  }

  let before: AuditCursor | null = null;
  const cursor = blank(raw.before);
  if (cursor) {
    if (ISO_PREFIX.test(cursor)) {
      const at = new Date(cursor);
      if (Number.isNaN(at.getTime())) {
        return { ok: false, message: "before is not a valid timestamp" };
      }
      before = { kind: "time", at };
    } else if (cursor.length <= 64) {
      before = { kind: "id", id: cursor };
    } else {
      return { ok: false, message: "before is not a row id or a timestamp" };
    }
  }

  const requested = Number.parseInt(blank(raw.limit) ?? "", 10);
  const limit = Number.isFinite(requested)
    ? Math.min(AUDIT_MAX_LIMIT, Math.max(1, requested))
    : AUDIT_DEFAULT_LIMIT;

  return {
    ok: true,
    value: {
      skill,
      status: status as AuditQuery["status"],
      source: source as AuditQuery["source"],
      before,
      limit,
    },
  };
}

/** One joined row as the query returns it. */
export type AuditDbRow = {
  id: string;
  createdAt: Date;
  skillName: string;
  params: Record<string, unknown> | null;
  result: unknown;
  status: "ok" | "error";
  error: string | null;
  durationMs: number | null;
  chatId: string | null;
  source: ToolCallSource | null;
  userId: string | null;
  userName: string | null;
  userEmail: string | null;
};

export type AuditRow = Omit<AuditDbRow, "userId" | "userName" | "userEmail"> & {
  /** Null when nobody is known: legacy rows, external MCP clients, deleted accounts. */
  user: { id: string; name: string; email: string } | null;
};

/**
 * Shape one page. The query asks for `limit + 1` rows; the extra one only
 * proves there is a next page and is never returned.
 */
export function auditPage(
  dbRows: AuditDbRow[],
  limit: number,
): { rows: AuditRow[]; nextCursor: string | null } {
  const page = dbRows.slice(0, limit);
  return {
    rows: page.map(({ userId, userName, userEmail, ...row }) => ({
      ...row,
      user:
        userId !== null
          ? { id: userId, name: userName ?? "", email: userEmail ?? "" }
          : null,
    })),
    nextCursor:
      dbRows.length > limit && page.length > 0
        ? page[page.length - 1]!.id
        : null,
  };
}

/** Read one page from the database. */
async function readAudit(q: AuditQuery): Promise<AuditDbRow[]> {
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

export const auditRoutes = new Elysia({ prefix: "/audit" })
  .use(betterAuth)
  .guard({ auth: true }, (app) =>
    app.get(
      "/",
      async ({ user, query, status }) => {
        if (user.role !== "admin") {
          return status(403, {
            error: "admin_required" as const,
            message: "The command log is restricted to admins.",
          });
        }

        const parsed = parseAuditQuery(query);
        if (!parsed.ok) {
          return status(422, {
            error: "invalid_query" as const,
            message: parsed.message,
          });
        }
        const q = parsed.value;

        if (q.before?.kind === "id" && !(await cursorExists(q.before.id))) {
          return status(400, {
            error: "invalid_cursor" as const,
            message: "before does not name a row in the log",
          });
        }

        return auditPage(await readAudit(q), q.limit);
      },
      {
        // All optional strings so Elysia's validation (which runs before the
        // auth resolver) can never answer an anonymous caller first; the
        // semantics are checked in `parseAuditQuery`.
        query: t.Object({
          skill: t.Optional(t.String()),
          status: t.Optional(t.String()),
          source: t.Optional(t.String()),
          before: t.Optional(t.String()),
          limit: t.Optional(t.String()),
        }),
        detail: {
          summary:
            "Read the command log (every skill dispatched to the robot), newest first. Admin-only.",
          tags: ["audit"],
        },
      },
    ),
  );
