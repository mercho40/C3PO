/**
 * /telemetry/* — what the robot SEES and HEARS, proxied from the bridge.
 *
 * Sibling of `/map/costmap.png` and behind the same reasoning: the bridge binds
 * loopback with no auth of its own and serves `/mcp` — the tool surface that can
 * walk the robot — on the same port. Proxying puts these behind Better Auth,
 * which is the only authentication anywhere in this path. Never hand a browser
 * a route to that port.
 *
 * Plain `fetch`, not the MCP client, for the same reason as the map: these are
 * request/response reads and want none of the transport's long-lived stream
 * machinery. (That transport now works under Bun — see docs/OPERATIONS.md — but
 * a plain GET is still the correct shape for read-only telemetry, not a
 * workaround retained out of habit.)
 *
 * READING THESE MUTATES NOTHING, and that is load-bearing for the voice route:
 * the bridge serves it from `recent()` rather than `poll()`, so a console left
 * open cannot eat the utterances the agent was supposed to act on.
 */

import { Elysia } from "elysia";

import type { BridgeHttp } from "../bridge/http";

/**
 * Proxy one JSON telemetry route.
 *
 * A 503 from the bridge is a real answer, not a failure: "no perception is
 * running", "the voice stack is not installed". It is passed through with its
 * body intact so the console can say WHY there is nothing rather than just
 * that there is nothing — the same distinction D7 draws between an empty scene
 * and one nobody looked at.
 */
async function proxyJson(
  http: Pick<BridgeHttp, "relay">,
  path: string,
  status: (code: number, body: unknown) => unknown,
): Promise<unknown> {
  // Short on purpose: this is polled, and a slow answer is a stale answer.
  // The console would rather be told nothing arrived than block a cycle.
  const r = await http.relay(path, {
    timeoutMs: 3000,
    unavailableBody: { error: "unavailable" },
  });
  if (!r.ok) return status(r.status, r.body);

  return new Response(r.body, {
    headers: {
      "Content-Type": "application/json",
      // Never cache. A cached scene or transcript is indistinguishable from a
      // current one, and both are read to decide what the robot is doing NOW.
      "Cache-Control": "no-store",
    },
  });
}

export const telemetryRoutes = (deps: { http: Pick<BridgeHttp, "relay"> }) =>
  new Elysia()
    .get(
      "/telemetry/surroundings",
      ({ status }) => proxyJson(deps.http, "/telemetry/surroundings", status),
      {
        detail: {
          summary:
            "The D7 world-model snapshot — the same one the agent is given, never a parallel view.",
          tags: ["telemetry"],
        },
      },
    )
    .get(
      "/telemetry/scan",
      ({ status }) => proxyJson(deps.http, "/telemetry/scan", status),
      {
        detail: {
          summary:
            "The lidar ring — ~120 bearings of centimetres, with its age and a stale flag.",
          tags: ["telemetry"],
        },
      },
    )
    .get(
      "/telemetry/voice",
      ({ status }) => proxyJson(deps.http, "/telemetry/voice", status),
      {
        detail: {
          summary:
            "Recent speech heard by the robot, plus whether it can hear at all. Non-consuming.",
          tags: ["telemetry"],
        },
      },
    )
    .get(
      "/telemetry/gate",
      ({ status }) => proxyJson(deps.http, "/telemetry/gate", status),
      {
        detail: {
          summary:
            "The Nav2 cmd_vel gate: whether autonomous driving is armed, for how long, and what it last sent.",
          tags: ["telemetry"],
        },
      },
    );
