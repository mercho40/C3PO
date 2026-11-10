/**
 * chats.ts on a real Postgres: whose chat is whose, and what concurrent
 * appends do to `seq`. Every other back suite mocks drizzle, so none of this
 * had ever run against a database.
 *
 *   C3PO_DB_TEST=1 DATABASE_URL=postgres://…@localhost:5432/<scratch db> \
 *     bun test src/db/chats.db.test.ts
 *
 * Skipped without C3PO_DB_TEST=1. Run it alone: five suites mock
 * #back/db/drizzle.js process-wide, so inside the full run it could be handed
 * their stub. It refuses any DATABASE_URL host but localhost or 127.0.0.1
 * before connecting, because bun loads apps/back/.env, which can name the
 * shared cloud database. Give it a scratch database: it applies the
 * migrations, writes only rows with this run's ids, and deletes them after.
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { join } from "node:path";

const enabled = process.env.C3PO_DB_TEST === "1";

describe.skipIf(!enabled)("chats.ts on Postgres", () => {
  let chats: typeof import("./chats");
  let db: (typeof import("./drizzle"))["db"];
  let schema: typeof import("./schema");
  let orm: typeof import("drizzle-orm");

  const run = crypto.randomUUID().slice(0, 8);
  const alice = `alice-${run}`;
  const bob = `bob-${run}`;
  const text = (t: string) => [{ type: "text", text: t }];

  beforeAll(async () => {
    const host = new URL(process.env.DATABASE_URL ?? "").hostname;
    if (host !== "localhost" && host !== "127.0.0.1") {
      throw new Error(
        `C3PO_DB_TEST refuses DATABASE_URL host "${host}": localhost or 127.0.0.1 only`,
      );
    }
    ({ db } = await import("./drizzle"));
    chats = await import("./chats");
    schema = await import("./schema");
    orm = await import("drizzle-orm");
    const { migrate } = await import("drizzle-orm/bun-sql/migrator");
    await migrate(db, {
      migrationsFolder: join(import.meta.dir, "../../migrations"),
    });
    await db.insert(schema.user).values(
      [alice, bob].map((id) => ({
        id,
        name: id,
        email: `${id}@c3po.test`,
      })),
    );
  });

  afterAll(async () => {
    // Chats and their messages cascade.
    await db
      ?.delete(schema.user)
      .where(orm.inArray(schema.user.id, [alice, bob]));
  });

  test("someone else's chat id is refused, not taken over", async () => {
    const id = `chat-${run}-owned`;
    expect(await chats.ensureChat({ id, userId: alice, title: "a" })).toEqual({
      id,
      created: true,
      channel: "text",
    });
    expect(await chats.ensureChat({ id, userId: alice })).toEqual({
      id,
      created: false,
      channel: "text",
    });
    expect(await chats.ensureChat({ id, userId: bob, title: "b" })).toBeNull();
    expect(await chats.getChatWithMessages(id, bob)).toBeNull();
    expect(await chats.renameChat(id, bob, "mine now")).toBeNull();
    expect(await chats.deleteChat(id, bob)).toBe(false);
    expect((await chats.listChats(bob)).map((c) => c.id)).not.toContain(id);
    expect((await chats.getChatWithMessages(id, alice))?.title).toBe("a");
  });

  test("a message id from another chat is never rewritten: the new one gets its own id", async () => {
    const a = `chat-${run}-a`;
    const b = `chat-${run}-b`;
    await chats.ensureChat({ id: a, userId: alice });
    await chats.ensureChat({ id: b, userId: bob });
    const id = `msg-${run}-borrowed`;
    expect(
      await chats.appendMessage({
        id,
        chatId: a,
        role: "user",
        parts: text("alice"),
      }),
    ).toBe(id);
    const bobs = await chats.appendMessage({
      id,
      chatId: b,
      role: "user",
      parts: text("bob"),
    });
    expect(bobs).not.toBe(id);
    const inA = (await chats.getChatWithMessages(a, alice))?.messages;
    expect(inA?.map((m) => [m.id, m.parts])).toEqual([[id, text("alice")]]);
    const inB = (await chats.getChatWithMessages(b, bob))?.messages;
    expect(inB?.map((m) => [m.id, m.parts])).toEqual([[bobs, text("bob")]]);
  });

  test("a retried id rewrites its own row, never a row of the other role", async () => {
    const c = `chat-${run}-retry`;
    await chats.ensureChat({ id: c, userId: alice });
    const u = `u-${run}`;
    const r = `a-${run}`;
    await chats.appendMessage({
      id: u,
      chatId: c,
      role: "user",
      parts: text("hola"),
    });
    await chats.appendMessage({
      id: r,
      chatId: c,
      role: "assistant",
      parts: text("draft"),
    });
    expect(
      await chats.appendMessage({
        id: r,
        chatId: c,
        role: "assistant",
        parts: text("final"),
      }),
    ).toBe(r);
    // A replayed user id under the assistant role keeps the user's row.
    const replay = await chats.appendMessage({
      id: u,
      chatId: c,
      role: "assistant",
      parts: text("forged"),
    });
    expect(replay).not.toBe(u);
    const messages = (await chats.getChatWithMessages(c, alice))?.messages;
    expect(messages?.map((m) => [m.role, m.parts])).toEqual([
      ["user", text("hola")],
      ["assistant", text("final")],
      ["assistant", text("forged")],
    ]);
  });

  test("concurrent appends to one chat all land, each on its own seq", async () => {
    const c = `chat-${run}-race`;
    await chats.ensureChat({ id: c, userId: alice });
    const n = 20;
    const settled = await Promise.allSettled(
      Array.from({ length: n }, (_, i) =>
        chats.appendMessage({ chatId: c, role: "user", parts: text(`${i}`) }),
      ),
    );
    expect(
      settled.flatMap((s) =>
        s.status === "rejected" ? [String(s.reason?.cause ?? s.reason)] : [],
      ),
    ).toEqual([]);
    const rows = await db
      .select({ seq: schema.chatMessage.seq })
      .from(schema.chatMessage)
      .where(orm.eq(schema.chatMessage.chatId, c))
      .orderBy(schema.chatMessage.seq);
    expect(rows.map((row) => row.seq)).toEqual(
      Array.from({ length: n }, (_, i) => i + 1),
    );
  });
});
