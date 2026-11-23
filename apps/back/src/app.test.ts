/**
 * The app boots, and every route module is actually mounted in it.
 *
 * WHY THIS TEST EXISTS. Four separate times in this project a finished,
 * tested component was connected to nothing, and every one of them read from
 * the outside as "not built yet" rather than as a bug (`PerceptionLink` never
 * `start()`ed, a stop that never closed the gate, a detector flag hardcoded
 * off, and `VoiceLoop`, referenced by nothing but its own test). A route file
 * whose factory never reaches `createApp` serves 404 while looking entirely
 * finished in the editor; no test of that module can catch it, because the
 * module passes.
 *
 * This used to read index.ts as text (routes/mounted.test.ts), because
 * nothing could build the app without a database, a bridge and a port. Now it
 * boots the real app over fakes (`testApp`) and talks to it in process through
 * Eden — the same typed client apps/web uses — and compares the routes the app
 * serves with the routes every module declares.
 */
import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { treaty } from "@elysiajs/eden";

import { createAuthPlugin } from "./lib/auth-plugin";
import { fakeSessions, testApp, testConfig } from "./testing/fakes";

const ROUTES_DIR = join(import.meta.dir, "routes");

/** Every `xxxRoutes` factory each routes/*.ts module exports. */
async function routeFactories(): Promise<[string, string, Function][]> {
  const files = readdirSync(ROUTES_DIR)
    .filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"))
    .sort();
  const out: [string, string, Function][] = [];
  for (const file of files) {
    const mod = (await import(join(ROUTES_DIR, file))) as Record<
      string,
      unknown
    >;
    for (const [name, value] of Object.entries(mod))
      if (name.endsWith("Routes") && typeof value === "function")
        out.push([file, name, value as Function]);
  }
  return out;
}

/**
 * Deps for building a factory just to read its route table: the auth plugin
 * is real (its macro must exist), everything else is a stand-in nobody calls.
 */
function inertDeps(): Record<string, unknown> {
  const authPlugin = createAuthPlugin(fakeSessions().source);
  return new Proxy({} as Record<string, unknown>, {
    get: (_t, key) => (key === "authPlugin" ? authPlugin : {}),
  });
}

const served = (routes: { method: string; path: string }[]) =>
  new Set(routes.map((r) => `${r.method} ${r.path}`));

const factories = await routeFactories();

describe("the app serves every route every module declares", () => {
  const app = testApp().app;
  const mounted = served(app.routes);

  test("there are route modules to check, and the app serves routes", () => {
    // A glob that silently matches nothing would make every assertion below
    // vacuously pass, which is the failure mode of structural tests.
    expect(factories.length).toBeGreaterThanOrEqual(13);
    expect(mounted.size).toBeGreaterThan(20);
  });

  for (const [file, name, factory] of factories) {
    test(`${name} (${file}) is mounted, every route of it`, () => {
      const own = served(
        (factory(inertDeps()) as { routes: typeof app.routes }).routes,
      );
      expect(own.size).toBeGreaterThan(0);
      const missing = [...own].filter((r) => !mounted.has(r));
      expect(missing).toEqual([]);
    });
  }
});

describe("through Eden, in process", () => {
  test("/health answers without a session, and names the configured providers", async () => {
    const h = testApp({
      env: { GITHUB_CLIENT_ID: "id", GITHUB_CLIENT_SECRET: "secret" },
    });
    const api = treaty(h.app);
    const { data, status } = await api.health.get();
    expect(status).toBe(200);
    // The app's onError can answer a Response, so Eden types that in too.
    const body = data as Exclude<typeof data, Response>;
    expect(body?.status).toBe("ok");
    expect(body?.auth.providers).toEqual(["github"]);
    expect(body?.agent.provider).toBe("primary");
  });

  test("everything else wants a session", async () => {
    const api = treaty(testApp().app);
    expect((await api.state.get()).status).toBe(401);
    expect((await api.skills.get()).status).toBe(401);
    expect((await api.chats.get({ query: {} })).status).toBe(401);
  });

  test("a signed-in operator reads the robot's state from the bridge", async () => {
    const h = testApp({
      callTool: async (name) =>
        name === "get_state" ? { posture: "stand", env: "stub" } : {},
    });
    h.signIn({ id: "u1", role: null });
    const { data, status } = await treaty(h.app).state.get();
    expect(status).toBe(200);
    expect(data).toMatchObject({ posture: "stand", env: "stub" });
  });

  test("PARAR, through the client the console uses, reaches the bridge once", async () => {
    const h = testApp();
    h.signIn({ id: "u1", role: null });
    const { status } = await treaty(h.app)
      .skills({ name: "stop_everything" })
      .invoke.post({});
    expect(status).toBe(200);
    expect(h.callTool).toHaveBeenCalledTimes(1);
    expect(h.callTool).toHaveBeenCalledWith("stop_everything", {});
  });
});

/**
 * Modules that run their own `auth` guard must sit BESIDE the app's outer
 * guard, not inside it. Nested, Elysia runs the auth macro's resolver once per
 * guard — measured on 1.4.28: two `getSession()` calls per request — and the
 * module this matters most for is /skills, whose /invoke carries PARAR and the
 * ~1.1/s VR walk loop. Counted, not read off the source.
 */
describe("one session lookup per request", () => {
  for (const [label, path, method] of [
    ["PARAR (/skills invoke)", "/skills/stop_everything/invoke", "POST"],
    ["the command log (/audit)", "/audit", "GET"],
    ["/safety/status", "/safety/status", "GET"],
    ["/face/seen", "/face/seen", "GET"],
    ["a route inside the outer guard (/state)", "/state", "GET"],
  ] as const) {
    test(label, async () => {
      const h = testApp();
      h.signIn({ id: "u1", role: "admin" });
      await h.request(path, {
        method,
        ...(method === "POST"
          ? { headers: { "content-type": "application/json" }, body: "{}" }
          : {}),
      });
      expect(h.sessions.getSession).toHaveBeenCalledTimes(1);
    });
  }
});

/**
 * apps/back listens on loopback unless a LAN box opts out (BACK_HOST=0.0.0.0).
 * Elysia's bare `.listen(port)` binds every interface, which put the robot's
 * API (cookie-guarded, but still) on the school Wi-Fi. The default must be the
 * IPv4 literal: "localhost" binds ::1 on macOS, and adb reverse (the Quest)
 * dials IPv4, which blanked the headset once before.
 */
describe("apps/back binds loopback by default", () => {
  test("BACK_HOST defaults to 127.0.0.1, and is honoured when set", () => {
    expect(testConfig().host).toBe("127.0.0.1");
    expect(testConfig({ BACK_HOST: "0.0.0.0" }).host).toBe("0.0.0.0");
  });

  test("index.ts passes the configured host to listen", () => {
    // index.ts listens on a real port with a real database, so it is the one
    // file this reads rather than runs.
    const index = readFileSync(join(import.meta.dir, "index.ts"), "utf8");
    expect(index).toMatch(
      /\.listen\(\{\s*port:\s*config\.port,\s*hostname:\s*config\.host,?\s*\}\)/,
    );
  });
});
