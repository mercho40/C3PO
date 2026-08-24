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
