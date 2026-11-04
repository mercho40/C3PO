/**
 * /teleop — the token that opens the bridge's teleop socket (port 8767).
 *
 * GET /teleop/token  admins only (driving the robot directly is admin-only,
 *                    as in skills.ts): a token valid for TELEOP_TOKEN_TTL_S,
 *                    checked by `authorize` in apps/bridge/src/bridge/teleop/
 *                    server.py on the WebSocket handshake. `{ token: null }`
 *                    when TELEOP_SECRET is unset: the bridge then accepts any
 *                    allowed Origin and warns loudly, so test day still works.
 */
import { createHmac } from "node:crypto";
import { Elysia } from "elysia";
import { betterAuth } from "#back/lib/auth-plugin.js";

//: Only has to outlive the handshake; each connect asks for a fresh one.
//: shortcut: robot and laptop clocks must agree within this, widen it if
//: `teleop.token_expired` shows up in the teleop log.
export const TELEOP_TOKEN_TTL_S = 60;

/** `<exp>.<hex HMAC-SHA256(secret, "teleop:<exp>")>`; mirrors `token_valid`. */
export function mintTeleopToken(secret: string, nowMs = Date.now()): string {
  const exp = Math.floor(nowMs / 1000) + TELEOP_TOKEN_TTL_S;
  const sig = createHmac("sha256", secret)
    .update(`teleop:${exp}`)
    .digest("hex");
  return `${exp}.${sig}`;
}

export const teleopRoutes = new Elysia({ prefix: "/teleop" })
  .use(betterAuth)
  .guard({ auth: true }, (app) =>
    app.get(
      "/token",
      ({ status, user }) => {
        if (user.role !== "admin") {
          return status(403, { error: "admin_required" });
        }
        const secret = process.env.TELEOP_SECRET;
        return { token: secret ? mintTeleopToken(secret) : null };
      },
      {
        detail: {
          summary: "A short-lived token for the teleop socket. Admin-only.",
          tags: ["teleop"],
        },
      },
    ),
  );
