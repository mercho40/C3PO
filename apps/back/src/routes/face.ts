/**
 * /face — the «Caras» page: who the robot knows by face. Admins only.
 *
 * GET    /face/people                 the bridge's list, switches and counter.
 * POST   /face/confirmations          {action, args}: the operator opened a
 *                                     Confirmar card for exactly this action
 *                                     and person; answers its id.
 * DELETE /face/confirmations/:id      «Cancelar» on that card.
 * POST   /face/settings?confirmation= {greet?, greet_unknown?, gate?}
 * DELETE /face/people/:id?confirmation=
 * POST   /face/enroll?confirmation=   {name, role}; the bridge takes 5–10 s.
 * GET    /face/people/:id/photo.jpg?confirmation=   never cached.
 *
 * Seeing a face is confirmed like an action: every mutation and every photo
 * view needs a card noted here (agent/confirmations.ts, the same store as the
 * arm/hand cards), taken once, by its owner, fresh, for the same action and
 * arguments. PARAR voids it with every other card. The bridge then gets the
 * same proof /safety/confirm does (bridge/proof.ts). Each relayed call is
 * audited (who, action, person); a photo's bytes never are.
 */
import { Elysia } from "elysia";
import { bridgeSiblingUrl } from "../bridge/url";
import { bridgeProof } from "../bridge/proof";
import { betterAuth } from "#back/lib/auth-plugin.js";
import { logToolCall } from "#back/db/chats.js";
import {
  PENDING_TTL_MS,
  dropPending,
  notePending,
  takePending,
} from "#back/agent/confirmations.js";

export const FACE_ACTIONS = ["settings", "delete", "enroll", "photo"] as const;
export type FaceAction = (typeof FACE_ACTIONS)[number];
const SETTINGS = ["greet", "greet_unknown", "gate"] as const;
const ROLES = new Set(["superadmin", "operator"]);
// The chatId slot of a card: no chat has this id, so no chat or /voice lists it.
const FACE_CARDS = "face";
const toolOf = (a: FaceAction) => `face_${a}`;

/** The exact arguments a confirmation binds, or null when `raw` is not valid for `action`. */
export function faceArgs(
  action: string,
  raw: unknown,
): Record<string, unknown> | null {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  if (action === "settings") {
    const out: Record<string, boolean> = {};
    for (const k of SETTINGS) if (typeof r[k] === "boolean") out[k] = r[k];
    return Object.keys(out).length ? out : null;
  }
  if (action === "delete" || action === "photo")
    return typeof r.id === "string" && r.id ? { id: r.id } : null;
  if (action === "enroll") {
    const name = typeof r.name === "string" ? r.name.trim() : "";
    return name && name.length <= 80 && ROLES.has(r.role as string)
      ? { name, role: r.role }
      : null;
  }
  return null;
}

const json = (status: number, body: unknown) => Response.json(body, { status });

/**
 * Take the operator's card for this call and relay it with the bridge proof.
 * A card is single-use: a failure after it was taken means asking again.
 */
async function confirmedRelay(
  userId: string,
  action: FaceAction,
  raw: unknown,
  confirmation: string | undefined,
  path: string,
  method: "POST" | "DELETE" | "GET",
  timeoutMs: number,
): Promise<Response> {
  const args = faceArgs(action, raw);
  if (!args) return json(422, { error: "invalid_args" });
  const proof = bridgeProof();
  // Before the card is taken: a misconfigured server does not burn it.
  if (!proof) return json(503, { error: "confirmation_not_configured" });
  const taken = confirmation
    ? takePending(confirmation, userId, toolOf(action), args)
    : "not_pending";
  if (taken === "not_pending")
    return json(428, { error: "confirmation_required" });
  if (taken === "args_mismatch")
    return json(409, { error: "confirmation_mismatch" });

  const sendsBody = action === "settings" || action === "enroll";
  const startedAt = Date.now();
  let upstream: Response | null = null;
  try {
    upstream = await fetch(bridgeSiblingUrl(path), {
      method,
      headers: sendsBody
        ? { "content-type": "application/json", ...proof }
        : proof,
      body: sendsBody ? JSON.stringify(args) : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    // Falls through to the audit row and the 502 below.
  }
  const photo = action === "photo" && upstream?.ok === true;
  const body = photo
    ? null
    : (((await upstream?.json().catch(() => ({}))) ?? {}) as Record<
        string,
        unknown
      >);
  const ok = upstream?.ok === true;
  // Who looked at or changed whose face. Never the photo itself.
  void logToolCall({
    userId,
    source: "direct",
    skillName: toolOf(action),
    params:
      action === "delete" || action === "photo" ? { person_id: args.id } : args,
    ...(photo ? {} : { result: body }),
    status: ok ? "ok" : "error",
    ...(ok
      ? {}
      : {
          error: upstream
            ? String(body?.error ?? body?.reason ?? upstream.status)
            : "bridge_unavailable",
        }),
    durationMs: Date.now() - startedAt,
    startedAt,
  });
  if (!upstream) return json(502, { error: "bridge_unavailable" });
  if (photo)
    return new Response(upstream.body, {
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") ?? "image/jpeg",
        // A face is shown once, to the operator who confirmed it.
        "Cache-Control": "no-store",
      },
    });
  return json(upstream.status, body);
}

export const faceRoutes = new Elysia({ prefix: "/face" })
  .use(betterAuth)
  .guard({ auth: true }, (app) =>
    app
      .onBeforeHandle(({ user, status }) => {
        if (user.role !== "admin")
          return status(403, { error: "admin_required" });
      })
      .get(
        "/people",
        async ({ status }) => {
          try {
            const upstream = await fetch(bridgeSiblingUrl("/face/people"), {
              signal: AbortSignal.timeout(3000),
            });
            const body = await upstream.json();
            return upstream.ok ? body : status(upstream.status, body);
          } catch {
            return status(502, { error: "bridge_unavailable" });
          }
        },
        { detail: { summary: "Enrolled faces and switches.", tags: ["face"] } },
      )
      .post(
        "/confirmations",
        ({ user, body, status }) => {
          const { action, args: raw } = (body ?? {}) as {
            action?: unknown;
            args?: unknown;
          };
          const known = FACE_ACTIONS.find((a) => a === action);
          const args = known ? faceArgs(known, raw) : null;
          if (!known || !args) return status(422, { error: "invalid_args" });
          const id = crypto.randomUUID();
          notePending({
            id,
            tool: toolOf(known),
            args,
            ownerId: user.id,
            chatId: FACE_CARDS,
            loop: false,
          });
          return { id, action: known, args, ttl_ms: PENDING_TTL_MS };
        },
        {
          detail: {
            summary: "Open a Confirmar card for one face action.",
            tags: ["face"],
          },
        },
      )
      .delete("/confirmations/:id", ({ user, params }) => {
        for (const a of FACE_ACTIONS)
          if (dropPending(params.id, user.id, toolOf(a))) break;
        return { cancelled: true };
      })
      .post("/settings", ({ user, body, query }) =>
        confirmedRelay(
          user.id,
          "settings",
          body,
          query.confirmation,
          "/face/settings",
          "POST",
          5000,
        ),
      )
      .delete("/people/:id", ({ user, params, query }) =>
        confirmedRelay(
          user.id,
          "delete",
          { id: params.id },
          query.confirmation,
          `/face/people/${encodeURIComponent(params.id)}`,
          "DELETE",
          5000,
        ),
      )
      // The person stands in front of the head camera while the bridge samples.
      .post("/enroll", ({ user, body, query }) =>
        confirmedRelay(
          user.id,
          "enroll",
          body,
          query.confirmation,
          "/face/enroll",
          "POST",
          30_000,
        ),
      )
      .get("/people/:id/photo.jpg", ({ user, params, query }) =>
        confirmedRelay(
          user.id,
          "photo",
          { id: params.id },
          query.confirmation,
          `/face/people/${encodeURIComponent(params.id)}/photo.jpg`,
          "GET",
          5000,
        ),
      ),
  );
