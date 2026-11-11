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
import { bridgeSiblingUrl } from "../bridge/url";
import { bridgeProof } from "../bridge/proof";
import { betterAuth } from "#back/lib/auth-plugin.js";
import { logToolCall } from "#back/db/chats.js";
import { clearPending } from "#back/agent/confirmations.js";

export const safetyRoutes = new Elysia({ prefix: "/safety" })
  .use(betterAuth)
  .guard({ auth: true }, (app) =>
    app
      .get(
        "/status",
        async ({ status }) => {
          try {
            const upstream = await fetch(bridgeSiblingUrl("/safety/status"), {
              signal: AbortSignal.timeout(3000),
            });
            const body = await upstream.json();
            return upstream.ok ? body : status(upstream.status, body);
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
          clearPending();
          const proof = bridgeProof();
          if (!proof) {
            return status(503, { error: "confirmation_not_configured" });
          }
          const startedAt = Date.now();
          let upstream: Response;
          try {
            upstream = await fetch(bridgeSiblingUrl("/safety/ack"), {
              method: "POST",
              headers: { "content-type": "application/json", ...proof },
              // The stamp the operator saw; the bridge refuses (409) a newer stop.
              body: JSON.stringify({
                stopped_at:
                  typeof (request as { stopped_at?: unknown } | null)
                    ?.stopped_at === "number"
                    ? (request as { stopped_at: number }).stopped_at
                    : null,
              }),
              signal: AbortSignal.timeout(5000),
            });
          } catch {
            return status(502, { error: "bridge_unavailable" });
          }
          const body = (await upstream.json().catch(() => ({}))) as Record<
            string,
            unknown
          >;
          // Who resumed the robot after a stop is the row worth having.
          void logToolCall({
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
