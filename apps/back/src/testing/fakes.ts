/**
 * Test doubles for the app's edges, and `testApp`: the real app over them.
 *
 * Tests used to reach the app's collaborators by replacing modules
 * (`mock.module("#back/lib/auth.js")`, `#back/db/drizzle.js`,
 * `../bridge/client`) — process-wide in bun, so the last file to mock a path
 * won and every mock had to carry the real module's whole surface — or by
 * patching `globalThis.fetch`, or by importing a module in a child process to
 * get one built from a different environment. `createApp(deps)` takes its
 * edges as arguments, so a test builds its own app, in process, and nothing it
 * does leaks into the next file.
 *
 * Imported by tests only; nothing in the app imports this.
 */

import { mock } from "bun:test";
import type { LanguageModel } from "ai";

import { createApp, createServices, type AppDeps } from "#back/app.js";
import type { AgentModels } from "#back/agent/models.js";
import type { BridgeTool } from "#back/bridge/client.js";
import { createBridgeHttp } from "#back/bridge/http.js";
import { loadConfig, type Config } from "#back/config.js";
import { createAuditReader } from "#back/db/audit.js";
import { createChatStore } from "#back/db/chats.js";
import type { Db } from "#back/db/drizzle.js";
import type { AuthSession, SessionSource } from "#back/lib/auth-plugin.js";
import type { SkillCatalogueEntry } from "#back/skills/index.js";
import { createTranscriber } from "#back/voice/transcribe.js";

/** The four variables `loadConfig` requires, with values that reach nothing. */
export const TEST_ENV = {
  DATABASE_URL: "postgres://test:test@127.0.0.1:1/test",
  WEB_URL: "http://localhost:3001",
  BETTER_AUTH_URL: "http://localhost:3000",
  BETTER_AUTH_SECRET: "test-only-not-a-secret-000000000000000",
  // A bridge nobody listens on: a test that forgets to fake one fails fast.
  BRIDGE_URL: "http://127.0.0.1:1/mcp",
} as const;

/** A Config from TEST_ENV plus `env`; never the process environment. */
export function testConfig(
  env: Record<string, string | undefined> = {},
): Config {
  return loadConfig({ ...TEST_ENV, ...env });
}

export type TestUser = { id: string; role: string | null } | null;

/** Better Auth's two entry points; `signIn(null)` signs out. */
export function fakeSessions() {
  let user: TestUser = null;
  let organizationId: string | null = null;
  const getSession = mock(
    async (_headers: Headers): Promise<AuthSession | null> =>
      user
        ? {
            user: { email: `${user.id}@test`, name: user.id, ...user },
            session: { id: "sess_1", activeOrganizationId: organizationId },
          }
        : null,
  );
  const source: SessionSource = {
    handler: async () => new Response("not mocked", { status: 404 }),
    getSession,
  };
  return {
    source,
    getSession,
    signIn(next: TestUser, org: string | null = null) {
      user = next;
      organizationId = org;
    },
  };
}

/**
 * A drizzle stand-in: every query chain resolves (to [] unless queued), every
 * `insert(table).values(row)` is recorded. The real chat store runs over it,
 * so `logToolCall`'s own reading of a result (a 200 body that reports failure
 * is an error row) is exercised too.
 */
export function fakeDb() {
  const inserted: Record<string, unknown>[] = [];
  const state = {
    /** Every insert hangs forever: a stop must not wait on the audit row. */
    insertHangs: false,
    /** What successive select() chains resolve to. */
    selectResults: [] as unknown[][],
    /** Every select() made, with the chain of calls on it. */
    selects: [] as { calls: [string, unknown[]][] }[],
    findFirst: async (_q?: unknown): Promise<unknown> => undefined,
  };

  function thenable(
    result: unknown,
    record?: { calls: [string, unknown[]][] },
    hangs = false,
  ): unknown {
    const self = {
      then: (
        resolve: (v: unknown) => unknown,
        reject: (e: unknown) => unknown,
      ) =>
        hangs
          ? new Promise(() => {})
          : Promise.resolve(result).then(resolve, reject),
    };
    const proxy: unknown = new Proxy(self, {
      get: (obj, prop) =>
        prop === "then"
          ? obj.then
          : (...args: unknown[]) => {
              record?.calls.push([String(prop), args]);
              return proxy;
            },
    });
    return proxy;
  }

  const db = {
    insert: () => ({
      values: (row: Record<string, unknown>) => {
        inserted.push(row);
        return thenable([{ id: row.id }], undefined, state.insertHangs);
      },
    }),
    select: (...args: unknown[]) => {
      const record = { calls: [["select", args]] as [string, unknown[]][] };
      state.selects.push(record);
      return thenable(state.selectResults.shift() ?? [], record);
    },
    update: () => thenable([]),
    delete: () => thenable([]),
    transaction: async (fn: (tx: unknown) => unknown) => fn(db),
    query: { chat: { findFirst: (q: unknown) => state.findFirst(q) } },
  };
  return { db: db as unknown as Db, inserted, state };
}

/**
 * A catalogue entry as the bridge's `tools/list` serves it — the inverse of
 * `toEntry` (skills/catalogue.ts) — so tests drive the real catalogue.
 */
export function toolOf(skill: SkillCatalogueEntry): BridgeTool {
  return {
    name: skill.name,
    description: skill.description,
    inputSchema: skill.parameters,
    _meta: {
      c3po: {
        classification: skill.classification,
        danger_level: skill.dangerLevel,
        ...(skill.tier ? { tier: skill.tier } : {}),
        status: skill.status,
        cancellable: skill.cancellable,
        expected_duration_s: skill.expectedDurationSeconds,
        works: skill.works,
        preconditions: skill.preconditions ?? [],
        typical_failure_modes: skill.typicalFailureModes ?? [],
      },
    },
  };
}

/** A fixture skill, low danger, watched working on both targets. */
export function fixtureSkill(
  name: string,
  overrides: Partial<SkillCatalogueEntry> = {},
): SkillCatalogueEntry {
  return {
    name,
    description: `Fixture ${name}.`,
    parameters: { type: "object", properties: {}, additionalProperties: true },
    preconditions: [],
    expectedDurationSeconds: 1,
    cancellable: false,
    typicalFailureModes: [],
    classification: "gesture",
    dangerLevel: "low",
    status: "real",
    works: { sim: true, real: true },
    ...overrides,
  };
}

/** `model` for every call: the agent's and the vision model's. */
export function fakeModels(model: LanguageModel): AgentModels {
  return {
    chatModel: () => model,
    model: "test-model",
    visionModel: "test-vision",
    missingKey: null,
    activeProvider: () => "primary",
  };
}

type FetchFn = (url: string, init: RequestInit) => Response | Promise<Response>;

/**
 * The real app over fakes: a session you set with `signIn`, a recording
 * database, a bridge whose `callTool` / `listTools` are bun mocks, and one
 * fake `fetch` for the bridge's HTTP routes and the transcription gateway.
 */
export function testApp(
  opts: {
    env?: Record<string, string | undefined>;
    /** The bridge's catalogue. */
    skills?: SkillCatalogueEntry[];
    callTool?: (
      name: string,
      args: Record<string, unknown>,
      signal?: AbortSignal,
    ) => Promise<unknown>;
    /** Every HTTP call to the bridge (and the transcription gateway). */
    fetch?: FetchFn;
    model?: LanguageModel;
    timings?: AppDeps["timings"];
  } = {},
) {
  const sessions = fakeSessions();
  const db = fakeDb();
  const chats = createChatStore(db.db);
  const config = testConfig({ SAFETY_CONFIRM_SECRET: "s3cret", ...opts.env });
  const catalogue = { skills: opts.skills ?? [] };
  const callTool = mock(
    opts.callTool ??
      (async (name: string, args: Record<string, unknown>) => ({
        called: name,
        args,
      })),
  );
  const listTools = mock(async () => catalogue.skills.map(toolOf));
  const fetched: { url: string; init: RequestInit }[] = [];
  const net = {
    handler: (opts.fetch ?? (() => Response.json({}))) as FetchFn,
  };
  const fetch = async (url: string, init: RequestInit) => {
    fetched.push({ url, init });
    return net.handler(url, init);
  };
  const deps: AppDeps = {
    config,
    sessions: sessions.source,
    chats,
    audit: createAuditReader(db.db),
    bridge: {
      // Two arguments unless there is a signal, as the real call sites pass
      // them, so `toHaveBeenCalledWith(name, args)` reads naturally.
      callTool: (name, args = {}, signal) =>
        signal ? callTool(name, args, signal) : callTool(name, args),
      listTools,
    },
    http: createBridgeHttp({ ...config.bridge, fetch }),
    ...(opts.model ? { models: fakeModels(opts.model) } : {}),
    transcribe: createTranscriber(config.transcribe, {
      fetch: fetch as typeof globalThis.fetch,
    }),
    timings: opts.timings,
  };
  const services = createServices(deps);
  const app = createApp(deps, services);

  return {
    app,
    services,
    deps,
    config,
    db,
    /** The audit rows written so far (`tool_call_log` inserts). */
    get rows() {
      return db.inserted.filter((r) => "skillName" in r);
    },
    sessions,
    signIn: sessions.signIn,
    callTool,
    listTools,
    /** Replace the bridge's catalogue (the next refresh serves it). */
    setSkills(skills: SkillCatalogueEntry[]) {
      catalogue.skills = skills;
    },
    fetched,
    /** Replace the fake network from here on. */
    onFetch(handler: FetchFn) {
      net.handler = handler;
    },
    request(path: string, init: RequestInit = {}) {
      return app.handle(new Request(`http://localhost${path}`, init));
    },
    post(path: string, body?: unknown, init: RequestInit = {}) {
      return app.handle(
        new Request(`http://localhost${path}`, {
          method: "POST",
          ...(body !== undefined
            ? {
                headers: { "content-type": "application/json" },
                body: JSON.stringify(body),
              }
            : {}),
          ...init,
        }),
      );
    },
  };
}

export type TestApp = ReturnType<typeof testApp>;
