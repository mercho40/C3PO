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

import type { Static, TSchema } from "@sinclair/typebox";
import { Elysia, status } from "elysia";

import type { BridgeHttp } from "../bridge/http";
import {
  BridgeFailureBody,
  BridgeUnavailableBody,
  GateStatusSchema,
  ScanRingSchema,
  SurroundingsSchema,
  VoiceTelemetrySchema,
  badPayload,
  checkUnavailable,
  payloadCheck,
} from "../bridge/payloads";

/**
 * Proxy one JSON telemetry route, checked against its schema.
 *
 * A 503 from the bridge is a real answer, not a failure: "no perception is
 * running", "the voice stack is not installed". It is passed through with its
 * body intact so the console can say WHY there is nothing rather than just
 * that there is nothing — the same distinction D7 draws between an empty scene
 * and one nobody looked at. (A 503 body that is not `{error?, hint?}` becomes
 * `{error: "unavailable"}`: the reason is lost, the answer is not.)
 *
 * A 200 whose body is not JSON, or not the schema, is 502
 * `bridge_bad_payload` with the first field that did not match: the console
 * reads it as "no current reading" — never as an empty scene or a closed gate.
 */
function telemetryRoute<S extends TSchema>(
  http: Pick<BridgeHttp, "relay">,
  path: string,
  schema: S,
) {
  const check = payloadCheck(schema);
  return async ({
    set,
  }: {
    set: { headers: Record<string, string | number> };
  }) => {
    // Short on purpose: this is polled, and a slow answer is a stale answer.
    // The console would rather be told nothing arrived than block a cycle.
    const r = await http.relay(path, {
      timeoutMs: 3000,
      unavailableBody: { error: "unavailable" },
    });
    if (!r.ok) {
      if (r.status === 502)
        return status(502, r.body as Static<typeof BridgeFailureBody>);
      const why = checkUnavailable(r.body);
      return status(503, why.ok ? why.value : { error: "unavailable" });
    }
    let body: unknown;
    try {
      body = JSON.parse(new TextDecoder().decode(r.body));
    } catch {
      return status(502, badPayload(path, { path: "/", message: "not JSON" }));
    }
    const checked = check(body);
    if (!checked.ok) return status(502, badPayload(path, checked));
    // Never cache. A cached scene or transcript is indistinguishable from a
    // current one, and both are read to decide what the robot is doing NOW.
    set.headers["cache-control"] = "no-store";
    return checked.value;
  };
}

const responses = <S extends TSchema>(ok: S) => ({
  200: ok,
  502: BridgeFailureBody,
  503: BridgeUnavailableBody,
});

export const telemetryRoutes = (deps: { http: Pick<BridgeHttp, "relay"> }) =>
  new Elysia()
    .get(
      "/telemetry/surroundings",
      telemetryRoute(deps.http, "/telemetry/surroundings", SurroundingsSchema),
      {
        response: responses(SurroundingsSchema),
        detail: {
          summary:
            "The D7 world-model snapshot — the same one the agent is given, never a parallel view.",
          tags: ["telemetry"],
        },
      },
    )
    .get(
      "/telemetry/scan",
      telemetryRoute(deps.http, "/telemetry/scan", ScanRingSchema),
      {
        response: responses(ScanRingSchema),
        detail: {
          summary:
            "The lidar ring — ~120 bearings of centimetres, with its age and a stale flag.",
          tags: ["telemetry"],
        },
      },
    )
    .get(
      "/telemetry/voice",
      telemetryRoute(deps.http, "/telemetry/voice", VoiceTelemetrySchema),
      {
        response: responses(VoiceTelemetrySchema),
        detail: {
          summary:
            "Recent speech heard by the robot, plus whether it can hear at all. Non-consuming.",
          tags: ["telemetry"],
        },
      },
    )
    .get(
      "/telemetry/gate",
      telemetryRoute(deps.http, "/telemetry/gate", GateStatusSchema),
      {
        response: responses(GateStatusSchema),
        detail: {
          summary:
            "The Nav2 cmd_vel gate: whether autonomous driving is armed, for how long, and what it last sent.",
          tags: ["telemetry"],
        },
      },
    );
