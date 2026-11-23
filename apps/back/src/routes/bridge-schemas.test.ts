/**
 * The proxy routes' response schemas (bridge/payloads.ts), through the client
 * the console uses: Eden's `treaty(app)`, in process.
 *
 * For each route: the bridge's real answer passes, typed; a malformed one is a
 * 502 `bridge_bad_payload` naming the field — never a 200 the console would
 * draw from `undefined`, never Elysia's 422 (that reads as the CALLER's
 * mistake); a field the bridge adds is kept. And the stop path checks nothing:
 * PARAR and «Reanudar» answer whatever the bridge says.
 */
import { describe, expect, test } from "bun:test";
import { treaty } from "@elysiajs/eden";

import { testApp } from "../testing/fakes";
import {
  FACES,
  GATE,
  SAFETY_STATUS,
  SCAN,
  STATE,
  SURROUNDINGS,
  VOICE_TELEMETRY,
} from "../testing/payloads";

/** A fake bridge answering `body` on every HTTP route. */
function bridgeAnswering(body: unknown, status = 200) {
  const h = testApp({
    fetch: () =>
      typeof body === "string"
        ? new Response(body, { status })
        : Response.json(body, { status }),
  });
  h.signIn({ id: "u1", role: "admin" });
  return h;
}

const BAD = { error: "bridge_bad_payload" };

describe("GET /state", () => {
  test("the bridge's state passes, typed", async () => {
    const h = testApp({ callTool: async () => ({ ...STATE, extra: 1 }) });
    h.signIn({ id: "u1", role: null });
    const { data, status } = await treaty(h.app).state.get();
    expect(status).toBe(200);
    // Typed by the contract: no cast needed to read the pose.
    expect(data?.pose?.x_meters_world).toBe(1);
    expect(data).toMatchObject({ extra: 1 });
  });

  test("a malformed state is a 502 naming the field", async () => {
    const h = testApp({
      callTool: async () => ({
        ...STATE,
        pose: { ...STATE.pose, x_meters_world: "1" },
      }),
    });
    h.signIn({ id: "u1", role: null });
    const { error } = await treaty(h.app).state.get();
    expect(error?.status).toBe(502);
    expect(error?.value).toMatchObject({
      ...BAD,
      path: "/pose/x_meters_world",
    });
  });
});

describe("GET /safety/status", () => {
  test("the governor's status passes, typed", async () => {
    const h = bridgeAnswering({ ...SAFETY_STATUS, stop_standing: true });
    const { data } = await treaty(h.app).safety.status.get();
    expect(data?.stop_standing).toBe(true);
    expect(data?.caps.linear_mps).toBe(0.3);
  });

  test("a status without the latch is a 502, not «not stopped»", async () => {
    const { stop_standing: _, ...noLatch } = SAFETY_STATUS;
    const h = bridgeAnswering(noLatch);
    const { error } = await treaty(h.app).safety.status.get();
    expect(error?.status).toBe(502);
    expect(error?.value).toMatchObject({ ...BAD, path: "/stop_standing" });
  });

  test("a non-200 from the bridge is a 502 bridge_error", async () => {
    const h = bridgeAnswering({ detail: "Not Found" }, 404);
    const { error } = await treaty(h.app).safety.status.get();
    expect(error?.status).toBe(502);
    expect(error?.value).toMatchObject({ error: "bridge_error" });
  });
});

describe("GET /telemetry/*", () => {
  const routes = [
    ["surroundings", SURROUNDINGS, { ...SURROUNDINGS, sources: { a: 1 } }],
    ["scan", SCAN, { ...SCAN, r_cm: ["far"] }],
    ["voice", VOICE_TELEMETRY, { ...VOICE_TELEMETRY, running: "yes" }],
    ["gate", GATE, { ...GATE, enabled: undefined }],
  ] as const;

  for (const [name, good, bad] of routes) {
    test(`${name}: the bridge's answer passes, extra fields kept, never cached`, async () => {
      const h = bridgeAnswering({ ...good, added_later: true });
      const res = await h.request(`/telemetry/${name}`);
      expect(res.status).toBe(200);
      expect(res.headers.get("cache-control")).toBe("no-store");
      expect(await res.json()).toEqual({ ...good, added_later: true });
    });

    test(`${name}: a malformed answer is a 502 bridge_bad_payload`, async () => {
      const res = await bridgeAnswering(bad).request(`/telemetry/${name}`);
      expect(res.status).toBe(502);
      expect(await res.json()).toMatchObject(BAD);
    });
  }

  test("a 200 that is not JSON is a bad payload, not a 500", async () => {
    const res = await bridgeAnswering("<html>").request("/telemetry/gate");
    expect(res.status).toBe(502);
    expect(await res.json()).toMatchObject({ ...BAD, message: "not JSON" });
  });

  test("the bridge's 503 passes with its reason", async () => {
    const h = bridgeAnswering(
      { error: "no scan received", hint: "lidar" },
      503,
    );
    const { error } = await treaty(h.app).telemetry.scan.get();
    expect(error?.status).toBe(503);
    expect(error?.value).toMatchObject({ error: "no scan received" });
  });

  test("a 503 whose body is not a reason is still a 503", async () => {
    const res = await bridgeAnswering({ error: 7 }, 503).request(
      "/telemetry/voice",
    );
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ error: "unavailable" });
  });

  test("a bridge that does not answer is 502 bridge_unavailable", async () => {
    const h = bridgeAnswering({});
    h.onFetch(() => {
      throw new TypeError("refused");
    });
    const res = await h.request("/telemetry/surroundings");
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "bridge_unavailable" });
  });
});

describe("GET /face/people", () => {
  test("the list passes, typed", async () => {
    const { data } = await treaty(bridgeAnswering(FACES).app).face.people.get();
    expect(data?.people[0]?.name).toBe("Ana");
  });

  test("a malformed list is a 502 bridge_bad_payload", async () => {
    const h = bridgeAnswering({ ...FACES, people: [{ id: "p1" }] });
    const { error } = await treaty(h.app).face.people.get();
    expect(error?.status).toBe(502);
    expect(error?.value).toMatchObject(BAD);
  });

  test("the bridge refusing back's proof is a 502, not the operator's 403", async () => {
    const h = bridgeAnswering({ error: "forbidden" }, 403);
    const { error } = await treaty(h.app).face.people.get();
    expect(error?.status).toBe(502);
    expect(error?.value).toMatchObject({ error: "bridge_error" });
  });
});

describe("the stop path checks nothing", () => {
  test("PARAR answers whatever the bridge said", async () => {
    const h = testApp({ callTool: async () => ({ surprising: ["shape"] }) });
    h.signIn({ id: "u1", role: null });
    const { status, data } = await treaty(h.app)
      .skills({ name: "stop_everything" })
      .invoke.post({});
    expect(status).toBe(200);
    expect(data).toMatchObject({ surprising: ["shape"] });
  });

  test("«Reanudar» passes the bridge's answer through, whatever its shape", async () => {
    const h = bridgeAnswering({ resumed: "yes", stop_standing: "no" });
    const ok = await treaty(h.app).safety.ack.post({ stopped_at: 1 });
    expect(ok.status).toBe(200);
    expect(ok.data).toMatchObject({ resumed: "yes" });

    const refused = bridgeAnswering({ error: "newer_stop" }, 409);
    const r = await treaty(refused.app).safety.ack.post({ stopped_at: 1 });
    expect(r.error?.status).toBe(409);
    expect(r.error?.value).toMatchObject({ error: "newer_stop" });
  });
});
