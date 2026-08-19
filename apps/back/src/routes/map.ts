/**
 * /map/costmap.png — Nav2's global costmap, proxied from the bridge.
 *
 * WHY THIS IS A PROXY AND NOT A DIRECT BROWSER FETCH.
 *
 * The live camera goes straight from the browser to the vision container's own
 * port (`PUBLIC_ROBOT_CAM_URL`), and copying that here would be the obvious
 * move. It is the wrong one, for a reason that is about security rather than
 * taste: that camera port serves nothing but frames, whereas the costmap lives
 * on the BRIDGE's port — which also serves `/mcp`, the tool surface that can
 * walk the robot. Handing a browser a route to that port is a different risk
 * class entirely, and the bridge deliberately binds loopback with no auth of
 * its own. Proxying puts the map behind Better Auth, which is the only
 * authentication anywhere in this path.
 *
 * WHY PLAIN `fetch` AND NOT THE MCP CLIENT.
 *
 * `callTool` cannot be used here and that is not a limitation — it is the
 * point. The MCP SDK's StreamableHTTPClientTransport fails under Bun with
 * "socket connection was closed unexpectedly", while identical code under Node
 * works (docs/OPERATIONS.md). The break is specific to the transport holding a
 * LONG-LIVED STREAM; a plain request/response `fetch` in Bun to the same server
 * is fine. So this route works today, under Bun, while every tool-backed route
 * in this app does not — the costmap is the first robot data that can reach the
 * console while that blocker stands.
 *
 * A 240x240 costmap is ~540 bytes of PNG. Polling it at 1 Hz is cheaper than
 * the `/state` JSON already being polled beside it.
 */

import { Elysia } from "elysia";

/**
 * The bridge's telemetry origin. `BRIDGE_URL` points at the MCP endpoint
 * (`…/mcp`); the telemetry routes are siblings of it, so derive rather than
 * introducing a second env var that can drift out of step with the first.
 */
function telemetryUrl(path: string): string {
  const bridgeUrl = process.env.BRIDGE_URL ?? "http://127.0.0.1:8000/mcp";
  const base = new URL(bridgeUrl);
  base.pathname = path;
  base.search = "";
  return base.toString();
}

/** Placement metadata the bridge attaches, so the console can georeference the image. */
const FORWARDED_HEADERS = [
  "X-C3PO-Frame",
  "X-C3PO-Width",
  "X-C3PO-Height",
  "X-C3PO-Resolution-M",
  "X-C3PO-Origin-X-M",
  "X-C3PO-Origin-Y-M",
  "X-C3PO-Age-S",
  "X-C3PO-Stale",
] as const;

export const mapRoutes = new Elysia().get(
  "/map/costmap.png",
  async ({ status }) => {
    let upstream: Response;
    try {
      upstream = await fetch(telemetryUrl("/telemetry/costmap.png"), {
        // Short: at 1 Hz a slow map is a stale map, and the console would
        // rather be told "no map" than block a poll cycle waiting for one.
        signal: AbortSignal.timeout(3000),
      });
    } catch {
      return status(502, { error: "bridge_unavailable" });
    }

    // 503 from the bridge means "no costmap has arrived", which is a real and
    // useful answer — it happens whenever no nav2 stage is up. Pass it through
    // with its body intact rather than flattening it into a generic error, so
    // the console can say WHY there is no map instead of just that there isn't.
    if (upstream.status === 503) {
      return status(503, await upstream.json().catch(() => ({ error: "no_costmap" })));
    }
    if (!upstream.ok) return status(502, { error: "bridge_error" });

    const headers = new Headers({
      "Content-Type": "image/png",
      // Never cache. At 1 Hz a cached map is a lie within a second, and a map
      // that is quietly a minute old is exactly the failure the age header
      // exists to prevent.
      "Cache-Control": "no-store",
    });
    for (const name of FORWARDED_HEADERS) {
      const value = upstream.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    // Let the browser read the placement metadata; without this it can fetch
    // the image but not position it, and the map silently lands at the origin.
    headers.set("Access-Control-Expose-Headers", FORWARDED_HEADERS.join(", "));

    return new Response(await upstream.arrayBuffer(), { headers });
  },
  {
    detail: {
      summary: "Nav2's global costmap as a PNG, with placement metadata on X-C3PO-* headers.",
      tags: ["map"],
    },
  },
);
