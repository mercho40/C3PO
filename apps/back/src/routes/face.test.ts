/**
 * /face: admins only; nothing that changes a face or shows one reaches the
 * bridge without a Confirmar card for exactly that action and person; the
 * photo is never cached nor h.rows; PARAR voids the cards. In process through
 * the real app (`testApp`): a fake session, database and bridge.
 */
import { beforeEach, describe, expect, test } from "bun:test";

import { testApp, type TestApp } from "../testing/fakes";
import { FACES } from "../testing/payloads";

let h: TestApp;
const seen = () => h.fetched;
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);

function call(method: string, path: string, body?: unknown) {
  return h.app.handle(
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

function bridge(url: string) {
  if (String(url).endsWith("/photo.jpg"))
    return new Response(JPEG, {
      headers: { "content-type": "image/jpeg" },
    });
  if (String(url).endsWith("/face/people")) return Response.json(FACES);
  return Response.json({ ok: true, settings: { greet: true } });
}

describe("/face", () => {
  beforeEach(() => {
    h = testApp({ fetch: bridge });
    h.signIn({ id: "admin1", role: "admin" });
  });

  test("401 without a session, 403 for an operator, nothing reaches the bridge", async () => {
    h.signIn(null);
    expect((await call("GET", "/people")).status).toBe(401);
    h.signIn({ id: "op1", role: null });
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
    expect(seen()).toHaveLength(0);
  });

  test("an admin reads the list, relayed with the bridge proof", async () => {
    const res = await call("GET", "/people");
    expect(res.status).toBe(200);
    expect(seen()[0]?.url).toEndWith("/face/people");
    const headers = seen()[0]?.init?.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    h = testApp({ fetch: bridge, env: { SAFETY_CONFIRM_SECRET: "" } });
    h.signIn({ id: "admin1", role: "admin" });
    expect((await call("GET", "/people")).status).toBe(503);
    expect(seen()).toHaveLength(0);
  });

  test("/seen: any signed-in user gets the bridge's boxes, never cached", async () => {
    h.signIn({ id: "op1", role: null });
    const res = await call("GET", "/seen");
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.json()).toEqual({ ok: true, settings: { greet: true } });
    expect(seen()[0]?.url).toEndWith("/face/seen");
    h.signIn(null);
    expect((await call("GET", "/seen")).status).toBe(401);
    h.onFetch(() => {
      throw new Error("down");
    });
    h.signIn({ id: "op1", role: null });
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
    expect(JSON.parse(String(seen()[0]?.init?.body))).toEqual(replacing);
    expect(seen()).toHaveLength(1);
  });

  test("every mutation and photo view refuses without a confirmation", async () => {
    for (const [method, path, body] of [
      ["POST", "/settings", { greet: false }],
      ["DELETE", "/people/p1", undefined],
      ["POST", "/enroll", { name: "Ana", role: "operator" }],
      ["GET", "/people/p1/photo.jpg", undefined],
      ["GET", "/people/p1/photo.jpg?confirmation=made-up", undefined],
      ["PATCH", "/people/p1", { role: "operator" }],
    ] as const) {
      const res = await call(method, path, body);
      expect(res.status).toBe(428);
    }
    expect(seen()).toHaveLength(0);
  });

  test("a card for one person does not show another's face", async () => {
    const id = await ask("photo", { id: "p1" });
    const res = await call("GET", `/people/p2/photo.jpg?confirmation=${id}`);
    expect(res.status).toBe(409);
    expect(seen()).toHaveLength(0);
  });

  test("a confirmed photo is relayed with the proof, never cached, never h.rows", async () => {
    const id = await ask("photo", { id: "p1" });
    const res = await call("GET", `/people/p1/photo.jpg?confirmation=${id}`);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(JPEG);
    expect(seen()[0]?.url).toEndWith("/face/people/p1/photo.jpg");
    const headers = seen()[0]?.init?.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    expect(h.rows[0]).toMatchObject({
      userId: "admin1",
      skillName: "face_photo",
      params: { person_id: "p1" },
    });
    expect(h.rows[0]?.result ?? null).toBeNull();
    // Single use: the same card does not show it twice.
    const again = await call("GET", `/people/p1/photo.jpg?confirmation=${id}`);
    expect(again.status).toBe(428);
  });

  test("«Escuchar después de saludar» on makes its admin the window's owner; off clears it", async () => {
    const { listenWindow } = h.services.voice;
    listenWindow.setOwner(null);
    let id = await ask("settings", { listen_after_greet: true });
    const on = await call("POST", `/settings?confirmation=${id}`, {
      listen_after_greet: true,
    });
    expect(on.status).toBe(200);
    expect(JSON.parse(String(seen()[0]?.init?.body))).toEqual({
      listen_after_greet: true,
    });
    expect(listenWindow.ownerId()).toBe("admin1");
    // Another switch leaves the owner alone; a refused call changes nothing.
    id = await ask("settings", { greet: false });
    await call("POST", `/settings?confirmation=${id}`, { greet: false });
    await call("POST", `/settings`, { listen_after_greet: false }); // no card: 428
    expect(listenWindow.ownerId()).toBe("admin1");
    id = await ask("settings", { listen_after_greet: false });
    await call("POST", `/settings?confirmation=${id}`, {
      listen_after_greet: false,
    });
    expect(listenWindow.ownerId()).toBeNull();
  });

  test("confirmed settings, delete and enroll relay exactly the confirmed args", async () => {
    let id = await ask("settings", { gate: true });
    expect(
      (await call("POST", `/settings?confirmation=${id}`, { gate: true }))
        .status,
    ).toBe(200);
    expect(JSON.parse(String(seen()[0]?.init?.body))).toEqual({ gate: true });

    id = await ask("delete", { id: "p1" });
    expect((await call("DELETE", `/people/p1?confirmation=${id}`)).status).toBe(
      200,
    );
    expect(seen()[1]?.init?.method).toBe("DELETE");
    expect(seen()[1]?.url).toEndWith("/face/people/p1");

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
    expect(seen()).toHaveLength(2);
    id = await ask("enroll", { name: "Ana", role: "operator" });
    expect(
      (
        await call("POST", `/enroll?confirmation=${id}`, {
          name: "Ana",
          role: "operator",
        })
      ).status,
    ).toBe(200);
    expect(seen()[2]?.url).toEndWith("/face/enroll");
    expect(h.rows.map((r) => r.skillName)).toEqual([
      "face_settings",
      "face_delete",
      "face_enroll",
    ]);
  });

  test("a role change relays exactly the confirmed person and role", async () => {
    let id = await ask("role", { id: "p1", role: "operator" });
    // The card is for operator: superadmin, or another person, is refused.
    expect(
      (
        await call("PATCH", `/people/p1?confirmation=${id}`, {
          role: "superadmin",
        })
      ).status,
    ).toBe(409);
    id = await ask("role", { id: "p1", role: "operator" });
    expect(
      (
        await call("PATCH", `/people/p2?confirmation=${id}`, {
          role: "operator",
        })
      ).status,
    ).toBe(409);
    expect(seen()).toHaveLength(0);
    id = await ask("role", { id: "p1", role: "operator" });
    expect(
      (
        await call("PATCH", `/people/p1?confirmation=${id}`, {
          role: "operator",
        })
      ).status,
    ).toBe(200);
    expect(seen()[0]?.init?.method).toBe("PATCH");
    expect(seen()[0]?.url).toEndWith("/face/people/p1");
    expect(JSON.parse(String(seen()[0]?.init?.body))).toMatchObject({
      role: "operator",
    });
    expect(h.rows[0]).toMatchObject({ skillName: "face_role" });
    // No such role: no card at all.
    const bad = await call("POST", "/confirmations", {
      action: "role",
      args: { id: "p1", role: "root" },
    });
    expect(bad.status).toBe(422);
  });

  test("another admin cannot use my card", async () => {
    const id = await ask("delete", { id: "p1" });
    h.signIn({ id: "admin2", role: "admin" });
    expect((await call("DELETE", `/people/p1?confirmation=${id}`)).status).toBe(
      428,
    );
    expect(seen()).toHaveLength(0);
  });

  test("PARAR voids an open card", async () => {
    const id = await ask("photo", { id: "p1" });
    h.services.turns.abortAll();
    expect(
      (await call("GET", `/people/p1/photo.jpg?confirmation=${id}`)).status,
    ).toBe(428);
    expect(seen()).toHaveLength(0);
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
    h = testApp({ fetch: bridge, env: { SAFETY_CONFIRM_SECRET: "" } });
    h.signIn({ id: "admin1", role: "admin" });
    const id = await ask("photo", { id: "p1" });
    expect(
      (await call("GET", `/people/p1/photo.jpg?confirmation=${id}`)).status,
    ).toBe(503);
    // Checked before the card is taken: a misconfigured server does not burn it.
    expect(
      h.services.confirmations.peek(id, "admin1", "face_photo", { id: "p1" }),
    ).toMatchObject({ id });
  });
});
