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
const bridge = (await import("../bridge/client")) as unknown as {
  callTool: ReturnType<typeof mock>;
  BridgeUnavailableError: new (message?: string) => Error;
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
 * The console's half of the T2 gate. The bridge owns the grant and the tier
 * check; this route owns who may ask (admins) and keeps the secret off the
 * browser. Every confirmation is audited: who authorised arm motion is the
 * row worth having afterwards.
 */
describe("POST /skills/:name/confirm", () => {
  const realFetch = globalThis.fetch;
  let forwarded: { url: string; init: RequestInit }[] = [];

  function confirm(name: string, body?: unknown) {
    return skillsRoutes.handle(
      new Request(`http://localhost/skills/${name}/confirm`, {
        method: "POST",
        ...(body === undefined
          ? {}
          : {
              headers: { "content-type": "application/json" },
              body: JSON.stringify(body),
            }),
      }),
    );
  }

  beforeEach(() => {
    forwarded = [];
    inserted = [];
    process.env.SAFETY_CONFIRM_SECRET = "s3cret";
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      forwarded.push({ url: String(url), init });
      return Response.json({ granted: true, tool: "move_arm" });
    }) as typeof fetch;
  });

  test("a non-admin cannot confirm", async () => {
    sessionUser = { id: "u2", role: null };
    expect((await confirm("move_arm")).status).toBe(403);
    expect(forwarded.length).toBe(0);
  });

  test("no secret configured: refused, nothing forwarded", async () => {
    sessionUser = { id: "u1", role: "admin" };
    delete process.env.SAFETY_CONFIRM_SECRET;
    expect((await confirm("move_arm")).status).toBe(503);
    expect(forwarded.length).toBe(0);
  });

  test("an admin's confirmation reaches the bridge with the secret, and is audited", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await confirm("move_arm");
    globalThis.fetch = realFetch;

    expect(res.status).toBe(200);
    expect(forwarded[0].url).toEndWith("/safety/confirm");
    const headers = forwarded[0].init.headers as Record<string, string>;
    expect(headers["x-c3po-confirm-secret"]).toBe("s3cret");
    // No body (an old console tab): the grant covers the defaults only.
    expect(JSON.parse(String(forwarded[0].init.body))).toEqual({
      tool: "move_arm",
      args: {},
    });
    await Bun.sleep(0);
    expect(inserted.some((r) => r.skillName === "safety_confirm")).toBe(true);
  });

  test("the refused call's arguments are forwarded, and audited with the grant", async () => {
    sessionUser = { id: "u1", role: "admin" };
    const res = await confirm("move_arm", { args: { side: "right" } });
    globalThis.fetch = realFetch;

    expect(res.status).toBe(200);
    expect(JSON.parse(String(forwarded[0].init.body))).toEqual({
      tool: "move_arm",
      args: { side: "right" },
    });
    await Bun.sleep(0);
    const row = inserted.find((r) => r.skillName === "safety_confirm");
    expect(row?.params).toEqual({ tool: "move_arm", args: { side: "right" } });
  });

  test("the bridge's refusal of the arguments reaches the console", async () => {
    sessionUser = { id: "u1", role: "admin" };
    globalThis.fetch = (async () =>
      Response.json(
        { error: "invalid_arguments", tool: "move_arm" },
        { status: 400 },
      )) as unknown as typeof fetch;
    const res = await confirm("move_arm", { args: { side: 3 } });
    globalThis.fetch = realFetch;
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe(
      "invalid_arguments",
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
