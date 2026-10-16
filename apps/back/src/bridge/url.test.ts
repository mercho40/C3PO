/**
 * One copy of the bridge's address, and the path derivation that goes with it.
 *
 * `apps/back/.env.example` spends a paragraph on why this value is dangerous:
 * a locally-run bridge is on 8000, the real robot over the SSH tunnel is on
 * 8001, and getting it wrong "presents as `bridge_unavailable` with both
 * processes visibly healthy". A one-character mistake that looks like a dead
 * robot.
 *
 * The fallback for it used to be written out in three files and the derivation
 * helper in two, verbatim. Nothing made them agree.
 */

import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_BRIDGE_URL,
  bridgeSiblingUrl,
  bridgeUrl,
  unreachableHint,
} from "./url";

// `fileURLToPath`, not `.pathname`: this repository lives under a directory
// with spaces in its name, which `.pathname` returns percent-encoded.
const SRC = fileURLToPath(new URL("../", import.meta.url));
const LITERAL = DEFAULT_BRIDGE_URL;

/**
 * Source with comments removed.
 *
 * The line-comment pattern refuses to match a `//` preceded by a colon —
 * otherwise it eats the rest of every line containing `http://`, which in a
 * file whose whole subject is a URL means stripping the thing being looked
 * for and concluding it is absent.
 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sourceFiles(full, found);
    else if (/\.ts$/.test(entry)) found.push(full);
  }
  return found;
}

describe("bridge URL configuration", () => {
  test("the default literal appears in exactly one source file", () => {
    // The point of the whole module. If this fails, somebody has written the
    // address out again somewhere and the two can now drift — which is the
    // failure that cost a headset session when the camera port moved from 8081
    // to 8001 and only one of the two places that knew about it was updated.
    //
    // Comments stripped: prose that MENTIONS the address is not a second copy
    // that can drift, and a docstring explaining the rule should not fail the
    // rule. Test files are excluded for the same reason.
    const holders = sourceFiles(SRC)
      .filter((f) => !/\.test\.ts$/.test(f))
      .filter((f) => stripComments(readFileSync(f, "utf8")).includes(LITERAL));
    expect(holders.map((f) => f.slice(SRC.length))).toEqual(["bridge/url.ts"]);
  });

  test("defaults to the robot's port, not a local bridge's", () => {
    // 8001 is what the deployed bridge listens on and what the documented
    // tunnel forwards. A developer running locally on 8000 sets BRIDGE_URL;
    // the robot works unconfigured, which is the case that matters when
    // something is already going wrong.
    expect(DEFAULT_BRIDGE_URL).toBe("http://127.0.0.1:8001/mcp");
    expect(bridgeUrl("")).toBe(DEFAULT_BRIDGE_URL);
  });

  test("honours an explicit endpoint", () => {
    expect(bridgeUrl("http://bridge.test:9000/mcp")).toBe(
      "http://bridge.test:9000/mcp",
    );
  });

  test("falls back to the default when the environment is unset", () => {
    const saved = process.env.BRIDGE_URL;
    delete process.env.BRIDGE_URL;
    try {
      expect(bridgeUrl()).toBe(DEFAULT_BRIDGE_URL);
    } finally {
      if (saved !== undefined) process.env.BRIDGE_URL = saved;
    }
  });

  test("derives sibling routes without carrying MCP query state", () => {
    expect(
      bridgeSiblingUrl(
        "/telemetry/voice",
        "http://bridge.test:9000/mcp?session=stale",
      ),
    ).toBe("http://bridge.test:9000/telemetry/voice");
  });

  test("the robot's port survives the round trip unchanged", () => {
    // The specific mistake `.env.example` warns about is 8000 vs 8001. If the
    // derivation ever normalised or defaulted the port away, a console aimed
    // at the robot would silently talk to a local bridge instead.
    expect(
      new URL(bridgeSiblingUrl("/telemetry/scan", "http://127.0.0.1:8001/mcp"))
        .port,
    ).toBe("8001");
  });
});

describe("the tunnel hint", () => {
  // The bridge stopped binding 0.0.0.0 on 2026-09-06, because /mcp can walk
  // the robot and has no auth of its own. Anything still dialling the robot's
  // LAN address now gets a refused connection that looks exactly like a dead
  // robot — the same symptom this module already exists to prevent.

  test("a LAN address gets the tunnel command, with its own port", () => {
    const hint = unreachableHint("http://10.10.32.19:8001/mcp");
    expect(hint).toContain("ssh -N -L 8001:127.0.0.1:8001");
    expect(hint).toContain("10.10.32.19");
    // ControlMaster=no is not decoration: OPERATIONS records that a forward on
    // a shared master evaporates when the master idles out, which presents as
    // a tunnel that worked and then stopped.
    expect(hint).toContain("ControlMaster=no");
  });

  test("loopback gets no hint, because nothing is wrong with it", () => {
    expect(unreachableHint("http://127.0.0.1:8001/mcp")).toBeNull();
    expect(unreachableHint("http://localhost:8001/mcp")).toBeNull();
    expect(unreachableHint(DEFAULT_BRIDGE_URL)).toBeNull();
  });

  test("a malformed URL returns null rather than throwing", () => {
    // This runs inside a `.catch` on the connect path. Throwing here would
    // replace a diagnosable BridgeUnavailableError with a TypeError about URL
    // parsing, which is a strictly worse thing to find in a log.
    expect(unreachableHint("not a url")).toBeNull();
    expect(unreachableHint("")).toBeNull();
  });

  test("the port is carried through, not hardcoded to 8001", () => {
    // 8000 vs 8001 is the exact confusion this file opens by describing.
    const hint = unreachableHint("http://10.10.32.19:8000/mcp");
    expect(hint).toContain("ssh -N -L 8000:127.0.0.1:8000");
    expect(hint).toContain("http://127.0.0.1:8000/mcp");
  });
});
