/**
 * Tests for /skills/:name/invoke's two dispatch gates:
 *   1. the admin-only role check (the audit's top finding -- this route
 *      used to be reachable by any authenticated user with zero LLM
 *      reasoning in between, see the docstring in ./skills.ts)
 *   2. the TypeBox `Value.Check` validation gate ahead of it
 *
 * `@back/lib/auth`'s `auth.api.getSession` normally hits Postgres; mocked
 * here so these are fast unit tests that don't need a live DB. The mock
 * must be registered before `./skills` is imported (it transitively pulls
 * in the real `@back/lib/auth-plugin`, which imports `@back/lib/auth`),
 * hence the dynamic import after `mock.module`.
 */
import { describe, expect, test, mock, beforeEach } from "bun:test";

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
    // the run -- another file's `mock.module("@back/bridge/client", ...)`
    // (the alias form, same resolved file as this relative one) would
    // otherwise "win" or lose the race depending on run order, and a mock
    // missing an export the real module has throws at import time for
    // whichever file loses. Keep this mock's surface complete.
    listTools: mock(async () => []),
    BridgeUnavailableError,
    BridgeToolError,
  };
});

const FIXTURE_PARAMS = { type: "object", properties: {}, additionalProperties: true };

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

mock.module("../skills", () => ({
  getCatalogue: mock(async () => ({
    skills: FIXTURE_SKILLS,
    source: "bridge",
    ageSeconds: 0,
  })),
  getSkill: mock(async (name: string) => FIXTURE_SKILLS.find((s) => s.name === name)),
  listSkills: mock(async () => FIXTURE_SKILLS),
}));

const { skillsRoutes } = await import("./skills");

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
