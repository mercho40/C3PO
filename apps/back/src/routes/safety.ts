/**
 * /safety — the PARAR latch, as the console sees and clears it.
 *
 * GET  /safety/status  any signed-in user: the bridge's governor status
 *                      (`stop_standing`, caps, nearest person, pending T2
 *                      confirmations). Read-only.
 * POST /safety/ack     admins only: «Reanudar». Clears a standing PARAR on the
 *                      bridge, which otherwise refuses every motion tool. It
 *                      is a route and not a tool so no model can un-stop the
 *                      robot it was stopped from driving, and the secret that
 *                      authorises it stays on this server.
 */
import { Elysia } from "elysia";

import type { Confirmations } from "#back/agent/confirmations.js";
import type { BridgeHttp } from "#back/bridge/http.js";
import type { LogToolCall } from "#back/db/chats.js";
import type { AuthPlugin } from "#back/lib/auth-plugin.js";

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
              return upstream.ok
                ? upstream.body
                : status(upstream.status, upstream.body);
            } catch {
              return status(502, { error: "bridge_unavailable" });
            }
          },
          {
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
            const body = upstream.body ?? {};
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
