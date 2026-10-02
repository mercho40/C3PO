/**
 * GET /audit — the command log's read side.
 *
 * The properties pinned here: the log is admin-only (it shows every
 * operator's commands, not just the caller's), the gate runs BEFORE query
 * validation (an anonymous caller must get 401, never a 422 that confirms the
 * endpoint's shape), filters it does not understand are refused rather than
 * silently ignored, and pagination never invents a "next page" that is not
 * there.
 *
 * No database: `@back/db/drizzle` is replaced with a chain that records what
 * the route asked for and answers from a queue. `bun test` shares one module
 * registry across files, so this mock's surface (`db`) is complete — see the
 * note in skills.test.ts.
 */
import { beforeEach, describe, expect, mock, test } from "bun:test";

let sessionUser: { id: string; role: string | null } | null = null;

mock.module("@back/lib/auth", () => ({
  auth: {
    handler: async () => new Response("not mocked", { status: 404 }),
    api: {
      getSession: async () => {
        if (!sessionUser) return null;
        return { user: sessionUser, session: { id: "sess_1" } };
      },
    },
  },
}));

/** Every select() the route made, with the chain of calls on it. */
let selects: { calls: [string, unknown[]][] }[] = [];
/** What each successive select() resolves to. */
let selectResults: unknown[][] = [];

function chain(record: { calls: [string, unknown[]][] }, result: unknown[]) {
  const target = {
    then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) =>
      Promise.resolve(result).then(resolve, reject),
  };
  const proxy: unknown = new Proxy(target, {
    get(obj, prop) {
      if (prop === "then") return obj.then;
      return (...args: unknown[]) => {
        record.calls.push([String(prop), args]);
        return proxy;
      };
    },
  });
  return proxy;
}

mock.module("@back/db/drizzle", () => ({
  db: {
    select: (...args: unknown[]) => {
      const record = { calls: [["select", args]] as [string, unknown[]][] };
      selects.push(record);
      return chain(record, selectResults.shift() ?? []);
    },
    // Writes are not this route's business, but other modules share this
    // mock through the registry; they must not throw on import or call.
    insert: () => chain({ calls: [] }, []),
    update: () => chain({ calls: [] }, []),
    delete: () => chain({ calls: [] }, []),
    query: { chat: { findFirst: async () => undefined } },
  },
}));

const { auditRoutes, parseAuditQuery, auditPage, AUDIT_MAX_LIMIT } =
  await import("./audit");

function get(query = "") {
  return auditRoutes.handle(
    new Request(`http://localhost/audit${query ? `?${query}` : ""}`),
  );
}

function dbRow(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    createdAt: new Date("2026-10-01T14:05:00.000Z"),
    skillName: "walk_velocity",
    params: { vx: 0.2 },
    result: { status: "ok" },
    status: "ok",
    error: null,
    durationMs: 41,
    chatId: null,
    source: "direct",
    userId: "u1",
    userName: "Ada",
    userEmail: "ada@example.com",
    ...overrides,
  };
}

beforeEach(() => {
  sessionUser = null;
  selects = [];
  selectResults = [];
});

describe("GET /audit — the gate", () => {
  test("401 without a session", async () => {
    const res = await get();
    expect(res.status).toBe(401);
  });

  test("401 without a session even when the query is malformed", async () => {
    // Validation must not run ahead of the gate and answer anonymously.
    const res = await get("status=bogus");
    expect(res.status).toBe(401);
  });

  test("403 admin_required for a non-admin, and the log is never read", async () => {
    sessionUser = { id: "u2", role: null };
    const res = await get();
    expect(res.status).toBe(403);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe("admin_required");
    expect(selects.length).toBe(0);
  });

  test("403 for a non-admin even with a malformed query", async () => {
    sessionUser = { id: "u2", role: "user" };
    const res = await get("status=bogus");
    expect(res.status).toBe(403);
  });
});

describe("GET /audit — the shape", () => {
  test("an admin gets rows newest first with the user folded in, and no next page when the read came back short", async () => {
    sessionUser = { id: "u1", role: "admin" };
    selectResults = [
      [
        dbRow("b"),
        dbRow("a", {
          userId: null,
          userName: null,
          userEmail: null,
          source: null,
        }),
      ],
    ];
    const res = await get();
    expect(res.status).toBe(200);
    const json = (await res.json()) as {
      rows: Record<string, unknown>[];
      nextCursor: string | null;
    };
    expect(json.rows.length).toBe(2);
    expect(json.rows[0]!.id).toBe("b");
    expect(json.rows[0]!.user).toEqual({
      id: "u1",
      name: "Ada",
      email: "ada@example.com",
    });
    expect(json.rows[0]!.skillName).toBe("walk_velocity");
    expect(json.rows[0]!.params).toEqual({ vx: 0.2 });
    expect(json.rows[0]!.source).toBe("direct");
    expect(json.rows[0]!.durationMs).toBe(41);
    // Nobody known is null, never a fabricated placeholder user.
    expect(json.rows[1]!.user).toBeNull();
    expect(json.rows[1]!.source).toBeNull();
    // The flattened join columns do not leak into the response.
    expect("userEmail" in json.rows[0]!).toBe(false);
    expect(json.nextCursor).toBeNull();
  });

  test("asks the database for one row more than the page, and orders newest first", async () => {
    sessionUser = { id: "u1", role: "admin" };
    await get("limit=10");
    const calls = selects[0]!.calls;
    const limit = calls.find(([name]) => name === "limit");
    expect(limit?.[1][0]).toBe(11);
    expect(calls.some(([name]) => name === "orderBy")).toBe(true);
  });

  test("the limit is clamped to the maximum", async () => {
    sessionUser = { id: "u1", role: "admin" };
    await get("limit=100000");
    const limit = selects[0]!.calls.find(([name]) => name === "limit");
    expect(limit?.[1][0]).toBe(AUDIT_MAX_LIMIT + 1);
  });

  test("422 for a filter value it does not understand, rather than ignoring it", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await get("source=telepathy");
    expect(res.status).toBe(422);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe("invalid_query");
  });

  test("400 for a cursor id that names no row — not an empty page that reads as 'no more'", async () => {
    sessionUser = { id: "u1", role: "admin" };
    selectResults = [[]]; // the existence check finds nothing
    const res = await get("before=not-a-row");
    expect(res.status).toBe(400);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe("invalid_cursor");
  });

  test("a known cursor id pages on", async () => {
    sessionUser = { id: "u1", role: "admin" };
    selectResults = [[{ id: "c" }], [dbRow("b")]];
    const res = await get("before=c");
    expect(res.status).toBe(200);
    expect(selects.length).toBe(2);
  });
});

describe("parseAuditQuery", () => {
  test("defaults", () => {
    const r = parseAuditQuery({});
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value).toEqual({
      skill: null,
      status: null,
      source: null,
      before: null,
      limit: 50,
    });
  });

  test("blank values are absent, not filters for the empty string", () => {
    const r = parseAuditQuery({ skill: "  ", status: "", source: "" });
    expect(r.ok && r.value.skill === null && r.value.status === null).toBe(
      true,
    );
  });

  test("an ISO timestamp is a time cursor; anything else short is a row id", () => {
    const t = parseAuditQuery({ before: "2026-10-01T14:05:00Z" });
    expect(t.ok && t.value.before?.kind).toBe("time");
    const id = parseAuditQuery({
      before: "3f2a9c1e-0000-4000-8000-000000000000",
    });
    expect(id.ok && id.value.before?.kind).toBe("id");
  });

  test("a timestamp that does not parse is refused", () => {
    expect(parseAuditQuery({ before: "2026-13-45T99:99" }).ok).toBe(false);
  });

  test("a non-numeric limit falls back to the default; zero and negatives clamp to 1", () => {
    const nan = parseAuditQuery({ limit: "lots" });
    expect(nan.ok && nan.value.limit).toBe(50);
    const zero = parseAuditQuery({ limit: "0" });
    expect(zero.ok && zero.value.limit).toBe(1);
  });

  test("unknown status/source values are refused", () => {
    expect(parseAuditQuery({ status: "maybe" }).ok).toBe(false);
    expect(parseAuditQuery({ source: "mcp" }).ok).toBe(false);
    expect(parseAuditQuery({ status: "error", source: "voice" }).ok).toBe(true);
  });
});

describe("auditPage", () => {
  test("the extra row proves a next page and is never returned; the cursor is the last returned row", () => {
    const rows = [dbRow("c"), dbRow("b"), dbRow("a")] as Parameters<
      typeof auditPage
    >[0];
    const page = auditPage(rows, 2);
    expect(page.rows.map((r) => r.id)).toEqual(["c", "b"]);
    expect(page.nextCursor).toBe("b");
  });

  test("exactly a full page with nothing after it has no next cursor", () => {
    const rows = [dbRow("b"), dbRow("a")] as Parameters<typeof auditPage>[0];
    expect(auditPage(rows, 2).nextCursor).toBeNull();
  });
});
