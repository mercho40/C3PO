/**
 * Tests for /skills/:name/invoke's two dispatch gates:
 *   1. the admin-only role check (the audit's top finding -- this route
 *      used to be reachable by any authenticated user with zero LLM
 *      reasoning in between, see the docstring in ./skills.ts)
 *   2. the TypeBox `Value.Check` validation gate ahead of it
 * and, at the end, the audit row every dispatch writes (fire-and-forget —
 * the stop must never wait on the database).
 *
 * `#back/lib/auth`'s `auth.api.getSession` normally hits Postgres; mocked
 * here so these are fast unit tests that don't need a live DB. The mock
 * must be registered before `./skills` is imported (it transitively pulls
 * in the real `#back/lib/auth-plugin`, which imports `#back/lib/auth`),
 * hence the dynamic import after `mock.module`.
 */
import { describe, expect, test, mock, beforeEach } from "bun:test";

let sessionUser: { id: string; role: string | null } | null = null;

mock.module("#back/lib/auth.js", () => ({
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

mock.module("../bridge/client", () => {
  class BridgeUnavailableError extends Error {}
  class BridgeToolError extends Error {
    constructor(
      readonly tool: string,
      readonly detail: string,
    ) {
      super(`tool_error: ${tool}`);
    }
  }
  return {
    callTool: mock(async (name: string, args: Record<string, unknown>) => ({
      called: name,
      args,
    })),
    // Not used directly by these tests (`../skills` is mocked below instead),
    // but `bun test` shares one module registry across every test file in
    // the run -- another file's `mock.module("#back/bridge/client.js", ...)`
    // (the alias form, same resolved file as this relative one) would
    // otherwise "win" or lose the race depending on run order, and a mock
    // missing an export the real module has throws at import time for
    // whichever file loses. Keep this mock's surface complete.
    listTools: mock(async () => []),
    BridgeUnavailableError,
    BridgeToolError,
  };
});

const FIXTURE_PARAMS = {
  type: "object",
  properties: {},
  additionalProperties: true,
};

function fixtureSkill(name: string, classification: string) {
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

const FIXTURE_SKILLS = [
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

//: Set empty to simulate a cold start with the bridge unreachable: the
//: catalogue is fetched from the bridge and cached, so with nothing cached
//: every lookup misses — including the e-stop's.
let catalogueSkills = FIXTURE_SKILLS;

mock.module("../skills", () => ({
  getCatalogue: mock(async () => ({
    skills: catalogueSkills,
    source: "bridge",
    ageSeconds: 0,
  })),
  getSkill: mock(async (name: string) =>
    catalogueSkills.find((s) => s.name === name),
  ),
  listSkills: mock(async () => catalogueSkills),
}));

//: What `logToolCall` wrote: every `db.insert(table).values(row)` lands here.
//: The real `logToolCall` runs (so its body-failure reading is exercised too);
//: only the database underneath it is fake. `bun test` shares one module
//: registry, so this mock's surface matches audit.test.ts's.
let inserted: Record<string, unknown>[] = [];
//: Set to make every insert hang forever — the stop must not wait on it.
let insertHangs = false;

function thenable(result: unknown) {
  const self: Record<string, unknown> = {
    then: (
      resolve: (v: unknown) => unknown,
      reject: (e: unknown) => unknown,
    ) =>
      insertHangs
        ? new Promise(() => {})
        : Promise.resolve(result).then(resolve, reject),
  };
  return new Proxy(self, {
    get: (obj, prop) => (prop === "then" ? obj.then : () => thenable(result)),
  });
}

mock.module("#back/db/drizzle.js", () => ({
  db: {
    insert: () => ({
      values: (row: Record<string, unknown>) => {
        inserted.push(row);
        return thenable([]);
      },
    }),
    select: () => thenable([]),
    update: () => thenable([]),
    delete: () => thenable([]),
    query: { chat: { findFirst: async () => undefined } },
  },
}));

const { skillsRoutes } = await import("./skills");
const confirmations = await import("../agent/confirmations");
const bridge = (await import("../bridge/client")) as unknown as {
  callTool: ReturnType<typeof mock>;
  BridgeUnavailableError: new (message?: string) => Error;
};
const bridgeErrors = (await import("../bridge/client")) as unknown as {
  BridgeToolError: new (tool: string, detail: string) => Error;
};

function invoke(name: string, body: unknown) {
  return skillsRoutes.handle(
    new Request(`http://localhost/skills/${name}/invoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

describe("POST /skills/:name/invoke", () => {
  beforeEach(() => {
    sessionUser = null;
    catalogueSkills = FIXTURE_SKILLS;
  });

  test("401 when there's no session", async () => {
    const res = await invoke("stop_everything", {});
    expect(res.status).toBe(401);
  });

  test("403 for a non-admin session on a non-safety skill, and never reaches the bridge", async () => {
    sessionUser = { id: "u1", role: null };
    const res = await invoke("wave", {});
    expect(res.status).toBe(403);
    const json = (await res.json()) as { error: string };
    expect(json.error).toBe("admin_required");
  });

  test("stop_everything (classification: safety) is reachable by a non-admin session", async () => {
    // The dashboard's PARAR button calls this as any logged-in operator --
    // an e-stop must never be gated behind an extra permission a regular
    // user might lack.
    sessionUser = { id: "u1", role: null };
    const res = await invoke("stop_everything", {});
    expect(res.status).toBe(200);
    const json = (await res.json()) as { called: string };
    expect(json.called).toBe("stop_everything");
  });

  test("PARAR aborts every agent turn in flight before the stop is sent", async () => {
    const { beginTurn, activeTurnCount } = await import("../agent/turns");
    const turn = beginTurn();
    sessionUser = { id: "u1", role: null };
    const res = await invoke("stop_everything", {});
    expect(res.status).toBe(200);
    expect(turn.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("soft_stop ends the agent's turns too", async () => {
    const { beginTurn, activeTurnCount } = await import("../agent/turns");
    const turn = beginTurn();
    sessionUser = { id: "u1", role: null };
    const res = await invoke("soft_stop", {});
    expect(res.status).toBe(200);
    expect(turn.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("the /tasks estop ends the agent's turns too", async () => {
    const { beginTurn, activeTurnCount } = await import("../agent/turns");
    const { tasksRoutes } = await import("./tasks");
    const turn = beginTurn();
    const res = await tasksRoutes.handle(
      new Request("http://localhost/tasks/t1/cancel", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "estop" }),
      }),
    );
    expect(res.status).toBe(200);
    expect(turn.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("a graceful task cancel leaves the agent's turns alone", async () => {
    const { beginTurn, abortAllTurns } = await import("../agent/turns");
    const { tasksRoutes } = await import("./tasks");
    const turn = beginTurn();
    await tasksRoutes.handle(
      new Request("http://localhost/tasks/t1/cancel", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode: "graceful" }),
      }),
    );
    expect(turn.signal.aborted).toBe(false);
    abortAllTurns("test cleanup");
  });

  test("404 for an unknown skill even as admin", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await invoke("not_a_real_skill", {});
    expect(res.status).toBe(404);
  });

  test("422 for invalid params even as admin", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await invoke("walk_velocity", { vx: "not-a-number" });
    expect(res.status).toBe(422);
  });

  test("admin with valid params reaches the bridge dispatch on a non-safety skill", async () => {
    sessionUser = { id: "u1", role: "admin" };
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
    sessionUser = null;
    catalogueSkills = [];
  });

  test("stop_everything still dispatches with an empty catalogue", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await invoke("stop_everything", {});

    expect(res.status).not.toBe(404);
    expect(res.status).toBe(200);
  });

  test("and it is still reachable by a NON-admin with an empty catalogue", async () => {
    // The safety exemption must not evaporate because a fetch failed — that
    // would quietly make the e-stop admin-only exactly when things are broken.
    sessionUser = { id: "u1", role: null };
    const res = await invoke("stop_everything", {});

    expect(res.status).not.toBe(403);
    expect(res.status).toBe(200);
  });

  test("an unknown skill is still 404, not dispatched", async () => {
    // The bypass is a named list, not "dispatch anything we cannot find".
    sessionUser = { id: "u1", role: "admin" };
    const res = await invoke("not_a_real_skill", {});

    expect(res.status).toBe(404);
  });

  test("a non-safety skill is still 404 rather than silently dispatched", async () => {
    sessionUser = { id: "u1", role: "admin" };
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

  const { SAFETY_SKILLS } = await import("./skills");
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
    sessionUser = null;
    catalogueSkills = FIXTURE_SKILLS;
    inserted = [];
    insertHangs = false;
  });

  test("a dispatch is logged as source 'direct', attributed to the caller", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await invoke("wave", {});
    expect(res.status).toBe(200);
    expect(inserted.length).toBe(1);
    const row = inserted[0]!;
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

  test("a non-admin's PARAR is logged too, under their name", async () => {
    sessionUser = { id: "u7", role: null };
    await invoke("stop_everything", {});
    expect(inserted.length).toBe(1);
    expect(inserted[0]!.userId).toBe("u7");
    expect(inserted[0]!.skillName).toBe("stop_everything");
  });

  test("a firmware refusal that arrives as a 200 body is logged as an error, with the bridge's code", async () => {
    sessionUser = { id: "u1", role: "admin" };
    bridge.callTool.mockImplementationOnce(async () => ({
      status: "failed",
      phase: "rpc_error",
      error: "rpc_error_code_7401",
    }));
    const res = await invoke("wave", {});
    expect(res.status).toBe(200);
    expect(inserted[0]!.status).toBe("error");
    expect(inserted[0]!.error).toBe("rpc_error_code_7401");
  });

  test("a bridge that cannot be reached is a 502 AND an error row", async () => {
    sessionUser = { id: "u1", role: "admin" };
    bridge.callTool.mockImplementationOnce(async () => {
      throw new bridge.BridgeUnavailableError("down");
    });
    const res = await invoke("wave", {});
    expect(res.status).toBe(502);
    expect(inserted.length).toBe(1);
    expect(inserted[0]!.status).toBe("error");
    expect(inserted[0]!.error).toBe("bridge_unavailable");
  });

  test("the stop answers even when the audit write never completes", async () => {
    sessionUser = { id: "u1", role: null };
    insertHangs = true;
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
    sessionUser = { id: "u2", role: null };
    expect((await invoke("wave", {})).status).toBe(403);
    sessionUser = { id: "u1", role: "admin" };
    expect((await invoke("walk_velocity", { vx: "fast" })).status).toBe(422);
    expect((await invoke("not_a_real_skill", {})).status).toBe(404);
    expect(inserted.length).toBe(0);
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
  const realFetch = globalThis.fetch;
  let forwarded: { url: string; init: RequestInit }[] = [];
  const pick = { label: "Dixie cup", shape: "power", side: "auto" };

  function post(name: string, verb: "confirm" | "cancel", body: unknown) {
    return skillsRoutes.handle(
      new Request(`http://localhost/skills/${name}/${verb}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  }
  const refuse = (id = "call_1", args: Record<string, unknown> = pick) =>
    confirmations.notePending({
      id,
      tool: "pick",
      args,
      ownerId: "u1",
      chatId: "chat_1",
      loop: false,
    });

  beforeEach(() => {
    forwarded = [];
    inserted = [];
    bridge.callTool.mockClear();
    confirmations.clearPending();
    sessionUser = { id: "u1", role: "admin" };
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    catalogueSkills = [
      ...FIXTURE_SKILLS,
      { ...fixtureSkill("pick", "manipulation"), tier: "T2" },
    ] as typeof FIXTURE_SKILLS;
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      forwarded.push({ url: String(url), init });
      return Response.json({ granted: true, tool: "pick" });
    }) as typeof fetch;
  });

  test("grants and dispatches exactly the refused arguments once, and says the result", async () => {
    refuse();
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;

    expect(res.status).toBe(200);
    expect(forwarded.length).toBe(1);
    expect(forwarded[0].url).toEndWith("/safety/confirm");
    const headers = forwarded[0].init.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    expect(JSON.parse(String(forwarded[0].init.body))).toEqual({
      tool: "pick",
      args: pick,
    });
    expect(bridge.callTool).toHaveBeenCalledTimes(1);
    expect(bridge.callTool).toHaveBeenCalledWith("pick", pick);
    const body = (await res.json()) as { result: unknown; message: string };
    expect(body.result).toEqual({ called: "pick", args: pick });
    expect(body.message).toStartWith("El operador confirmó y se ejecutó pick");
    expect(body.message).toContain('"called":"pick"');
    expect(body.message).toContain("No lo vuelvas a llamar");
    await Bun.sleep(0);
    const grant = inserted.find((r) => r.skillName === "safety_confirm");
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
    globalThis.fetch = realFetch;
    expect(again.status).toBe(409);
    expect(((await again.json()) as { error: string }).error).toBe(
      "not_pending",
    );
    expect(forwarded.length).toBe(1);
    expect(bridge.callTool).toHaveBeenCalledTimes(1);
  });

  test("two presses at once still run it once", async () => {
    refuse();
    const both = await Promise.all([
      post("pick", "confirm", { callId: "call_1", args: pick }),
      post("pick", "confirm", { callId: "call_1", args: pick }),
    ]);
    globalThis.fetch = realFetch;
    expect(both.map((r) => r.status).sort()).toEqual([200, 409]);
    expect(bridge.callTool).toHaveBeenCalledTimes(1);
  });

  test("arguments other than the refused ones are refused, and the call stays pending", async () => {
    refuse();
    const wrong = await post("pick", "confirm", {
      callId: "call_1",
      args: { ...pick, side: "left" },
    });
    expect(wrong.status).toBe(409);
    expect(forwarded.length).toBe(0);
    expect(bridge.callTool).not.toHaveBeenCalled();
    // Key order is not a different argument set.
    const right = await post("pick", "confirm", {
      callId: "call_1",
      args: { side: "auto", shape: "power", label: "Dixie cup" },
    });
    globalThis.fetch = realFetch;
    expect(right.status).toBe(200);
  });

  test("another tool's name on the same call id runs nothing", async () => {
    refuse();
    const res = await post("wave", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(409);
    expect(bridge.callTool).not.toHaveBeenCalled();
  });

  test("a non-admin cannot confirm, and the call stays pending", async () => {
    refuse();
    sessionUser = { id: "u1", role: null };
    expect(
      (await post("pick", "confirm", { callId: "call_1", args: pick })).status,
    ).toBe(403);
    globalThis.fetch = realFetch;
    expect(forwarded.length).toBe(0);
    expect(confirmations.listPending("u1", "chat_1").length).toBe(1);
  });

  test("another admin's refused call is not theirs to confirm", async () => {
    refuse();
    sessionUser = { id: "u9", role: "admin" };
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(409);
    expect(bridge.callTool).not.toHaveBeenCalled();
  });

  test("no secret: 503, nothing dispatched, and the card still works afterwards", async () => {
    refuse();
    delete process.env.SAFETY_CONFIRM_SECRET;
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    expect(res.status).toBe(503);
    expect(bridge.callTool).not.toHaveBeenCalled();
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    const retry = await post("pick", "confirm", {
      callId: "call_1",
      args: pick,
    });
    globalThis.fetch = realFetch;
    expect(retry.status).toBe(200);
    expect(bridge.callTool).toHaveBeenCalledTimes(1);
  });

  test("a refusal older than the window is not confirmable", async () => {
    confirmations.notePending(
      {
        id: "old",
        tool: "pick",
        args: pick,
        ownerId: "u1",
        chatId: "chat_1",
        loop: false,
      },
      Date.now() - confirmations.PENDING_TTL_MS - 1,
    );
    const res = await post("pick", "confirm", { callId: "old", args: pick });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(409);
    expect(bridge.callTool).not.toHaveBeenCalled();
  });

  test("a failed run is reported with the bridge's reason, in words for the model", async () => {
    refuse();
    bridge.callTool.mockImplementationOnce(async () => {
      throw new bridgeErrors.BridgeToolError("pick", "target_lost");
    });
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;
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
    globalThis.fetch = realFetch;
    expect(after.status).toBe(409);
    expect(forwarded.length).toBe(0);
    expect(bridge.callTool).not.toHaveBeenCalled();
  });

  test("a voice-loop call's outcome is fed back to that loop", async () => {
    const fed: string[] = [];
    confirmations.onLoopOutcome((message, spoken) => fed.push(message, spoken));
    confirmations.notePending({
      id: "v1",
      tool: "pick",
      args: pick,
      ownerId: "u1",
      chatId: null,
      loop: true,
    });
    const res = await post("pick", "confirm", { callId: "v1", args: pick });
    globalThis.fetch = realFetch;
    confirmations.onLoopOutcome(null);
    expect(res.status).toBe(200);
    expect(fed[0]).toStartWith("El operador confirmó y se ejecutó pick");
    expect(fed[1]).toBe("Confirmado: ejecuté pick.");
  });

  // The bridge mints a fresh grant on Confirmar, so only back can void the card.
  test("a card asked before PARAR is not confirmable after «Reanudar»", async () => {
    const { safetyRoutes } = await import("./safety");
    refuse();
    expect((await invoke("stop_everything", {})).status).toBe(200);
    const ack = await safetyRoutes.handle(
      new Request("http://localhost/safety/ack", { method: "POST" }),
    );
    expect(ack.status).toBe(200);
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("not_pending");
    expect(bridge.callTool).not.toHaveBeenCalledWith("pick", pick);
  });

  test("PARAR alone voids it", async () => {
    refuse();
    await invoke("stop_everything", {});
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(409);
  });

  test("so does «Reanudar» after a stop back never saw (headset, remote)", async () => {
    const { safetyRoutes } = await import("./safety");
    refuse();
    await safetyRoutes.handle(
      new Request("http://localhost/safety/ack", { method: "POST" }),
    );
    const res = await post("pick", "confirm", { callId: "call_1", args: pick });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(409);
    expect(forwarded.some((f) => f.url.endsWith("/safety/confirm"))).toBe(
      false,
    );
  });
});

/**
 * 1.3: the bridge gates a high-danger T1 for every MCP caller, so an admin's
 * /invoke (the console and headset bring-up) grants that exact call first.
 * The admin's click is the confirmation; T2 keeps the two-step Confirmar.
 */
describe("POST /skills/:name/invoke pre-grants a high-danger T1", () => {
  const realFetch = globalThis.fetch;
  let forwarded: { url: string; body: unknown }[] = [];
  let bridgeAnswer: () => Response;

  const gated = (name: string, tier: string, dangerLevel: string) => ({
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
    inserted = [];
    bridge.callTool.mockClear();
    sessionUser = { id: "u1", role: "admin" };
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    bridgeAnswer = () => Response.json({ granted: true });
    catalogueSkills = [
      ...FIXTURE_SKILLS,
      gated("start_walking_waist", "T1", "high"),
      gated("prepare", "T1", "medium"),
      gated("move_arm", "T2", "high"),
      { ...gated("old_bridge_tool", "T1", "high"), tier: undefined },
    ] as typeof FIXTURE_SKILLS;
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      forwarded.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return bridgeAnswer();
    }) as typeof fetch;
  });

  test("grants the exact, default-filled arguments before dispatching, and audits it", async () => {
    const res = await invoke("start_walking_waist", {});
    globalThis.fetch = realFetch;
    expect(res.status).toBe(200);
    expect(forwarded).toEqual([
      {
        url: expect.stringContaining("/safety/confirm"),
        body: { tool: "start_walking_waist", args: { seconds: 5 } },
      },
    ]);
    expect(bridge.callTool).toHaveBeenCalledWith("start_walking_waist", {
      seconds: 5,
    });
    await Bun.sleep(0);
    expect(inserted.some((r) => r.skillName === "safety_confirm")).toBe(true);
  });

  for (const code of [503, 403]) {
    test(`a ${code} from the grant is passed through and nothing is dispatched`, async () => {
      bridgeAnswer = () => Response.json({ error: "nope" }, { status: code });
      const res = await invoke("start_walking_waist", {});
      globalThis.fetch = realFetch;
      expect(res.status).toBe(code);
      expect(bridge.callTool).not.toHaveBeenCalled();
    });
  }

  test("no secret: 503 and nothing dispatched", async () => {
    delete process.env.SAFETY_CONFIRM_SECRET;
    const res = await invoke("start_walking_waist", {});
    globalThis.fetch = realFetch;
    expect(res.status).toBe(503);
    expect(bridge.callTool).not.toHaveBeenCalled();
  });

  for (const name of ["prepare", "move_arm", "wave", "old_bridge_tool"]) {
    test(`${name} is never pre-granted`, async () => {
      await invoke(name, {});
      globalThis.fetch = realFetch;
      expect(forwarded.length).toBe(0);
      expect(bridge.callTool).toHaveBeenCalled();
    });
  }

  test("a non-admin is refused before any grant", async () => {
    sessionUser = { id: "u2", role: null };
    const res = await invoke("start_walking_waist", {});
    globalThis.fetch = realFetch;
    expect(res.status).toBe(403);
    expect(forwarded.length).toBe(0);
    expect(bridge.callTool).not.toHaveBeenCalled();
  });
});
