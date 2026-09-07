/**
 * One copy of the bridge's address, and the path derivation that goes with it.
 *
 * `apps/back/.env.example` spends a paragraph on why this value is dangerous:
 * a locally-run bridge is on 8000, the real robot over the SSH tunnel is on
 * 8001, and getting it wrong "presents as `bridge_unavailable` with both
 * processes visibly healthy". A one-character mistake that looks like a dead
 * robot.
 *
 * The fallback for it used to be written out in three files and the
 * `telemetryUrl` helper in two, verbatim. Nothing made them agree.
 */

import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_BRIDGE_URL,
  bridgeUrl,
  telemetryUrl,
  unreachableHint,
} from "./url";

const SRC = fileURLToPath(new URL("..", import.meta.url));

/** What must appear once. Test files are excluded from the scan below. */
const LITERAL = "127.0.0.1:8000/mcp";

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

describe("bridge URL", () => {
  test("the default literal appears in exactly one source file", () => {
    // The point of the whole module. If this fails, somebody has written the
    // address out again somewhere and the two can now drift — which is the
    // failure that took a day out of the headset session when the camera port
    // moved from 8081 to 8001 and one of the two places that knew did not.
    //
    // Comments stripped: prose that MENTIONS the address is not a second copy
    // that can drift, and a docstring explaining the rule should not fail the
    // rule. Test files are excluded for the same reason.
    const holders = sourceFiles(SRC)
      .filter((f) => !/\.test\.ts$/.test(f))
      .filter((f) => stripComments(readFileSync(f, "utf8")).includes(LITERAL));
    expect(holders.map((f) => f.slice(SRC.length))).toEqual(["bridge/url.ts"]);
  });

  test("falls back to the local bridge when nothing is set", () => {
    const saved = process.env.BRIDGE_URL;
    delete process.env.BRIDGE_URL;
    try {
      expect(bridgeUrl()).toBe(DEFAULT_BRIDGE_URL);
    } finally {
      if (saved !== undefined) process.env.BRIDGE_URL = saved;
    }
  });

  test("the environment wins when it is set", () => {
    const saved = process.env.BRIDGE_URL;
    process.env.BRIDGE_URL = "http://127.0.0.1:8001/mcp";
    try {
      expect(bridgeUrl()).toBe("http://127.0.0.1:8001/mcp");
    } finally {
      if (saved === undefined) delete process.env.BRIDGE_URL;
      else process.env.BRIDGE_URL = saved;
    }
  });

  test("telemetry paths are siblings of /mcp, on the same origin", () => {
    // Derived rather than configured separately: a second env var for the
    // telemetry origin is a second thing to get wrong, pointing at the same
    // process.
    const saved = process.env.BRIDGE_URL;
    process.env.BRIDGE_URL = "http://127.0.0.1:8001/mcp";
    try {
      expect(telemetryUrl("/telemetry/scan")).toBe(
        "http://127.0.0.1:8001/telemetry/scan",
      );
    } finally {
      if (saved === undefined) delete process.env.BRIDGE_URL;
      else process.env.BRIDGE_URL = saved;
    }
  });

  test("a query string on the bridge URL does not leak into telemetry", () => {
    // These endpoints are proxied. A caller's parameters belong to the proxy,
    // and forwarding a stray `?token=` upstream would be a surprise.
    const saved = process.env.BRIDGE_URL;
    process.env.BRIDGE_URL = "http://127.0.0.1:8000/mcp?session=abc";
    try {
      expect(telemetryUrl("/telemetry/gate")).toBe(
        "http://127.0.0.1:8000/telemetry/gate",
      );
    } finally {
      if (saved === undefined) delete process.env.BRIDGE_URL;
      else process.env.BRIDGE_URL = saved;
    }
  });

  test("the robot's port survives the round trip unchanged", () => {
    // The specific mistake `.env.example` warns about is 8000 vs 8001. If the
    // derivation ever normalised or defaulted the port away, a console aimed
    // at the robot would silently talk to a local bridge instead.
    const saved = process.env.BRIDGE_URL;
    process.env.BRIDGE_URL = "http://127.0.0.1:8001/mcp";
    try {
      expect(new URL(telemetryUrl("/telemetry/scan")).port).toBe("8001");
    } finally {
      if (saved === undefined) delete process.env.BRIDGE_URL;
      else process.env.BRIDGE_URL = saved;
    }
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
