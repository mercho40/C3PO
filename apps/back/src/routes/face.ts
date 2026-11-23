/**
 * /face — the «Caras» page: who the robot knows by face. Admins only, but
 * /face/seen, which any signed-in user's camera overlay polls.
 *
 * GET    /face/seen                   the boxes over the picture (signed in).
 * GET    /face/people                 the bridge's list, switches and counter;
 *                                     checked (bridge/payloads.ts FacesView), a
 *                                     body that is not is 502 `bridge_bad_payload`.
 * POST   /face/confirmations          {action, args}: the operator opened a
 *                                     Confirmar card for exactly this action
 *                                     and person; answers its id.
 * DELETE /face/confirmations/:id      «Cancelar» on that card.
 * POST   /face/settings?confirmation= {greet?, greet_unknown?, gate?,
 *                                     listen_after_greet?}: whoever turns
 *                                     the last one on owns the windows it
 *                                     opens (voice/service.ts).
 * DELETE /face/people/:id?confirmation=
 * POST   /face/enroll?confirmation=   {name, role, replace}; the bridge takes
 *                                     5–10 s, 20 s at most. A name that
 *                                     exists is refused (409 `exists`) unless
 *                                     the card says replace: true.
 * PATCH  /face/people/:id?confirmation= {role}: superadmin or operator;
 *                                     features and photo stay.
 * GET    /face/people/:id/photo.jpg?confirmation=   never cached.
 *
 * Seeing a face is confirmed like an action: every mutation and every photo
 * view needs a card noted here (agent/confirmations.ts, the same store as the
 * arm/hand cards), taken once, by its owner, fresh, for the same action and
 * arguments. PARAR voids it with every other card. The bridge then gets the
 * same proof /safety/confirm does (bridge/http.ts). Each relayed call is
 * audited (who, action, person); a photo's bytes never are.
 */
import { Elysia } from "elysia";

import {
  PENDING_TTL_MS,
  type Confirmations,
} from "#back/agent/confirmations.js";
import type { BridgeHttp } from "#back/bridge/http.js";
import {
  BridgeFailureBody,
  FacesViewSchema,
  badPayload,
  payloadCheck,
} from "#back/bridge/payloads.js";
import type { LogToolCall } from "#back/db/chats.js";
import type { AuthPlugin } from "#back/lib/auth-plugin.js";
import type { ListenWindow } from "#back/voice/listen-window.js";

export const FACE_ACTIONS = [
  "settings",
  "delete",
  "enroll",
  "photo",
  "role",
] as const;
export type FaceAction = (typeof FACE_ACTIONS)[number];
const SETTINGS = [
  "greet",
  "greet_unknown",
  "gate",
  "listen_after_greet",
] as const;
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
  if (action === "role")
    return typeof r.id === "string" && r.id && ROLES.has(r.role as string)
      ? { id: r.id, role: r.role }
      : null;
  if (action === "enroll") {
    const name = typeof r.name === "string" ? r.name.trim() : "";
    // `replace` is bound by the card: a «¿reemplazar?» confirmation cannot be
    // spent on a plain add, nor a plain add's card on a replacement.
    return name && name.length <= 80 && ROLES.has(r.role as string)
      ? { name, role: r.role, replace: r.replace === true }
      : null;
  }
  return null;
}

const checkPeople = payloadCheck(FacesViewSchema);

const json = (status: number, body: unknown) => Response.json(body, { status });

/**
 * Take the operator's card for this call and relay it with the bridge proof.
 * A card is single-use: a failure after it was taken means asking again.
 */
async function confirmedRelay(
  deps: FaceDeps,
  userId: string,
  action: FaceAction,
  raw: unknown,
  confirmation: string | undefined,
  path: string,
  method: "POST" | "DELETE" | "GET" | "PATCH",
  timeoutMs: number,
): Promise<Response> {
  const args = faceArgs(action, raw);
  if (!args) return json(422, { error: "invalid_args" });
  // Before the card is taken: a misconfigured server does not burn it.
  if (!deps.http.canProve())
    return json(503, { error: "confirmation_not_configured" });
  const taken = confirmation
    ? deps.confirmations.take(confirmation, userId, toolOf(action), args)
    : "not_pending";
  if (taken === "not_pending")
    return json(428, { error: "confirmation_required" });
  if (taken === "args_mismatch")
    return json(409, { error: "confirmation_mismatch" });

  const sendsBody =
    action === "settings" || action === "enroll" || action === "role";
  const startedAt = Date.now();
  let upstream: Response | null = null;
  try {
    upstream = await deps.http.faceRelay(
      method,
      path,
      sendsBody ? args : undefined,
      timeoutMs,
    );
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
  void deps.logToolCall({
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

type FaceDeps = {
  authPlugin: AuthPlugin;
  http: Pick<BridgeHttp, "canProve" | "faceRelay" | "faceSeen" | "facePeople">;
  confirmations: Pick<Confirmations, "note" | "take" | "drop">;
  logToolCall: LogToolCall;
  /** «Escuchar después de saludar» (voice/service.ts): the settings switch sets its owner. */
  listenWindow: Pick<ListenWindow, "setOwner">;
};

export const faceRoutes = (deps: FaceDeps) =>
  new Elysia({ prefix: "/face" })
    .use(deps.authPlugin)
    .guard({ auth: true }, (app) =>
      // Any signed-in user: the boxes go over a picture they already see. The
      // bridge serves it without proof only because it listens on loopback.
      app.get(
        "/seen",
        async () => {
          try {
            const upstream = await deps.http.faceSeen(1500);
            if (upstream.body === undefined)
              return json(502, { error: "bridge_unavailable" });
            return Response.json(upstream.body, {
              status: upstream.status,
              headers: { "Cache-Control": "no-store" },
            });
          } catch {
            return json(502, { error: "bridge_unavailable" });
          }
        },
        {
          detail: { summary: "Faces in the camera picture.", tags: ["face"] },
        },
      ),
    )
    .guard({ auth: true }, (app) =>
      app
        .onBeforeHandle(({ user, status }) => {
          if (user.role !== "admin")
            return status(403, { error: "admin_required" });
        })
        .get(
          "/people",
          async ({ status }) => {
            // Names and roles of everyone enrolled: the bridge wants the proof.
            if (!deps.http.canProve())
              return status(503, { error: "confirmation_not_configured" });
            try {
              const upstream = await deps.http.facePeople();
              const body = upstream.body;
              if (body === undefined)
                return status(502, { error: "bridge_unavailable" });
              // A refusal here is the bridge rejecting back's proof — a
              // misconfigured server, not this operator's session or rights.
              if (!upstream.ok)
                return status(502, {
                  error: "bridge_error",
                  message: `HTTP ${upstream.status}`,
                });
              const checked = checkPeople(body);
              return checked.ok
                ? checked.value
                : status(502, badPayload("/face/people", checked));
            } catch {
              return status(502, { error: "bridge_unavailable" });
            }
          },
          {
            response: {
              200: FacesViewSchema,
              502: BridgeFailureBody,
              503: BridgeFailureBody,
            },
            detail: { summary: "Enrolled faces and switches.", tags: ["face"] },
          },
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
            deps.confirmations.note({
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
            if (deps.confirmations.drop(params.id, user.id, toolOf(a))) break;
          return { cancelled: true };
        })
        .post("/settings", async ({ user, body, query }) => {
          const res = await confirmedRelay(
            deps,
            user.id,
            "settings",
            body,
            query.confirmation,
            "/face/settings",
            "POST",
            5000,
          );
          const listen = faceArgs("settings", body)?.listen_after_greet;
          if (res.ok && typeof listen === "boolean")
            deps.listenWindow.setOwner(listen ? user.id : null);
          return res;
        })
        .delete("/people/:id", ({ user, params, query }) =>
          confirmedRelay(
            deps,
            user.id,
            "delete",
            { id: params.id },
            query.confirmation,
            `/face/people/${encodeURIComponent(params.id)}`,
            "DELETE",
            5000,
          ),
        )
        // The person stands in front of the head camera while the bridge samples;
        // the bridge gives up after 20 s (ENROLL_DEADLINE_S), well inside this.
        .post("/enroll", ({ user, body, query }) =>
          confirmedRelay(
            deps,
            user.id,
            "enroll",
            body,
            query.confirmation,
            "/face/enroll",
            "POST",
            30_000,
          ),
        )
        .patch("/people/:id", ({ user, params, body, query }) =>
          confirmedRelay(
            deps,
            user.id,
            "role",
            { id: params.id, role: (body as { role?: unknown } | null)?.role },
            query.confirmation,
            `/face/people/${encodeURIComponent(params.id)}`,
            "PATCH",
            5000,
          ),
        )
        .get("/people/:id/photo.jpg", ({ user, params, query }) =>
          confirmedRelay(
            deps,
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
