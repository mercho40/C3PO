/**
 * Every route module is actually mounted.
 *
 * WHY THIS TEST EXISTS. Four separate times in this project a finished,
 * tested component was connected to nothing, and every one of them read from
 * the outside as "not built yet" rather than as a bug:
 *
 *   - `PerceptionLink` was fully written and never `start()`ed, so the bridge
 *     had no participant on DDS domain 42 at all. The symptoms were a 503 from
 *     the costmap route, `Subscription count: 0` on the cmd_vel gate, and
 *     `describe_surroundings` reporting perception offline — three different
 *     wrong diagnoses, none pointing at the missing call.
 *   - `stop_everything` cancelled tasks but never closed the gate, so "stop"
 *     meant "pause".
 *   - `describe_surroundings` hardcoded `detector_online=False` while the
 *     function that would have filled it in was never called.
 *   - `VoiceLoop` was written, tested, and referenced by nothing but its own
 *     test file. The voice loop had "never been run end to end" because there
 *     was no way to start it.
 *
 * The common shape is a module that is complete and correct in isolation, with
 * no caller — which no unit test of that module can catch, because the module
 * passes. Only something that looks at the wiring can.
 *
 * This covers the `apps/back` instance of it: a route file whose export never
 * reaches the composition root serves 404 while looking entirely finished in
 * the editor. It is deliberately structural (it reads the source rather than
 * booting the app) so it needs no database, no bridge, and no network — the
 * same reasons the rest of this suite runs offline.
 */
import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const ROUTES_DIR = import.meta.dir;
const INDEX = join(ROUTES_DIR, "..", "index.ts");

function routeModules(): string[] {
  return readdirSync(ROUTES_DIR)
    .filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"))
    .sort();
}

/** The `xxxRoutes` symbols a module exports. */
function exportedRouteSymbols(file: string): string[] {
  const src = readFileSync(join(ROUTES_DIR, file), "utf8");
  return [...src.matchAll(/export const (\w+Routes)\b/g)].map((m) => m[1]!);
}

describe("route wiring", () => {
  const index = readFileSync(INDEX, "utf8");

  test("there is at least one route module to check", () => {
    // Guards the test itself: a glob that silently matches nothing would make
    // every assertion below vacuously pass, which is the failure mode of
    // structural tests.
    expect(routeModules().length).toBeGreaterThan(0);
  });

  for (const file of routeModules()) {
    const symbols = exportedRouteSymbols(file);

    test(`${file} exports a route module`, () => {
      // A file in routes/ that exports no `*Routes` is either dead code or a
      // helper in the wrong directory. Both are worth noticing.
      expect(symbols.length).toBeGreaterThan(0);
    });

    for (const symbol of symbols) {
      test(`${symbol} is imported and mounted in index.ts`, () => {
        expect(index).toContain(symbol);
        // Importing without mounting is the exact half-wiring that makes a
        // route look present in the composition root and still answer 404,
        // so assert the `.use()` and not merely the import.
        expect(index).toContain(`.use(${symbol})`);
      });
    }
  }
});

/**
 * Modules that run their own `auth` guard must sit BESIDE index.ts's outer
 * guard, not inside it. Nested, Elysia runs the auth macro's resolver once per
 * guard — measured on 1.4.28: two `getSession()` calls per request — and the
 * module this matters most for is /skills, whose /invoke carries PARAR and the
 * ~1.1/s VR walk loop. index.ts drifted back to nesting it once already, while
 * skills.ts's docstring still said otherwise.
 */
describe("self-guarded modules are mounted outside the outer guard", () => {
  const index = readFileSync(INDEX, "utf8");
  // The call, not a mention of it in a comment.
  const outerGuard = index.search(/\.guard\(\{ auth: true \},\s*\(app\)/);

  test("index.ts has an outer guard to compare against", () => {
    expect(outerGuard).toBeGreaterThan(0);
  });

  for (const symbol of ["skillsRoutes", "auditRoutes", "safetyRoutes"]) {
    test(`${symbol} is mounted before (outside) the outer guard`, () => {
      const at = index.indexOf(`.use(${symbol})`);
      expect(at).toBeGreaterThan(0);
      expect(at).toBeLessThan(outerGuard);
    });
  }
});

/**
 * apps/back listens on loopback unless a LAN box opts out (BACK_HOST=0.0.0.0).
 * Elysia's bare `.listen(port)` binds every interface, which put the robot's
 * API (cookie-guarded, but still) on the school Wi-Fi. The default
 * must be the IPv4 literal: "localhost" binds ::1 on macOS, and adb reverse
 * (the Quest) dials IPv4, which blanked the headset once before.
 */
describe("apps/back binds loopback by default", () => {
  const index = readFileSync(INDEX, "utf8");
  const envTs = readFileSync(join(ROUTES_DIR, "..", "lib", "env.ts"), "utf8");

  test("index.ts passes the configured hostname to listen", () => {
    expect(index).toMatch(
      /\.listen\(\{\s*port:\s*env\.PORT,\s*hostname:\s*env\.BACK_HOST\s*\}\)/,
    );
  });

  test("env.ts defaults BACK_HOST to 127.0.0.1", () => {
    expect(envTs).toMatch(
      /BACK_HOST:\s*process\.env\.BACK_HOST\s*\|\|\s*"127\.0\.0\.1"/,
    );
  });
});
