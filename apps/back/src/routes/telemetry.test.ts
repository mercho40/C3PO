/**
 * The telemetry paths line up across the two hops that serve them.
 *
 * WHY THIS TEST EXISTS. `mounted.test.ts` catches a route module that reaches
 * no composition root. This catches the next link in the same chain: a route
 * that IS mounted, on both sides, under two different paths.
 *
 * The failure looks like nothing. The bridge route exists and answers. The
 * back route exists and is mounted behind auth. The console gets a 502
 * `bridge_error`, which is the same thing it gets when the bridge is down —
 * so the diagnosis is "the robot is off" and the actual cause is a typo
 * between two files nobody edits together. This project has now spent real
 * sessions on four variants of "correct component, no caller"; this is the
 * variant where both callers are correct and disagree.
 *
 * Structural on purpose: it reads both sources, so it needs no bridge, no
 * database and no network — same as the rest of this suite.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const TELEMETRY_TS = join(import.meta.dir, "telemetry.ts");
const MCP_SERVER_PY = join(
  import.meta.dir,
  "..",
  "..",
  "..",
  "bridge",
  "src",
  "bridge",
  "mcp_server.py",
);

/** The upstream paths `telemetry.ts` asks the bridge for. */
function proxiedPaths(): string[] {
  const src = readFileSync(TELEMETRY_TS, "utf8");
  return [...src.matchAll(/proxyJson\(\s*"([^"]+)"/g)].map((m) => m[1]!);
}

/** The paths this app exposes to the browser. */
function exposedPaths(): string[] {
  const src = readFileSync(TELEMETRY_TS, "utf8");
  return [...src.matchAll(/\.get\(\s*"(\/telemetry\/[^"]+)"/g)].map(
    (m) => m[1]!,
  );
}

/** The GET routes the bridge actually serves. */
function bridgeRoutes(): string[] {
  const src = readFileSync(MCP_SERVER_PY, "utf8");
  return [...src.matchAll(/@mcp\.custom_route\(\s*"([^"]+)"/g)].map(
    (m) => m[1]!,
  );
}

describe("telemetry route contract", () => {
  test("the scan is read at all — guards the regexes themselves", () => {
    // A pattern that silently matched nothing would make every assertion
    // below vacuously true, which is the standing failure mode of a
    // structural test.
    expect(proxiedPaths().length).toBeGreaterThan(0);
    expect(bridgeRoutes().length).toBeGreaterThan(0);
    expect(proxiedPaths()).toContain("/telemetry/scan");
    expect(bridgeRoutes()).toContain("/telemetry/scan");
  });

  for (const path of proxiedPaths()) {
    test(`the bridge serves ${path}`, () => {
      // A proxy pointing at a path the bridge does not have returns 502
      // `bridge_error` — indistinguishable from a bridge that is switched off.
      expect(bridgeRoutes()).toContain(path);
    });
  }

  for (const path of exposedPaths()) {
    test(`${path} proxies the same path it exposes`, () => {
      // Not a style rule. When the two drift, the console asks for one thing
      // and the bridge is asked for another, and every symptom points at the
      // robot rather than at these two files.
      expect(proxiedPaths()).toContain(path);
    });
  }

  test("every proxied path is also reachable from the browser", () => {
    // A `proxyJson` with no `.get` is dead code that reads as a working
    // feature — the console-side twin of the never-started singleton.
    for (const path of proxiedPaths()) {
      expect(exposedPaths()).toContain(path);
    }
  });
});
