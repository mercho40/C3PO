/**
 * Tests for /skills/:name/invoke's two dispatch gates:
 *   1. the admin-only role check (the audit's top finding -- this route
 *      used to be reachable by any authenticated user with zero LLM
 *      reasoning in between, see the docstring in ./skills.ts)
 *   2. the TypeBox `Value.Check` validation gate ahead of it
 * and, at the end, the audit row every dispatch writes (fire-and-forget —
 * the stop must never wait on the database).
 *
 * In process, through the real app (`testApp`, src/testing/fakes.ts): a fake
 * session, a recording database under the real chat store, and a bridge whose
 * `callTool` is a bun mock. Every test gets a fresh app, so nothing (a cached
 * catalogue, a pending card, a stop timeout) carries over.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import { PENDING_TTL_MS } from "../agent/confirmations";
import { BridgeToolError, BridgeUnavailableError } from "../bridge/client";
import type { SkillCatalogueEntry } from "../skills";
import { SAFETY_SKILLS } from "../skills/invoke";
import { testApp, type TestApp } from "../testing/fakes";

const FIXTURE_PARAMS = {
  type: "object",
  properties: {},
  additionalProperties: true,
};

function fixtureSkill(
  name: string,
  classification: SkillCatalogueEntry["classification"],
): SkillCatalogueEntry {
  return {
    name,
    description: `Fixture ${name}.`,
    parameters: FIXTURE_PARAMS,
    preconditions: [],
    expectedDurationSeconds: 1,
    cancellable: false,
    typicalFailureModes: [],
    classification,
    dangerLevel: "low" as const,
    status: "real" as const,
    works: { sim: true, real: true },
  };
}

const FIXTURE_SKILLS: SkillCatalogueEntry[] = [
  fixtureSkill("wave", "gesture"),
  fixtureSkill("stop_everything", "safety"),
  fixtureSkill("soft_stop", "safety"),
  {
    ...fixtureSkill("walk_velocity", "locomotion"),
    // Real schema (not the permissive fixture default) so the 422 test
    // below exercises actual Ajv rejection, not just a missing skill.
    parameters: {
      type: "object",
      properties: { vx: { type: "number" } },
      required: ["vx"],
    },
  },
];

let h: TestApp;
beforeEach(() => {
  h = testApp({ skills: FIXTURE_SKILLS });
});

function invoke(name: string, body: unknown) {
  return h.post(`/skills/${name}/invoke`, body);
}

function estop() {
  return h.post("/tasks/t1/cancel", { mode: "estop" });
}

describe("POST /skills/:name/invoke", () => {
  beforeEach(() => {
    h.signIn(null);
    h.setSkills(FIXTURE_SKILLS);
  });

  test("401 when there's no session", async () => {
    const res = await invoke("stop_everything", {});
    expect(res.status).toBe(401);
  });

  test("403 for a non-admin session on a non-safety skill, and never reaches the bridge", async () => {
    h.signIn({ id: "u1", role: null });
    const res = await invoke("wave", {});
    expect(res.status).toBe(403);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe("admin_required");
  });

  test("stop_everything (classification: safety) is reachable by a non-admin session", async () => {
    // The dashboard's PARAR button calls this as any logged-in operator --
    // an e-stop must never be gated behind an extra permission a regular
    // user might lack.
    h.signIn({ id: "u1", role: null });
    const res = await invoke("stop_everything", {});
    expect(res.status).toBe(200);
    const json = (await res.json()) as { called: string };
    expect(json.called).toBe("stop_everything");
  });

  test("PARAR aborts every agent turn in flight before the stop is sent", async () => {
    const { beginTurn, activeCount: activeTurnCount } = h.services.turns;
    const turn = beginTurn();
    h.signIn({ id: "u1", role: null });
    const res = await invoke("stop_everything", {});
    expect(res.status).toBe(200);
    expect(turn.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("soft_stop ends the agent's turns too", async () => {
    const { beginTurn, activeCount: activeTurnCount } = h.services.turns;
    const turn = beginTurn();
    h.signIn({ id: "u1", role: null });
    const res = await invoke("soft_stop", {});
    expect(res.status).toBe(200);
    expect(turn.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("the /tasks estop ends the agent's turns too", async () => {
    const { beginTurn, activeCount: activeTurnCount } = h.services.turns;
    const turn = beginTurn();
    h.signIn({ id: "u1", role: null });
    const res = await estop();
    expect(res.status).toBe(200);
    expect(turn.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("a graceful task cancel leaves the agent's turns alone", async () => {
    const turn = h.services.turns.beginTurn();
    h.signIn({ id: "u1", role: null });
    const res = await h.post("/tasks/t1/cancel", { mode: "graceful" });
    expect(res.status).toBe(200);
    expect(h.callTool).toHaveBeenCalledWith("cancel_task", { task_id: "t1" });
    expect(turn.signal.aborted).toBe(false);
  });

  test("404 for an unknown skill even as admin", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("not_a_real_skill", {});
    expect(res.status).toBe(404);
  });

  test("422 for invalid params even as admin", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("walk_velocity", { vx: "not-a-number" });
    expect(res.status).toBe(422);
  });

  test("admin with valid params reaches the bridge dispatch on a non-safety skill", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("wave", {});
    expect(res.status).toBe(200);
    const json = (await res.json()) as { called: string };
    expect(json.called).toBe("wave");
  });
});

/**
 * The catalogue must not be on the e-stop's critical path.
 *
 * `getSkill` reads a catalogue fetched from the bridge and cached. On a cold
 * start with the bridge unreachable there is nothing cached, so the catalogue
 * is empty and every lookup misses — including `stop_everything`. PARAR then
 * answered `skill_not_found`, which reads as a bug in the button rather than
 * as "the bridge is unreachable", at the single worst moment to hand somebody
 * a misleading error.
 */
describe("PARAR when the catalogue cannot be read", () => {
  beforeEach(() => {
    h.signIn(null);
    h.setSkills([]);
  });

  test("stop_everything still dispatches with an empty catalogue", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("stop_everything", {});

    expect(res.status).not.toBe(404);
    expect(res.status).toBe(200);
  });

  test("and it is still reachable by a NON-admin with an empty catalogue", async () => {
    // The safety exemption must not evaporate because a fetch failed — that
    // would quietly make the e-stop admin-only exactly when things are broken.
    h.signIn({ id: "u1", role: null });
    const res = await invoke("stop_everything", {});

    expect(res.status).not.toBe(403);
    expect(res.status).toBe(200);
  });

  test("an unknown skill is still 404, not dispatched", async () => {
    // The bypass is a named list, not "dispatch anything we cannot find".
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("not_a_real_skill", {});

    expect(res.status).toBe(404);
  });

  test("a non-safety skill is still 404 rather than silently dispatched", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("walk_velocity", { vx: 0.2 });

    expect(res.status).toBe(404);
  });
});

test("the hardcoded safety list agrees with the catalogue's own classification", async () => {
  // The list exists for when the catalogue cannot be read, so it cannot be
  // derived from it — which means the two can drift. This is what catches it:
  // anything the bridge classifies as safety must be in the list, or it loses
  // its exemption precisely when the catalogue is unavailable.
  const classifiedSafety = FIXTURE_SKILLS.filter(
    (s) => s.classification === "safety",
  ).map((s) => s.name);

  for (const name of classifiedSafety) {
    expect(SAFETY_SKILLS.has(name)).toBe(true);
  }
});

/**
 * The direct path is audited — every VR button, the walk loop, bring-up and
 * PARAR used to leave no record at all. And the audit write is never on the
 * stop's critical path.
 */
describe("POST /skills/:name/invoke writes the audit trail", () => {
  beforeEach(() => {
    h.signIn(null);
    h.setSkills(FIXTURE_SKILLS);
  });

  test("a dispatch is logged as source 'direct', attributed to the caller", async () => {
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("wave", {});
    expect(res.status).toBe(200);
    expect(h.rows.length).toBe(1);
    const row = h.rows[0]!;
    expect(row.source).toBe("direct");
    expect(row.userId).toBe("u1");
    expect(row.skillName).toBe("wave");
    expect(row.status).toBe("ok");
    expect(typeof row.durationMs).toBe("number");
    // Stamped when it was SENT: the row is written on settle, and a finish
    // time made a press and its stop look like duplicates (2026-10-15).
    const sent = (row.createdAt as Date).getTime();
    expect(sent + (row.durationMs as number)).toBeLessThanOrEqual(Date.now());
  });

  // created_at has no time zone: the DB default is local time, a Date is UTC.
  test("a row given no startedAt is still stamped from JS, never by the DB default", async () => {
    const before = Date.now();
    await h.deps.chats.logToolCall({
      skillName: "wave",
      status: "ok",
      userId: null,
    });
    const at = h.rows[0]!.createdAt as Date;
    expect(at).toBeInstanceOf(Date);
    expect(at.getTime()).toBeGreaterThanOrEqual(before);
  });

  test("a non-admin's PARAR is logged too, under their name", async () => {
    h.signIn({ id: "u7", role: null });
    await invoke("stop_everything", {});
    expect(h.rows.length).toBe(1);
    expect(h.rows[0]!.userId).toBe("u7");
    expect(h.rows[0]!.skillName).toBe("stop_everything");
  });

  test("a firmware refusal that arrives as a 200 body is logged as an error, with the bridge's code", async () => {
    h.signIn({ id: "u1", role: "admin" });
    h.callTool.mockImplementationOnce(async () => ({
      status: "failed",
      phase: "rpc_error",
      error: "rpc_error_code_7401",
    }));
    const res = await invoke("wave", {});
    expect(res.status).toBe(200);
    expect(h.rows[0]!.status).toBe("error");
    expect(h.rows[0]!.error).toBe("rpc_error_code_7401");
  });

  test("a bridge that cannot be reached is a 502 AND an error row", async () => {
    h.signIn({ id: "u1", role: "admin" });
    h.callTool.mockImplementationOnce(async () => {
      throw new BridgeUnavailableError("down");
    });
    const res = await invoke("wave", {});
    expect(res.status).toBe(502);
    expect(h.rows.length).toBe(1);
    expect(h.rows[0]!.status).toBe("error");
    expect(h.rows[0]!.error).toBe("bridge_unavailable");
  });

  test("the stop answers even when the audit write never completes", async () => {
    h.signIn({ id: "u1", role: null });
    h.db.state.insertHangs = true;
    const res = await Promise.race([
      invoke("stop_everything", {}),
      new Promise<"blocked">((resolve) =>
        setTimeout(() => resolve("blocked"), 500),
      ),
    ]);
    expect(res).not.toBe("blocked");
    expect((res as Response).status).toBe(200);
  });

  test("refusals before dispatch are not dispatches, and are not logged", async () => {
    h.signIn({ id: "u2", role: null });
    expect((await invoke("wave", {})).status).toBe(403);
    h.signIn({ id: "u1", role: "admin" });
    expect((await invoke("walk_velocity", { vx: "fast" })).status).toBe(422);
    expect((await invoke("not_a_real_skill", {})).status).toBe(404);
    expect(h.rows.length).toBe(0);
  });
});

/**
 * PARAR through /skills and the /tasks estop are ONE stop (bridge/stop.ts): the
 * estop used to await the bridge with no bound, so a hung bridge held it for the
 * MCP default while /skills answered 504 after 8 s.
 */
describe("the /tasks estop and /skills PARAR are the same stop", () => {
  let release: (() => void) | undefined;

  beforeEach(() => {
    h = testApp({ skills: FIXTURE_SKILLS, timings: { stopTimeoutMs: 50 } });
    // /tasks sits behind the app's session guard.
    h.signIn({ id: "u1", role: null });
  });
  afterEach(async () => {
    release?.();
    release = undefined;
    await Bun.sleep(5); // its audit row lands here, not in the next test
  });

  /** The next callTool hangs until `release()`, then resolves like the stop did. */
  function bridgeHangs() {
    h.callTool.mockImplementationOnce(
      (name: string) =>
        new Promise((resolve) => {
          release = () => resolve({ called: name, args: {} });
        }),
    );
  }

  async function settled<T>(p: Promise<T>, ms = 2000): Promise<T | "blocked"> {
    return Promise.race([
      p,
      new Promise<"blocked">((r) => setTimeout(() => r("blocked"), ms)),
    ]);
  }

  test("a hung bridge: the estop answers 504 bridge_timeout, like PARAR", async () => {
    bridgeHangs();
    const res = await settled(estop());
    expect(res).not.toBe("blocked");
    expect((res as Response).status).toBe(504);
    expect(await (res as Response).json()).toEqual({
      error: "bridge_timeout",
      task_id: "t1",
    });
  });

  test("a hung bridge: /skills PARAR answers 504 with its own shape", async () => {
    bridgeHangs();
    const res = await settled(invoke("stop_everything", {}));
    expect(res).not.toBe("blocked");
    expect((res as Response).status).toBe(504);
    expect(await (res as Response).text()).toBe(
      JSON.stringify({ error: "bridge_timeout", name: "stop_everything" }),
    );
  });

  test("the timed-out estop keeps running, and is audited when it settles", async () => {
    bridgeHangs();
    expect(((await estop()) as Response).status).toBe(504);
    expect(h.rows.length).toBe(0);
    release!();
    await Bun.sleep(5);
    expect(h.rows.length).toBe(1);
    expect(h.rows[0]!.skillName).toBe("stop_everything");
    expect(h.rows[0]!.source).toBe("direct");
    expect(h.rows[0]!.status).toBe("ok");
  });

  test("an estop answer keeps its shape: mode, task_id, then the bridge's result", async () => {
    const res = await estop();
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(
      JSON.stringify({
        mode: "estop",
        task_id: "t1",
        called: "stop_everything",
        args: {},
      }),
    );
  });

  test("estop failures keep the /tasks error shapes", async () => {
    h.callTool.mockImplementationOnce(async () => {
      throw new BridgeUnavailableError("down");
    });
    let res = await estop();
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({
      error: "bridge_unavailable",
      task_id: "t1",
    });

    h.callTool.mockImplementationOnce(async () => {
      throw new BridgeToolError("stop_everything", "damp failed");
    });
    res = await estop();
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({
      error: "tool_error",
      task_id: "t1",
      detail: "damp failed",
    });
    expect(h.rows.map((r) => r.error)).toEqual([
      "bridge_unavailable",
      "tool_error: damp failed",
    ]);
  });
});

/**
 * The operator's Confirmar: one action. Back grants the refused call on the
 * bridge AND runs it, exactly once, with exactly the refused arguments, and
 * answers with the result and the words the model is told. The bridge owns the
 * grant and the tier check; this route owns who may ask (admins), keeps the
 * secret off the browser, and audits who authorised arm motion.
 */
describe("POST /skills/:name/confirm runs the refused call", () => {
  let forwarded: { url: string; init: RequestInit }[] = [];
  const pick = { label: "Dixie cup", shape: "power", side: "auto" };

  function post(name: string, verb: "confirm" | "cancel", body: unknown) {
    return h.app.handle(
      new Request(`http://localhost/skills/${name}/${verb}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  }
  const refuse = (id = "call_1", args: Record<string, unknown> = pick) =>
    h.services.confirmations.note({
      id,
      tool: "pick",
      args,
      ownerId: "u1",
      chatId: "chat_1",
      loop: false,
    });

  beforeEach(() => {
    forwarded = [];
    h.signIn({ id: "u1", role: "admin" });
    h.setSkills([
      ...FIXTURE_SKILLS,
      { ...fixtureSkill("pick", "task"), tier: "T2" },
    ]);
    h.onFetch(async (url: string, init: RequestInit) => {
      forwarded.push({ url: String(url), init });
      return Response.json({ granted: true, tool: "pick" });
    });
  });

  test("grants and dispatches exactly the refused arguments once, and says the result", async () => {
    refuse();
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });

    expect(res.status).toBe(200);
    expect(forwarded.length).toBe(1);
    expect(forwarded[0].url).toEndWith("/safety/confirm");
    const headers = forwarded[0].init.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    expect(JSON.parse(String(forwarded[0].init.body))).toEqual({
      tool: "pick",
      args: pick,
    });
    expect(h.callTool).toHaveBeenCalledTimes(1);
    expect(h.callTool).toHaveBeenCalledWith("pick", pick);
    const body = (await res.json()) as { result: unknown; message: string };
    expect(body.result).toEqual({ called: "pick", args: pick });
    expect(body.message).toStartWith("El operador confirmó y se ejecutó pick");
    expect(body.message).toContain('"called":"pick"');
    expect(body.message).toContain("No lo vuelvas a llamar");
    await Bun.sleep(0);
    const grant = h.rows.find((r) => r.skillName === "safety_confirm");
    expect(grant?.params).toEqual({ tool: "pick", args: pick, via: "button" });
    expect(grant?.userId).toBe("u1");
  });

  test("a second press runs nothing", async () => {
    refuse();
    await post("pick", "confirm", { callId: "call_1", args: pick });
    const again = await post("pick", "confirm", {
      callId: "call_1",
      args: pick,
    });
    expect(again.status).toBe(409);
    expect(((await again.json()) as { error: string }).error).toBe(
      "not_pending",
    );
    expect(forwarded.length).toBe(1);
    expect(h.callTool).toHaveBeenCalledTimes(1);
  });

  test("two presses at once still run it once", async () => {
    refuse();
    const both = await Promise.all([
      post("pick", "confirm", { callId: "call_1", args: pick }),
      post("pick", "confirm", { callId: "call_1", args: pick }),
    ]);
    expect(both.map((r) => r.status).sort()).toEqual([200, 409]);
    expect(h.callTool).toHaveBeenCalledTimes(1);
  });

  test("arguments other than the refused ones are refused, and the call stays pending", async () => {
    refuse();
    const wrong = await post("pick", "confirm", {
      callId: "call_1",
      args: { ...pick, side: "left" },
    });
    expect(wrong.status).toBe(409);
    expect(forwarded.length).toBe(0);
    expect(h.callTool).not.toHaveBeenCalled();
    // Key order is not a different argument set.
    const right = await post("pick", "confirm", {
      callId: "call_1",
      args: { side: "auto", shape: "power", label: "Dixie cup" },
    });
    expect(right.status).toBe(200);
  });

  test("another tool's name on the same call id runs nothing", async () => {
    refuse();
    const res = await post("wave", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(409);
    expect(h.callTool).not.toHaveBeenCalled();
  });

  test("a non-admin cannot confirm, and the call stays pending", async () => {
    refuse();
    h.signIn({ id: "u1", role: null });
    expect(
      (await post("pick", "confirm", { callId: "call_1", args: pick })).status,
    ).toBe(403);
    expect(forwarded.length).toBe(0);
    expect(h.services.confirmations.list("u1", "chat_1").length).toBe(1);
  });

  test("another admin's refused call is not theirs to confirm", async () => {
    refuse();
    h.signIn({ id: "u9", role: "admin" });
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(409);
    expect(h.callTool).not.toHaveBeenCalled();
  });

  test("no secret: 503, nothing dispatched, and the card is not burned", async () => {
    h = testApp({
      skills: [
        ...FIXTURE_SKILLS,
        { ...fixtureSkill("pick", "task"), tier: "T2" },
      ],
      env: { SAFETY_CONFIRM_SECRET: "" },
    });
    h.signIn({ id: "u1", role: "admin" });
    refuse();
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(503);
    expect(h.callTool).not.toHaveBeenCalled();
    // Still on the list: a configured server would run it on the next press.
    expect(h.services.confirmations.list("u1", "chat_1")).toHaveLength(1);
  });

  test("a refusal older than the window is not confirmable", async () => {
    h.services.confirmations.note(
      {
        id: "old",
        tool: "pick",
        args: pick,
        ownerId: "u1",
        chatId: "chat_1",
        loop: false,
      },
      Date.now() - PENDING_TTL_MS - 1,
    );
    const res = await post("pick", "confirm", { callId: "old", args: pick });
    expect(res.status).toBe(409);
    expect(h.callTool).not.toHaveBeenCalled();
  });

  test("a failed run is reported with the bridge's reason, in words for the model", async () => {
    refuse();
    h.callTool.mockImplementationOnce(async () => {
      throw new BridgeToolError("pick", "target_lost");
    });
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error: string; message: string };
    expect(body.error).toBe("tool_error");
    expect(body.message).toContain("falló: tool_error: target_lost");
  });

  test("Cancelar drops it and runs nothing", async () => {
    refuse();
    const res = await post("pick", "cancel", { callId: "call_1" });
    expect(res.status).toBe(200);
    expect(((await res.json()) as { message: string }).message).toContain(
      "canceló pick",
    );
    const after = await post("pick", "confirm", {
      callId: "call_1",
      args: pick,
    });
    expect(after.status).toBe(409);
    expect(forwarded.length).toBe(0);
    expect(h.callTool).not.toHaveBeenCalled();
  });

  test("a voice-loop call's outcome is fed back to that loop", async () => {
    const fed: string[] = [];
    h.services.confirmations.subscribe((o) => {
      if (o.kind === "loop") fed.push(o.message, o.spoken);
    });
    h.services.confirmations.note({
      id: "v1",
      tool: "pick",
      args: pick,
      ownerId: "u1",
      chatId: null,
      loop: true,
    });
    const res = await post("pick", "confirm", { callId: "v1", args: pick });
    expect(res.status).toBe(200);
    expect(fed[0]).toStartWith("El operador confirmó y se ejecutó pick");
    expect(fed[1]).toBe("Confirmado: ejecuté pick.");
  });

  // 2026-11-13: five refused picks were each announced «Confirmado: ejecuté pick.».
  test("a pick the planner refuses (a 200 with status failed) is said and told as failed", async () => {
    const fed: string[] = [];
    h.services.confirmations.subscribe((o) => {
      if (o.kind === "loop") fed.push(o.message, o.spoken);
    });
    h.services.confirmations.note({
      id: "v2",
      tool: "pick",
      args: pick,
      ownerId: "u1",
      chatId: null,
      loop: true,
    });
    const refused = {
      status: "failed",
      phase: "planning",
      error: "torso_clearance: the shoulder would pass through the torso",
      result: {
        moved: false,
        reason: "torso_clearance",
        note: "Acercate ~0.14 m",
      },
    };
    h.callTool.mockImplementationOnce(async () => refused as never);
    const res = await post("pick", "confirm", { callId: "v2", args: pick });
    // The console card still paints from the 200 body.
    expect(res.status).toBe(200);
    const body = (await res.json()) as { result: unknown; message: string };
    expect(body.result).toEqual(refused);
    expect(body.message).toStartWith("El operador confirmó pick, pero falló");
    expect(fed[0]).toStartWith("El operador confirmó pick, pero falló");
    expect(fed[1]).toBe("Confirmado, pero pick falló: Acercate ~0.14 m");
  });

  // A PARAR during the bridge's spoken warning ends the pick "cancelled".
  test("a confirmed pick stopped before it moved is not said as done", async () => {
    const fed: string[] = [];
    h.services.confirmations.subscribe((o) => {
      if (o.kind === "loop") fed.push(o.message, o.spoken);
    });
    h.services.confirmations.note({
      id: "v3",
      tool: "pick",
      args: pick,
      ownerId: "u1",
      chatId: null,
      loop: true,
    });
    const stopped = {
      status: "cancelled",
      phase: "stopped",
      result: {
        moved: false,
        note: "Stopped during the spoken warning; nothing moved.",
      },
    };
    h.callTool.mockImplementationOnce(async () => stopped as never);
    const res = await post("pick", "confirm", { callId: "v3", args: pick });
    expect(res.status).toBe(200);
    expect(fed[0]).toStartWith("El operador confirmó pick, pero falló");
    expect(fed[1]).toBe("Confirmado, pero pick se canceló.");
  });

  // The bridge mints a fresh grant on Confirmar, so only back can void the card.
  test("a card asked before PARAR is not confirmable after «Reanudar»", async () => {
    refuse();
    expect((await invoke("stop_everything", {})).status).toBe(200);
    const ack = await h.post("/safety/ack");
    expect(ack.status).toBe(200);
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("not_pending");
    expect(h.callTool).not.toHaveBeenCalledWith("pick", pick);
  });

  test("PARAR alone voids it", async () => {
    refuse();
    await invoke("stop_everything", {});
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(409);
  });

  test("so does «Reanudar» after a stop back never saw (headset, remote)", async () => {
    refuse();
    await h.post("/safety/ack");
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(409);
    expect(forwarded.some((f) => f.url.endsWith("/safety/confirm"))).toBe(
      false,
    );
  });
});

describe("POST /skills/:name/confirm-face: an enrolled face instead of the click", () => {
  let forwarded: { url: string; body: unknown }[] = [];
  let face: () => Response;
  const args = { seconds: 5 };

  const post = (body: unknown) =>
    h.app.handle(
      new Request("http://localhost/skills/start_walking_waist/confirm-face", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );

  beforeEach(() => {
    forwarded = [];
    h.signIn({ id: "u1", role: "admin" });
    h.setSkills([
      ...FIXTURE_SKILLS,
      {
        ...fixtureSkill("start_walking_waist", "posture"),
        tier: "T1",
        dangerLevel: "high",
      },
    ]);
    face = () =>
      Response.json({ passed: false, reason: "operator_face_not_seen" });
    h.onFetch(async (url: string, init: RequestInit) => {
      forwarded.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return String(url).endsWith("/face/confirm_check")
        ? face()
        : Response.json({ granted: true });
    });
    h.services.confirmations.note({
      id: "call_1",
      tool: "start_walking_waist",
      args,
      ownerId: "u1",
      chatId: "chat_1",
      loop: false,
    });
  });

  test("no face yet: keep waiting, nothing granted or run, the card stays", async () => {
    const res = await post({ callId: "call_1", args });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      passed: false,
      reason: "operator_face_not_seen",
    });
    expect(forwarded.map((f) => f.url)).toEqual([
      expect.stringContaining("/face/confirm_check"),
    ]);
    expect(h.callTool).not.toHaveBeenCalled();
    expect(h.services.confirmations.list("u1", "chat_1")).toHaveLength(1);
  });

  test("a face: granted via face and run once, like the click", async () => {
    face = () =>
      Response.json({ passed: true, person: { id: "p", name: "Oliver" } });
    const res = await post({ callId: "call_1", args });
    const again = await post({ callId: "call_1", args });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      passed: boolean;
      person: string;
      message: string;
    };
    expect(body.passed).toBe(true);
    expect(body.person).toBe("Oliver");
    expect(body.message).toStartWith("El operador confirmó y se ejecutó");
    expect(forwarded[1]).toEqual({
      url: expect.stringContaining("/safety/confirm"),
      body: { tool: "start_walking_waist", args, via: "face" },
    });
    expect(h.callTool).toHaveBeenCalledTimes(1);
    expect(again.status).toBe(409);
  });

  test("the bridge's refusal (face confirm off) passes through and runs nothing", async () => {
    face = () => Response.json({ error: "face_confirm_off" }, { status: 403 });
    const res = await post({ callId: "call_1", args });
    expect(res.status).toBe(403);
    expect(h.callTool).not.toHaveBeenCalled();
  });

  test("other arguments than the card's, or a non-admin: nothing is asked", async () => {
    const other = await post({ callId: "call_1", args: { seconds: 9 } });
    h.signIn({ id: "u1", role: "user" });
    const user = await post({ callId: "call_1", args });
    expect(other.status).toBe(409);
    expect(user.status).toBe(403);
    expect(forwarded).toEqual([]);
  });
});

describe("confirm-face on an arm/hand call: the person steps back first", () => {
  let nearest: (number | null)[] = [];
  let urls: string[] = [];
  const args = { side: "right", shape: "v_sign" };

  const post = () =>
    h.app.handle(
      new Request("http://localhost/skills/set_hand/confirm-face", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ callId: "call_h", args }),
      }),
    );

  beforeEach(() => {
    urls = [];
    h = testApp({
      skills: [
        ...FIXTURE_SKILLS,
        { ...fixtureSkill("set_hand", "gesture"), tier: "T2" },
      ],
      timings: { faceStepBack: { timeoutMs: 50, everyMs: 1 } },
    });
    h.signIn({ id: "u1", role: "admin" });
    h.onFetch(async (url: string) => {
      const u = String(url);
      urls.push(u);
      if (u.endsWith("/face/confirm_check"))
        return Response.json({
          passed: true,
          person: { name: "Oliver" },
          reach_m: 0.8,
        });
      if (u.endsWith("/safety/status"))
        return Response.json({
          nearest_person_m: nearest.length > 1 ? nearest.shift() : nearest[0],
        });
      return Response.json({ granted: true });
    });
    h.services.confirmations.note({
      id: "call_h",
      tool: "set_hand",
      args,
      ownerId: "u1",
      chatId: "chat_1",
      loop: false,
    });
  });

  test("says so, waits until nobody is within reach, then runs it once", async () => {
    nearest = [0.6, 0.6, null];
    const res = await post();
    expect(res.status).toBe(200);
    expect(((await res.json()) as { passed: boolean }).passed).toBe(true);
    const calls = h.callTool.mock.calls.map((c) => c[0]);
    expect(calls).toEqual(["say", "set_hand"]);
    expect(urls.filter((u) => u.endsWith("/safety/status")).length).toBe(3);
    expect(urls[urls.length - 1]).toEndWith("/safety/confirm");
  });

  test("still in reach at the deadline: nothing runs and the card stays", async () => {
    nearest = [0.5];
    const res = await post();
    expect(await res.json()).toEqual({
      passed: false,
      reason: "still_in_reach",
    });
    expect(h.callTool.mock.calls.map((c) => c[0])).toEqual(["say"]);
    expect(urls.some((u) => u.endsWith("/safety/confirm"))).toBe(false);
    expect(h.services.confirmations.list("u1", "chat_1")).toHaveLength(1);
  });
});

/**
 * 1.3: the bridge gates a high-danger T1 for every MCP caller, so an admin's
 * /invoke (the console and headset bring-up) grants that exact call first.
 * The admin's click is the confirmation; T2 keeps the two-step Confirmar.
 */
describe("POST /skills/:name/invoke pre-grants a high-danger T1", () => {
  let forwarded: { url: string; body: unknown }[] = [];
  let bridgeAnswer: () => Response;

  const gated = (
    name: string,
    tier: "T1" | "T2",
    dangerLevel: "medium" | "high",
  ): SkillCatalogueEntry => ({
    ...fixtureSkill(name, "posture"),
    tier,
    dangerLevel,
    parameters: {
      type: "object",
      properties: { seconds: { type: "number", default: 5 } },
    },
  });

  beforeEach(() => {
    forwarded = [];
    h.signIn({ id: "u1", role: "admin" });
    bridgeAnswer = () => Response.json({ granted: true });
    h.setSkills([
      ...FIXTURE_SKILLS,
      gated("start_walking_waist", "T1", "high"),
      gated("prepare", "T1", "medium"),
      gated("move_arm", "T2", "high"),
      { ...gated("old_bridge_tool", "T1", "high"), tier: undefined },
    ]);
    h.onFetch(async (url: string, init: RequestInit) => {
      forwarded.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return bridgeAnswer();
    });
  });

  test("grants the exact, default-filled arguments before dispatching, and audits it", async () => {
    const res = await invoke("start_walking_waist", {});
    expect(res.status).toBe(200);
    expect(forwarded).toEqual([
      {
        url: expect.stringContaining("/safety/confirm"),
        body: { tool: "start_walking_waist", args: { seconds: 5 } },
      },
    ]);
    expect(h.callTool).toHaveBeenCalledWith("start_walking_waist", {
      seconds: 5,
    });
    await Bun.sleep(0);
    expect(h.rows.some((r) => r.skillName === "safety_confirm")).toBe(true);
  });

  for (const code of [503, 403]) {
    test(`a ${code} from the grant is passed through and nothing is dispatched`, async () => {
      bridgeAnswer = () => Response.json({ error: "nope" }, { status: code });
      const res = await invoke("start_walking_waist", {});
      expect(res.status).toBe(code);
      expect(h.callTool).not.toHaveBeenCalled();
    });
  }

  test("no secret: 503 and nothing dispatched", async () => {
    h = testApp({
      skills: [...FIXTURE_SKILLS, gated("start_walking_waist", "T1", "high")],
      env: { SAFETY_CONFIRM_SECRET: "" },
    });
    h.signIn({ id: "u1", role: "admin" });
    const res = await invoke("start_walking_waist", {});
    expect(res.status).toBe(503);
    expect(h.callTool).not.toHaveBeenCalled();
  });

  for (const name of ["prepare", "move_arm", "wave", "old_bridge_tool"]) {
    test(`${name} is never pre-granted`, async () => {
      await invoke(name, {});
      expect(forwarded.length).toBe(0);
      expect(h.callTool).toHaveBeenCalled();
    });
  }

  test("a non-admin is refused before any grant", async () => {
    h.signIn({ id: "u2", role: null });
    const res = await invoke("start_walking_waist", {});
    expect(res.status).toBe(403);
    expect(forwarded.length).toBe(0);
    expect(h.callTool).not.toHaveBeenCalled();
  });
});
