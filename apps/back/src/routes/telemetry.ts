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

import { bridgeSiblingUrl } from "../bridge/url";

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
  path: string,
  status: (code: number, body: unknown) => unknown,
): Promise<unknown> {
  let upstream: Response;
  try {
    upstream = await fetch(bridgeSiblingUrl(path), {
      // Short on purpose: this is polled, and a slow answer is a stale answer.
      // The console would rather be told nothing arrived than block a cycle.
      signal: AbortSignal.timeout(3000),
    });
  } catch {
    return status(502, { error: "bridge_unavailable" });
  }

  if (upstream.status === 503) {
    return status(
      503,
      await upstream.json().catch(() => ({ error: "unavailable" })),
    );
  }
  if (!upstream.ok) return status(502, { error: "bridge_error" });

  return new Response(await upstream.arrayBuffer(), {
    headers: {
      "Content-Type": "application/json",
      // Never cache. A cached scene or transcript is indistinguishable from a
      // current one, and both are read to decide what the robot is doing NOW.
      "Cache-Control": "no-store",
    },
  });
}

export const telemetryRoutes = new Elysia()
  .get(
    "/telemetry/surroundings",
    ({ status }) => proxyJson("/telemetry/surroundings", status),
    {
      detail: {
        summary:
          "The D7 world-model snapshot — the same one the agent is given, never a parallel view.",
        tags: ["telemetry"],
      },
    },
  )
  .get(
    "/telemetry/voice",
    ({ status }) => proxyJson("/telemetry/voice", status),
    {
      detail: {
        summary:
          "Recent speech heard by the robot, plus whether it can hear at all. Non-consuming.",
        tags: ["telemetry"],
      },
    },
  );
