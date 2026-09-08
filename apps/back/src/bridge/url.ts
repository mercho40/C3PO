/**
 * Where the bridge is, in one place.
 *
 * WHY THIS FILE EXISTS
 *
 * The bridge's address was written out three times — `bridge/client.ts`,
 * `routes/map.ts`, `routes/telemetry.ts` — and the path-derivation helper twice,
 * verbatim, in the two route files.
 *
 * That is worth consolidating because of what `apps/back/.env.example` already
 * says about this exact value, at length:
 *
 *     THE PORT DIFFERS BY TARGET, and the two are one character apart:
 *       8000 — a locally-run bridge
 *       8001 — the real robot over the SSH tunnel
 *     Copying both .env.example files verbatim and then tunnelling to the
 *     robot gives a back that dials 8000 and a bridge listening on 8001 —
 *     which presents as "bridge_unavailable" with both processes visibly
 *     healthy.
 *
 * A one-character mistake with a symptom that looks like a dead robot.
 *
 * THE DEFAULT IS 8001, THE ROBOT'S. Both branches consolidated this file
 * independently and picked different defaults; 8001 is the right one, because
 * it is the port the deployed bridge actually listens on and the one the
 * documented tunnel forwards. A developer running a local bridge on 8000 sets
 * BRIDGE_URL; the robot — the case that matters when something is wrong — works
 * without configuration.
 *
 * NOT IN `lib/env.ts`, DELIBERATELY, FOR TWO REASONS.
 *
 * That module is for config that is REQUIRED and validated at startup — it
 * throws on missing values, and its own docstring notes that optional settings
 * are intentionally kept out. `BRIDGE_URL` is optional with a working default.
 *
 * And `client.ts` reads its URL once at module-load time, which
 * `client.test.ts` depends on: it sets `process.env.BRIDGE_URL` to a dead port
 * and then dynamically imports the module, so the failure it observes is a
 * genuine refused TCP connect rather than a mock. Reading through `env.ts`
 * would freeze the value at whenever THAT module first got imported by
 * anything in the suite, which is not something a test should have to reason
 * about.
 */

/** The bridge's MCP endpoint as deployed — the robot, over the tunnel. */
export const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8001/mcp";

/** Resolve the MCP endpoint without duplicating its deployment default. */
export function bridgeUrl(configured = process.env.BRIDGE_URL): string {
  return configured || DEFAULT_BRIDGE_URL;
}

/**
 * Derive a read-only HTTP endpoint served beside `/mcp` on the bridge.
 *
 * `BRIDGE_URL` points at `…/mcp`; the telemetry, camera and voice routes are
 * siblings of it, so this rewrites the path rather than introducing a second
 * env var that can drift out of step with the first. The query string is
 * dropped because these are proxied endpoints and any caller's parameters
 * belong to the proxy, not to the upstream.
 */
export function bridgeSiblingUrl(
  path: string,
  configured = process.env.BRIDGE_URL,
): string {
  const url = new URL(bridgeUrl(configured));
  url.pathname = path;
  url.search = "";
  return url.toString();
}

/** Hosts that need no tunnel to reach: this machine. */
const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/**
 * The one-line remedy for an unreachable bridge, or `null` when there is none.
 *
 * The bridge stopped binding `0.0.0.0` on 2026-09-06 — it can walk the robot
 * and has no authentication of its own, so it must not sit open on the school
 * Wi-Fi (`scripts/robot/c3po-bridge.service` carries the full note). Anything
 * still dialling the robot's LAN address now gets a refused connection.
 *
 * That failure is indistinguishable from "the robot is off" at the point where
 * it surfaces: a 502 `bridge_unavailable` with both processes visibly healthy —
 * the exact symptom this module already exists to prevent one port typo from
 * causing. The difference is that this one has a fix that fits on one line, so
 * the line is printed rather than left to be rediscovered.
 *
 * Deliberately NOT sent to the client: it names internal hosts, and a browser
 * cannot act on it. It goes to the server log, where the person who can start
 * a tunnel is looking.
 */
export function unreachableHint(url: string = bridgeUrl()): string | null {
  let host: string;
  let port: string;
  try {
    const parsed = new URL(url);
    host = parsed.hostname;
    port = parsed.port || "8001";
  } catch {
    return null;
  }
  if (LOOPBACK.has(host)) return null;
  return (
    `BRIDGE_URL points at ${host}:${port}, but the bridge binds loopback only. ` +
    `Open the tunnel:  ssh -N -L ${port}:127.0.0.1:${port} -o ControlMaster=no c3po  ` +
    `then set BRIDGE_URL=http://127.0.0.1:${port}/mcp in apps/back/.env`
  );
}
