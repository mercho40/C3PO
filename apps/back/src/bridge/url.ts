/**
 * Where the bridge is, in one place.
 *
 * WHY THIS FILE EXISTS
 *
 * The default `http://127.0.0.1:8000/mcp` was written out three times —
 * `bridge/client.ts`, `routes/map.ts`, `routes/telemetry.ts` — and the
 * `telemetryUrl` helper twice, verbatim, in the two route files.
 *
 * That is worth consolidating here rather than anywhere else because of what
 * `apps/back/.env.example` already says about this exact value, at length:
 *
 *     THE PORT DIFFERS BY TARGET, and the two are one character apart:
 *       8000 — a locally-run bridge
 *       8001 — the real robot over the SSH tunnel
 *     Copying both .env.example files verbatim and then tunnelling to the
 *     robot gives a back that dials 8000 and a bridge listening on 8001 —
 *     which presents as "bridge_unavailable" with both processes visibly
 *     healthy.
 *
 * A one-character mistake with a symptom that looks like a dead robot, and the
 * fallback for it was written down three times. One of those copies drifting
 * is the same shape of failure as the camera port that moved from 8081 to 8001
 * while `quest_setup.sh` kept forwarding the old one.
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

/** The local-bridge default. Not the robot's — see the note above. */
export const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8000/mcp";

/** The bridge's MCP endpoint, from the environment or the default. */
export function bridgeUrl(): string {
  return process.env.BRIDGE_URL ?? DEFAULT_BRIDGE_URL;
}

/**
 * A bridge telemetry route, derived from the MCP endpoint.
 *
 * `BRIDGE_URL` points at `…/mcp`; the telemetry routes are siblings of it, so
 * this rewrites the path rather than introducing a second env var that can
 * drift out of step with the first. The query string is dropped because these
 * are proxied endpoints and any caller's parameters belong to the proxy, not
 * to the upstream.
 */
export function telemetryUrl(path: string): string {
  const base = new URL(bridgeUrl());
  base.pathname = path;
  base.search = "";
  return base.toString();
}
