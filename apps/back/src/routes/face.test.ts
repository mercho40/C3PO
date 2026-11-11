/**
 * /face: admins only; nothing that changes a face or shows one reaches the
 * bridge without a Confirmar card for exactly that action and person; the
 * photo is never cached nor logged; PARAR voids the cards. Auth and the DB are
 * mocked as in safety.test.ts; the bridge is a stubbed global fetch.
 */
import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";

let sessionUser: { id: string; role: string | null } | null = null;
mock.module("#back/lib/auth.js", () => ({
  auth: {
    handler: async () => new Response("not mocked", { status: 404 }),
    api: {
      getSession: async () =>
        sessionUser ? { user: sessionUser, session: { id: "sess_1" } } : null,
    },
  },
}));
const logged: Record<string, unknown>[] = [];
function thenable(result: unknown): unknown {
  const self: Record<string, unknown> = {
    then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) =>
      Promise.resolve(result).then(resolve, reject),
  };
  return new Proxy(self, {
    get: (obj, prop) => (prop === "then" ? obj.then : () => thenable(result)),
  });
}
mock.module("#back/db/drizzle.js", () => ({
  db: {
    insert: () => ({
      values: (row: Record<string, unknown>) => {
        logged.push(row);
        return thenable([]);
      },
    }),
    select: () => thenable([]),
    update: () => thenable([]),
    delete: () => thenable([]),
    query: { chat: { findFirst: async () => undefined } },
  },
}));

const { faceRoutes } = await import("./face");
const { abortAllTurns } = await import("#back/agent/turns.js");
const realFetch = globalThis.fetch;
let seen: { url: string; init?: RequestInit }[] = [];
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);

function call(method: string, path: string, body?: unknown) {
  return faceRoutes.handle(
    new Request(`http://localhost/face${path}`, {
      method,
      ...(body === undefined
        ? {}
        : {
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
          }),
    }),
  );
}

async function ask(action: string, args: unknown): Promise<string> {
  const res = await call("POST", "/confirmations", { action, args });
  expect(res.status).toBe(200);
  return ((await res.json()) as { id: string }).id;
}

describe("/face", () => {
  beforeEach(() => {
    sessionUser = { id: "admin1", role: "admin" };
    seen = [];
    logged.length = 0;
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      seen.push({ url: String(url), init });
      if (String(url).endsWith("/photo.jpg"))
        return new Response(JPEG, {
          headers: { "content-type": "image/jpeg" },
        });
      return Response.json({ ok: true, settings: { greet: true } });
    }) as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  test("401 without a session, 403 for an operator, nothing reaches the bridge", async () => {
    sessionUser = null;
    expect((await call("GET", "/people")).status).toBe(401);
    sessionUser = { id: "op1", role: null };
    expect((await call("GET", "/people")).status).toBe(403);
    expect(
      (
        await call("POST", "/confirmations", {
          action: "photo",
          args: { id: "p1" },
        })
      ).status,
    ).toBe(403);
    expect((await call("GET", "/people/p1/photo.jpg")).status).toBe(403);
    expect(seen).toHaveLength(0);
  });

  test("an admin reads the list, relayed with the bridge proof", async () => {
    const res = await call("GET", "/people");
    expect(res.status).toBe(200);
    expect(seen[0]?.url).toEndWith("/face/people");
    const headers = seen[0]?.init?.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    delete process.env.SAFETY_CONFIRM_SECRET;
    expect((await call("GET", "/people")).status).toBe(503);
    expect(seen).toHaveLength(1);
  });

  test("/seen: any signed-in user gets the bridge's boxes, never cached", async () => {
    sessionUser = { id: "op1", role: null };
    const res = await call("GET", "/seen");
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.json()).toEqual({ ok: true, settings: { greet: true } });
    expect(seen[0]?.url).toEndWith("/face/seen");
    sessionUser = null;
    expect((await call("GET", "/seen")).status).toBe(401);
    globalThis.fetch = (async () => {
      throw new Error("down");
    }) as unknown as typeof fetch;
    sessionUser = { id: "op1", role: null };
    expect((await call("GET", "/seen")).status).toBe(502);
  });

  test("a replacement is its own card: replace is bound and relayed", async () => {
    const plain = await ask("enroll", { name: "Ana", role: "operator" });
    const replacing = { name: "Ana", role: "operator", replace: true };
    expect(
      (await call("POST", `/enroll?confirmation=${plain}`, replacing)).status,
    ).toBe(409);
    const id = await ask("enroll", replacing);
    expect(
      (
        await call("POST", `/enroll?confirmation=${id}`, {
          name: "Ana",
          role: "operator",
        })
      ).status,
    ).toBe(409);
    const again = await ask("enroll", replacing);
    expect(
      (await call("POST", `/enroll?confirmation=${again}`, replacing)).status,
    ).toBe(200);
    expect(JSON.parse(String(seen[0]?.init?.body))).toEqual(replacing);
    expect(seen).toHaveLength(1);
  });

  test("every mutation and photo view refuses without a confirmation", async () => {
    for (const [method, path, body] of [
      ["POST", "/settings", { greet: false }],
      ["DELETE", "/people/p1", undefined],
      ["POST", "/enroll", { name: "Ana", role: "operator" }],
      ["GET", "/people/p1/photo.jpg", undefined],
      ["GET", "/people/p1/photo.jpg?confirmation=made-up", undefined],
    ] as const) {
      const res = await call(method, path, body);
      expect(res.status).toBe(428);
    }
    expect(seen).toHaveLength(0);
  });

  test("a card for one person does not show another's face", async () => {
    const id = await ask("photo", { id: "p1" });
    const res = await call("GET", `/people/p2/photo.jpg?confirmation=${id}`);
    expect(res.status).toBe(409);
    expect(seen).toHaveLength(0);
  });

  test("a confirmed photo is relayed with the proof, never cached, never logged", async () => {
    const id = await ask("photo", { id: "p1" });
    const res = await call("GET", `/people/p1/photo.jpg?confirmation=${id}`);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(JPEG);
    expect(seen[0]?.url).toEndWith("/face/people/p1/photo.jpg");
    const headers = seen[0]?.init?.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    expect(logged[0]).toMatchObject({
      userId: "admin1",
      skillName: "face_photo",
      params: { person_id: "p1" },
    });
    expect(logged[0]?.result ?? null).toBeNull();
    // Single use: the same card does not show it twice.
    const again = await call("GET", `/people/p1/photo.jpg?confirmation=${id}`);
    expect(again.status).toBe(428);
  });

  test("confirmed settings, delete and enroll relay exactly the confirmed args", async () => {
    let id = await ask("settings", { gate: true });
    expect(
      (await call("POST", `/settings?confirmation=${id}`, { gate: true }))
        .status,
    ).toBe(200);
    expect(JSON.parse(String(seen[0]?.init?.body))).toEqual({ gate: true });

    id = await ask("delete", { id: "p1" });
    expect((await call("DELETE", `/people/p1?confirmation=${id}`)).status).toBe(
      200,
    );
    expect(seen[1]?.init?.method).toBe("DELETE");
    expect(seen[1]?.url).toEndWith("/face/people/p1");

    id = await ask("enroll", { name: "Ana", role: "operator" });
    // Different arguments from the card: refused.
    expect(
      (
        await call("POST", `/enroll?confirmation=${id}`, {
          name: "Ana",
          role: "superadmin",
        })
      ).status,
    ).toBe(409);
    expect(seen).toHaveLength(2);
    id = await ask("enroll", { name: "Ana", role: "operator" });
    expect(
      (
        await call("POST", `/enroll?confirmation=${id}`, {
          name: "Ana",
          role: "operator",
        })
      ).status,
    ).toBe(200);
    expect(seen[2]?.url).toEndWith("/face/enroll");
    expect(logged.map((r) => r.skillName)).toEqual([
      "face_settings",
      "face_delete",
      "face_enroll",
    ]);
  });

  test("another admin cannot use my card", async () => {
    const id = await ask("delete", { id: "p1" });
    sessionUser = { id: "admin2", role: "admin" };
    expect((await call("DELETE", `/people/p1?confirmation=${id}`)).status).toBe(
      428,
    );
    expect(seen).toHaveLength(0);
  });

  test("PARAR voids an open card", async () => {
    const id = await ask("photo", { id: "p1" });
    abortAllTurns();
    expect(
      (await call("GET", `/people/p1/photo.jpg?confirmation=${id}`)).status,
    ).toBe(428);
    expect(seen).toHaveLength(0);
  });

  test("Cancelar voids the card", async () => {
    const id = await ask("delete", { id: "p1" });
    expect((await call("DELETE", `/confirmations/${id}`)).status).toBe(200);
    expect((await call("DELETE", `/people/p1?confirmation=${id}`)).status).toBe(
      428,
    );
  });

  test("invalid cards are refused, and an unset secret does not burn the card", async () => {
    expect(
      (
        await call("POST", "/confirmations", {
          action: "enroll",
          args: { name: "", role: "operator" },
        })
      ).status,
    ).toBe(422);
    expect(
      (await call("POST", "/confirmations", { action: "rm -rf", args: {} }))
        .status,
    ).toBe(422);
    const id = await ask("photo", { id: "p1" });
    delete process.env.SAFETY_CONFIRM_SECRET;
    expect(
      (await call("GET", `/people/p1/photo.jpg?confirmation=${id}`)).status,
    ).toBe(503);
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    expect(
      (await call("GET", `/people/p1/photo.jpg?confirmation=${id}`)).status,
    ).toBe(200);
  });
});
