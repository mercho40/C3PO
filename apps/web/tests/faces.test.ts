/**
 * «Caras»: a switch, a photo, a delete or an enroll is two calls — open the
 * card, then the call carrying it — and the call goes to the right route with
 * exactly the card's arguments. The photo is fetched no-store and comes back
 * as a Blob; refusals read in Spanish; the page is listed for admins only.
 */
import { describe, expect, test } from "bun:test";
import {
  askFace,
  enrolledText,
  faceErrorText,
  runFace,
} from "../src/lib/admin/faces";
import { navFor } from "../src/lib/nav";

function back(status: number, body: unknown, photo = false) {
  const sent: { url: string; init: RequestInit }[] = [];
  const impl = (async (url: string, init: RequestInit) => {
    sent.push({ url, init });
    return photo
      ? new Response(new Uint8Array([1, 2]), {
          status,
          headers: { "content-type": "image/jpeg" },
        })
      : Response.json(body, { status });
  }) as unknown as typeof fetch;
  return { sent, impl };
}

describe("faces", () => {
  test("asking opens a card for exactly that action and person", async () => {
    const b = back(200, { id: "c1" });
    const r = await askFace("http://back", "photo", { id: "p1" }, b.impl);
    expect(r).toEqual({ ok: true, id: "c1" });
    expect(b.sent[0]?.url).toBe("http://back/face/confirmations");
    expect(JSON.parse(String(b.sent[0]?.init.body))).toEqual({
      action: "photo",
      args: { id: "p1" },
    });
  });

  test("Ver cara fetches the photo with the card, no-store, as a Blob", async () => {
    const b = back(200, null, true);
    const r = await runFace(
      "http://back",
      "photo",
      { id: "p 1" },
      "c1",
      b.impl,
    );
    expect(b.sent[0]?.url).toBe(
      "http://back/face/people/p%201/photo.jpg?confirmation=c1",
    );
    expect(b.sent[0]?.init.cache).toBe("no-store");
    expect(r.ok && r.photo?.size).toBe(2);
  });

  test("each action goes to its route with the card and its arguments", async () => {
    const b = back(200, { ok: true });
    await runFace("http://back", "settings", { gate: true }, "c1", b.impl);
    await runFace("http://back", "delete", { id: "p1" }, "c2", b.impl);
    await runFace(
      "http://back",
      "enroll",
      { name: "Ana", role: "operator" },
      "c3",
      b.impl,
    );
    expect(b.sent.map((s) => [s.init.method ?? "GET", s.url])).toEqual([
      ["POST", "http://back/face/settings?confirmation=c1"],
      ["DELETE", "http://back/face/people/p1?confirmation=c2"],
      ["POST", "http://back/face/enroll?confirmation=c3"],
    ]);
    expect(JSON.parse(String(b.sent[2]?.init.body))).toEqual({
      name: "Ana",
      role: "operator",
    });
  });

  test("a voided card and an enroll the bridge refused read in Spanish", async () => {
    const voided = await runFace(
      "http://back",
      "photo",
      { id: "p1" },
      "c1",
      back(428, { error: "confirmation_required" }).impl,
    );
    expect(voided.ok).toBe(false);
    expect(!voided.ok && voided.text).toContain("PARAR");
    const refused = await runFace(
      "http://back",
      "enroll",
      { name: "Ana", role: "operator" },
      "c1",
      back(200, { reason: "multiple_faces" }).impl,
    );
    expect(refused.ok).toBe(false);
    expect(faceErrorText(200, { reason: "something_new" })).toContain(
      "something_new",
    );
    expect(faceErrorText(null, null)).toContain("backend");
  });

  test("enrolled dates accept ISO text and epoch seconds", () => {
    expect(enrolledText("2026-11-10T12:00:00Z")).toBe(
      enrolledText(Date.parse("2026-11-10T12:00:00Z") / 1000),
    );
    expect(enrolledText(null)).toBe("—");
  });

  test("Caras is in the nav for admins only", () => {
    const hrefs = (admin: boolean) =>
      navFor(admin).flatMap((g) => g.items.map((i) => i.href));
    expect(hrefs(true)).toContain("/faces");
    expect(hrefs(false)).not.toContain("/faces");
    expect(hrefs(false)).toContain("/health");
  });
});
