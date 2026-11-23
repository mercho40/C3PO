/**
 * /safety — the PARAR latch, as the console sees and clears it.
 *
 * GET  /safety/status  any signed-in user: the bridge's governor status
 *                      (`stop_standing`, caps, nearest person, pending T2
 *                      confirmations). Read-only. Checked against the
 *                      contract (bridge/payloads.ts): a body that is not the
 *                      bridge's SafetyStatusBody is a 502 `bridge_bad_payload`,
 *                      which the console reads as "cannot read the latch" and
 *                      keeps its last reading. Nothing stops through here, and
 *                      stop-watch reads the bridge directly, not this route.
 * POST /safety/ack     admins only: «Reanudar». Clears a standing PARAR on the
 *                      bridge, which otherwise refuses every motion tool. It
 *                      is a route and not a tool so no model can un-stop the
 *                      robot it was stopped from driving, and the secret that
 *                      authorises it stays on this server. ON THE STOP
 *                      PATH, SO DELIBERATELY UNCHECKED: typed for Eden as the
 *                      contract's StopAcknowledged | StopAckRefused, but no
 *                      response schema — a resume the bridge recorded must
 *                      never be answered 502 because it said so in a new way.
 */
import { Elysia } from "elysia";

import type { Confirmations } from "#back/agent/confirmations.js";
import type { BridgeHttp } from "#back/bridge/http.js";
import {
  BridgeFailureBody,
  SafetyStatusSchema,
  badPayload,
  payloadCheck,
  type StopAckBody,
} from "#back/bridge/payloads.js";
import type { LogToolCall } from "#back/db/chats.js";
import type { AuthPlugin } from "#back/lib/auth-plugin.js";

const checkStatus = payloadCheck(SafetyStatusSchema);

export const safetyRoutes = (deps: {
  authPlugin: AuthPlugin;
  http: Pick<BridgeHttp, "safetyStatus" | "safetyAck" | "canProve">;
  confirmations: Pick<Confirmations, "clear">;
  logToolCall: LogToolCall;
}) =>
  new Elysia({ prefix: "/safety" })
    .use(deps.authPlugin)
    .guard({ auth: true }, (app) =>
      app
        .get(
          "/status",
          async ({ status }) => {
            try {
              const upstream = await deps.http.safetyStatus(3000);
              // Not JSON (or too slow to read) is no answer at all.
              if (upstream.body === undefined)
                return status(502, { error: "bridge_unavailable" });
              // The bridge's route always answers 200; anything else is not
              // the governor talking (an older bridge's 404, a proxy).
              if (!upstream.ok)
                return status(502, {
                  error: "bridge_error",
                  message: `HTTP ${upstream.status}`,
                });
              const checked = checkStatus(upstream.body);
              return checked.ok
                ? checked.value
                : status(502, badPayload("/safety/status", checked));
            } catch {
              return status(502, { error: "bridge_unavailable" });
            }
          },
          {
            response: { 200: SafetyStatusSchema, 502: BridgeFailureBody },
            detail: {
              summary:
                "The bridge's safety governor status, PARAR latch included.",
              tags: ["safety"],
            },
          },
        )
        .post(
          "/ack",
          async ({ status, user, body: request }) => {
            if (user.role !== "admin") {
              return status(403, { error: "admin_required" });
            }
            // A card asked before the stop is not confirmable after it, including
            // one noted while the stop stood or after a PARAR back never saw.
            deps.confirmations.clear();
            if (!deps.http.canProve()) {
              return status(503, { error: "confirmation_not_configured" });
            }
            const startedAt = Date.now();
            let upstream: Awaited<ReturnType<BridgeHttp["safetyAck"]>>;
            try {
              // The stamp the operator saw; the bridge refuses (409) a newer stop.
              upstream = await deps.http.safetyAck(
                typeof (request as { stopped_at?: unknown } | null)
                  ?.stopped_at === "number"
                  ? (request as { stopped_at: number }).stopped_at
                  : null,
              );
            } catch {
              return status(502, { error: "bridge_unavailable" });
            }
            // Typed, never checked (see the header): Eden reads the
            // contract's shape, the bridge's answer passes whatever it is.
            const body = (upstream.body ?? {}) as Partial<StopAckBody> & {
              error?: string;
            };
            // Who resumed the robot after a stop is the row worth having.
            void deps.logToolCall({
              userId: user.id,
              source: "direct",
              skillName: "safety_ack",
              params: {},
              result: body,
              status: upstream.ok ? "ok" : "error",
              ...(upstream.ok
                ? {}
                : { error: String(body.error ?? upstream.status) }),
              durationMs: Date.now() - startedAt,
              startedAt,
            });
            return upstream.ok ? body : status(upstream.status, body);
          },
          {
            detail: {
              summary: "Resume after PARAR («Reanudar»). Admin-only.",
              tags: ["safety"],
            },
          },
        ),
    );
