/**
 * /teleop — the token that opens the bridge's teleop socket (port 8767).
 *
 * GET /teleop/token  admins only (driving the robot directly is admin-only,
 *                    as in skills.ts): a token valid for TELEOP_TOKEN_TTL_S,
 *                    checked by `authorize` in apps/bridge/src/bridge/teleop/
 *                    server.py on the WebSocket handshake. `{ token: null }`
 *                    when TELEOP_SECRET is unset: the bridge then accepts any
 *                    allowed Origin and warns loudly, so test day still works.
 *                    The expiry is minted on the ROBOT's clock (`bridgeNowMs`).
 */
import { createHmac, randomBytes } from "node:crypto";
import { Elysia } from "elysia";
import { betterAuth } from "#back/lib/auth-plugin.js";
import { bridgeSiblingUrl } from "../bridge/url";

//: Only has to outlive the handshake; each connect asks for a fresh one, since
//: the bridge accepts a token once.
export const TELEOP_TOKEN_TTL_S = 60;

/**
 * The robot's clock, read off the bridge's HTTP `Date` header (uvicorn stamps
 * every response). The expiry is checked there (`token_valid`, teleop/
 * server.py) and the Jetson has no RTC, so this laptop's clock can be minutes
 * off and every token born expired. Ours (the old behaviour) only if the
 * bridge does not answer.
 */
export async function bridgeNowMs(): Promise<number> {
  try {
    const res = await fetch(bridgeSiblingUrl("/safety/status"), {
      signal: AbortSignal.timeout(2000),
    });
    void res.body?.cancel();
    const date = Date.parse(res.headers.get("date") ?? "");
    if (Number.isFinite(date)) return date;
  } catch {
    // unreachable: fall through to our clock
  }
  return Date.now();
}

/** `<exp>.<nonce>.<hex HMAC-SHA256(secret, "teleop:<exp>.<nonce>")>`; mirrors `token_valid`. */
export function mintTeleopToken(
  secret: string,
  nowMs = Date.now(),
  // Two tokens minted in the same second must differ: each is single-use.
  nonce = randomBytes(16).toString("hex"),
): string {
  const body = `${Math.floor(nowMs / 1000) + TELEOP_TOKEN_TTL_S}.${nonce}`;
  const sig = createHmac("sha256", secret)
    .update(`teleop:${body}`)
    .digest("hex");
  return `${body}.${sig}`;
}

export const teleopRoutes = new Elysia({ prefix: "/teleop" })
  .use(betterAuth)
  .guard({ auth: true }, (app) =>
    app.get(
      "/token",
      async ({ status, user }) => {
        if (user.role !== "admin") {
          return status(403, { error: "admin_required" });
        }
        const secret = process.env.TELEOP_SECRET;
        return {
          token: secret ? mintTeleopToken(secret, await bridgeNowMs()) : null,
        };
      },
      {
        detail: {
          summary: "A short-lived token for the teleop socket. Admin-only.",
          tags: ["teleop"],
        },
      },
    ),
  );
