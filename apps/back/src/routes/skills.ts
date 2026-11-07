/**
 * /skills routes — catalogue + invoke + dry-run.
 *
 * GET /skills and GET /skills/:name return the catalogue, derived live from
 * the bridge's MCP `listTools()` (see `../skills/catalogue.ts`) rather than
 * duplicated here. POST /skills/:name/invoke validates the body against the
 * bridge's own JSON Schema (Ajv, via `../skills/validate.ts` — TypeBox can't
 * check a plain JSON Schema it didn't author) and dispatches to the bridge
 * over MCP (`../bridge/client`). POST /skills/:name/dry-run validates the
 * same way and returns a simulated preview (skill metadata + the would-be
 * invocation) WITHOUT touching the bridge — dispatching for real would move
 * the robot. Confirmation flow per SCRUM-32.
 *
 * /invoke is a raw low-level control surface (no LLM reasoning in the loop)
 * restricted to admins, like /agent and /voice: any session could otherwise
 * dispatch real robot motion directly over HTTP with zero safety reasoning
 * applied.
 *
 * Exception: `classification: "safety"` skills (today, only stop_everything)
 * skip the admin check. That's the dashboard's PARAR button's only direct
 * /invoke call, used by any operator — an e-stop is the one action that
 * should never get *more* friction than usual, not less accessible than a
 * skill that can actually put the robot in motion.
 *
 * Auth: this plugin guards ALL of its own routes with a single local
 * `.guard({ auth: true }, ...)` below, and `index.ts` mounts it *outside*
 * its own outer guard. Reason: `/invoke` needs `user` typed, which requires
 * the `auth` macro's resolver to run within this plugin's own scope — if
 * this plugin were also nested inside index.ts's outer guard (like tasks/
 * state/agent are), the session would resolve twice per request. Re-verified
 * 2026-10-01 against Elysia 1.4.28: nested, one /invoke ran the macro's
 * resolver twice; mounted beside the guard, once. index.ts had drifted back
 * to nesting it (so PARAR and every ~1.1/s VR walk_velocity paid two
 * `auth.api.getSession()` calls); it is mounted outside again. Every other
 * guarded route doesn't need typed `user`, so the outer guard alone is still
 * right for them.
 *
 * Audit: every dispatch that reaches the bridge through /invoke writes a
 * `tool_call_log` row (`source: "direct"`) — this is the reasoning-free path
 * behind every VR button, the walk loop, bring-up and PARAR, and it used to
 * leave no record at all. The write is fire-and-forget by construction: the
 * response never waits on the database, because the stop must never wait on
 * an audit row. Refusals before dispatch (401/403/404/422) are not dispatches
 * and are not logged here.
 */

import { Elysia, t } from "elysia";

import { getCatalogue, getSkill } from "../skills";
import { validateArgs } from "../skills/validate";
import {
  callTool,
  BridgeUnavailableError,
  BridgeToolError,
} from "../bridge/client";
import { bridgeSiblingUrl } from "../bridge/url";
import { betterAuth } from "#back/lib/auth-plugin.js";
import { logToolCall } from "#back/db/chats.js";
import { abortAllTurns } from "#back/agent/turns.js";
import {
  cancelMessage,
  dropPending,
  feedChat,
  feedLoop,
  outcomeMessage,
  restorePending,
  takePending,
} from "#back/agent/confirmations.js";

//: Skills that must dispatch even when the catalogue cannot be read.
//:
//: Deliberately a hardcoded name and not a heuristic: this list exists to keep
//: the e-stop working when the thing that normally answers "is this a safety
//: skill?" is exactly what has failed. It is checked against the catalogue's
//: own classification whenever the catalogue IS available, so the two cannot
//: drift silently — see the test.
export const SAFETY_SKILLS = new Set(["stop_everything", "soft_stop"]);

/** How long PARAR waits for the bridge before answering 504 (the stop keeps running). */
const SAFETY_TIMEOUT_MS = 8000;

/**
 * Ask the bridge to grant ONE call of `name` with exactly `args`, and audit
 * who asked. Arguments are not validated here: the bridge canonicalises them
 * against the tool's own schema and refuses what does not fit.
 */
async function grantOnBridge(
  name: string,
  args: Record<string, unknown>,
  userId: string,
  /** Set when the operator's Confirmar of a refused call asked: button or voice. */
  via?: "button" | "voice",
): Promise<{ status: number; body: Record<string, unknown> }> {
  const secret = process.env.SAFETY_CONFIRM_SECRET;
  if (!secret) {
    return { status: 503, body: { error: "confirmation_not_configured" } };
  }
  const startedAt = Date.now();
  let upstream: Response;
  try {
    upstream = await fetch(bridgeSiblingUrl("/safety/confirm"), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-c3po-confirm-secret": secret,
      },
      body: JSON.stringify({ tool: name, args }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return { status: 502, body: { error: "bridge_unavailable", name } };
  }
  const body = (await upstream.json().catch(() => ({}))) as Record<
    string,
    unknown
  >;
  // Who authorised which arm motion is the row worth having after the fact.
  void logToolCall({
    userId,
    source: "direct",
    skillName: "safety_confirm",
    params: { tool: name, args, ...(via ? { via } : {}) },
    result: body,
    status: upstream.ok ? "ok" : "error",
    ...(upstream.ok ? {} : { error: String(body.error ?? upstream.status) }),
    durationMs: Date.now() - startedAt,
    startedAt,
  });
  return { status: upstream.status, body };
}

/**
 * The /invoke dispatch, shared with the operator's Confirmar. `dispatched`
 * says the call left for the bridge (so a failure may still have moved it).
 */
async function invokeSkill(
  name: string,
  body: unknown,
  user: { id: string; role?: string | null },
  opts: { confirmed?: "button" | "voice" } = {},
): Promise<{ status: number; body: unknown; dispatched?: boolean }> {
  const knownSafety = SAFETY_SKILLS.has(name);
  // The stop never waits on the catalogue. `getSkill` asks the bridge
  // for its tool list first, and a bridge that accepts connections but
  // never answers held PARAR behind that list for the MCP default
  // timeout — the stop was not even attempted until it expired. A
  // known safety skill needs no schema (no arguments) and no admin
  // check, so it skips the lookup and is dispatched immediately.
  const skill = knownSafety ? undefined : await getSkill(name);

  // A missing catalogue entry is a 404 for everything EXCEPT a skill
  // we already know is a stop.
  //
  // `getSkill` reads the catalogue, which is fetched from the bridge
  // and cached. On a cold start with the bridge unreachable there is
  // nothing cached, so the catalogue is empty and every lookup misses
  // — including `stop_everything`. The dashboard's PARAR button then
  // answered `skill_not_found`, which reads as a bug in the button
  // rather than as "the bridge is unreachable", and is the single
  // worst moment to hand somebody a misleading error.
  //
  // The stop does not need the catalogue. It needs to be dispatched.
  // So a known safety skill skips the lookup and goes straight to the
  // bridge, where it either works or fails with a transport error that
  // says what is actually wrong.
  if (!skill && !knownSafety) {
    return { status: 404, body: { error: "skill_not_found", name } };
  }

  // Admin-only, except safety-classified skills (see file docstring)
  // — this bypasses the agent's reasoning loop entirely and
  // dispatches straight to the bridge. `knownSafety` covers the
  // catalogue being unavailable: an e-stop must not become
  // admin-gated because a fetch failed.
  const isSafety = knownSafety || skill?.classification === "safety";
  if (!isSafety && user.role !== "admin") {
    return {
      status: 403,
      body: {
        error: "admin_required",
        message:
          "Direct skill invocation is restricted to admins. Use /agent to drive the robot through the reasoning agent instead.",
      },
    };
  }

  // Schema validation (SCRUM-57, layer 1): fill the bridge's
  // declared defaults, then check the body before dispatch. The schema
  // is plain JSON Schema from the bridge now, so this goes through Ajv
  // — TypeBox throws on a schema that lacks its own [Kind] symbol.
  // No catalogue entry means no schema to validate against. That only
  // happens on the known-safety path above, where the arguments are
  // empty and the right answer is to dispatch rather than to refuse.
  let args: Record<string, unknown> = {};
  if (skill) {
    const validated = validateArgs(
      skill.parameters,
      body as Record<string, unknown>,
    );
    if (!validated.ok) {
      return {
        status: 422,
        body: { error: "invalid_params", name, issues: validated.issues },
      };
    }
    args = validated.value;
  }

  // The bridge gates a high-danger T1 (start_walking_waist,
  // arm_navigation, zero_torque) for every MCP caller. An admin's /invoke IS the
  // confirmation, so the console and headset bring-up grant this exact
  // call first. Only here: a grant in the agent's tool execute would
  // let every agent turn confirm itself. A T2 is granted only when
  // `confirmed` (the operator's Confirmar of that refused call, see
  // `confirmPending`), and `tier === "T1"` fails closed on a catalogue
  // without tiers.
  const highT1 = skill?.tier === "T1" && skill.dangerLevel === "high";
  if (highT1 || (opts.confirmed && skill?.tier === "T2")) {
    const granted = await grantOnBridge(name, args, user.id, opts.confirmed);
    if (granted.status >= 300) return granted;
  }

  // Audited whatever happens next. `void`, never `await`: an audit
  // row is not allowed to sit between an operator and PARAR.
  // `logToolCall` never throws, and reads a 200 body that reports a
  // failure (`status: "failed"`, a non-empty `error`) as `error`.
  const startedAt = Date.now();
  const audit = (
    outcome:
      { result: unknown } | { error: string; body: Record<string, unknown> },
  ) =>
    void logToolCall({
      userId: user.id,
      source: "direct",
      skillName: name,
      params: args,
      ...("result" in outcome
        ? { result: outcome.result, status: "ok" as const }
        : {
            result: outcome.body,
            status: "error" as const,
            error: outcome.error,
          }),
      durationMs: Date.now() - startedAt,
      startedAt,
    });

  const failureBody = (err: unknown) =>
    err instanceof BridgeUnavailableError
      ? { error: "bridge_unavailable", name }
      : err instanceof BridgeToolError
        ? { error: "tool_error", name, detail: err.detail }
        : { error: "bridge_error", name };
  const auditFailure = (err: unknown) => {
    const body = failureBody(err);
    audit({
      error: "detail" in body ? `${body.error}: ${body.detail}` : body.error,
      body,
    });
    return body;
  };

  // Every stop also ends every agent turn in flight, before the stop is
  // even sent: the model must not plan a next step around it.
  if (SAFETY_SKILLS.has(name)) {
    const aborted = abortAllTurns(name);
    if (aborted)
      console.warn(`[skills] ${name} aborted ${aborted} agent turn(s)`);
  }

  // The dispatch, audited when it SETTLES — however long that takes —
  // so the log records what really happened, not when we stopped
  // waiting.
  const dispatch = callTool(name, args).then(
    (result) => {
      audit({ result });
      return { ok: true as const, result };
    },
    (err: unknown) => ({ ok: false as const, body: auditFailure(err) }),
  );

  // A stop must answer quickly or not at all: unbounded, a hung bridge
  // held PARAR on "Parando…" for the MCP SDK's 60 s default. But the
  // bound is on our WAITING, not on the call: the SDK's own timeout
  // sends `notifications/cancelled`, which made the bridge cancel the
  // stop_everything it was in the middle of — zero velocity may already
  // have gone out and a damp may still land. So the call keeps running
  // and the operator is told the truth: no answer yet (504), not "the
  // order never left". Other skills keep the default — gestures and
  // walks legitimately run for tens of seconds.
  let timer: ReturnType<typeof setTimeout> | undefined;
  const settled = await (isSafety
    ? Promise.race([
        dispatch,
        new Promise<{ ok: "timeout" }>((resolve) => {
          timer = setTimeout(
            () => resolve({ ok: "timeout" }),
            SAFETY_TIMEOUT_MS,
          );
        }),
      ])
    : dispatch);
  if (timer) clearTimeout(timer);

  if (settled.ok === "timeout") {
    return {
      status: 504,
      body: { error: "bridge_timeout", name },
      dispatched: true,
    };
  }
  if (settled.ok)
    return { status: 200, body: settled.result, dispatched: true };
  return { status: 502, body: settled.body, dispatched: true };
}

// `detail` is for the operator; `message` is reserved for what the agent reads.
const NOT_PENDING = {
  error: "not_pending",
  detail:
    "Esa confirmación ya no está pendiente (ya se ejecutó, se canceló o pasaron más de 2 min): pedíselo de nuevo al agente.",
};

/**
 * The operator's Confirmar: grant AND run the exact call the bridge refused,
 * once, and say what happened in words for the model. The pending record is
 * taken before any await, so a second press (or a voice "confirmo" racing a
 * click) finds nothing and runs nothing. Callers are admin routes only; no
 * agent tool reaches this.
 */
export async function confirmPending(
  name: string,
  callId: string,
  args: unknown,
  user: { id: string; role?: string | null },
  via: "button" | "voice",
): Promise<{ status: number; body: Record<string, unknown> }> {
  const p = takePending(callId, user.id, name, args);
  if (p === "not_pending") return { status: 409, body: NOT_PENDING };
  if (p === "args_mismatch")
    return {
      status: 409,
      body: {
        error: "args_mismatch",
        detail:
          "Los argumentos no son los de la llamada rechazada: no se envió nada.",
      },
    };
  console.log(
    `[confirm] ${name} confirmed by ${user.id} via ${via}`,
    JSON.stringify(p.args),
  );
  const r = await invokeSkill(name, p.args, user, { confirmed: via });
  // Nothing left for the bridge (the grant failed): the card stays usable.
  if (!r.dispatched) restorePending(p);
  const ok = r.status === 200;
  const failure = r.body as { error?: string; detail?: string } | undefined;
  const message = outcomeMessage(
    name,
    ok
      ? { ok, result: r.body }
      : {
          ok,
          error: [failure?.error, failure?.detail].filter(Boolean).join(": "),
        },
  );
  if (p.loop && r.dispatched)
    feedLoop(
      message,
      ok ? `Confirmado: ejecuté ${name}.` : `Confirmado, pero ${name} falló.`,
    );
  if (p.chatId && r.dispatched) feedChat(p.chatId, message);
  return ok
    ? { status: 200, body: { tool: name, result: r.body, message } }
    : {
        status: r.status,
        body: {
          error: failure?.error ?? "error",
          ...(failure?.detail ? { detail: failure.detail } : {}),
          // Only what ran is the agent's business; a failed grant ran nothing.
          ...(r.dispatched ? { message } : {}),
        },
      };
}

/** The operator's Cancelar: the refused call is dropped, and nothing runs. */
export function cancelPending(
  name: string,
  callId: string,
  user: { id: string },
): { status: number; body: Record<string, unknown> } {
  const p = dropPending(callId, user.id, name);
  if (!p) return { status: 409, body: NOT_PENDING };
  console.log(`[confirm] ${name} cancelled by ${user.id}`);
  const message = cancelMessage(name);
  if (p.loop) feedLoop(message, `Cancelado: no ejecuto ${name}.`);
  if (p.chatId) feedChat(p.chatId, message);
  return { status: 200, body: { tool: name, cancelled: true, message } };
}

export const skillsRoutes = new Elysia({ prefix: "/skills" })
  .use(betterAuth)
  .guard({ auth: true }, (app) =>
    app
      .get(
        "/",
        async () => {
          // `source` and `age_seconds` ride the envelope so a consumer can
          // tell a live catalogue from a cached one. The bridge is on
          // Wi-Fi, on DHCP, and gets power-cycled; pretending otherwise
          // would just move the surprise.
          const snap = await getCatalogue();
          return {
            count: snap.skills.length,
            source: snap.source,
            age_seconds: snap.ageSeconds,
            ...(snap.error ? { bridge_error: snap.error } : {}),
            // Tools the bridge offered whose `_meta` could not be read. They
            // are excluded rather than served with a guessed danger level —
            // but excluded silently, a tool just vanished from the console.
            // Forwarded so /health can name them.
            ...(snap.rejected?.length ? { rejected: snap.rejected } : {}),
            skills: snap.skills,
          };
        },
        {
          detail: {
            summary: "List the full skill catalogue.",
            tags: ["skills"],
          },
        },
      )
      .get(
        "/:name",
        async ({ params: { name }, status }) => {
          const skill = await getSkill(name);
          if (!skill) return status(404, { error: "skill_not_found", name });
          return skill;
        },
        {
          params: t.Object({ name: t.String() }),
          detail: {
            summary: "Get a single skill definition.",
            tags: ["skills"],
          },
        },
      )
      .post(
        "/:name/invoke",
        async ({ params: { name }, body, status, user, server, request }) => {
          // A skill can run for tens of seconds; Bun drops a request that sends
          // nothing for 10 s. The MCP call's own timeout still bounds it.
          server?.timeout(request, 0);
          const r = await invokeSkill(name, body, user);
          return r.status === 200 ? r.body : status(r.status, r.body);
        },
        {
          params: t.Object({ name: t.String() }),
          body: t.Record(t.String(), t.Any()),
          detail: {
            summary:
              "Invoke a skill on the bridge (typed, validated dispatch). Admin-only.",
            tags: ["skills"],
          },
        },
      )
      .post(
        "/:name/confirm",
        async ({ params: { name }, body, status, user, server, request }) => {
          // The operator's Confirmar for ONE refused call of a gated skill (T2,
          // or high-danger T1): back grants it on the bridge (30 s, exactly
          // these arguments, bridge/safety.py) and runs it right here. It is a
          // route and not a tool so that no model can reach it, and it lives
          // here so the bridge's secret stays on this server, never in a
          // browser. `callId` names the refused call; `args` must be the ones
          // the operator was shown.
          if (user.role !== "admin") {
            return status(403, { error: "admin_required" });
          }
          // A pick runs for tens of seconds; Bun drops a silent request after
          // 10 s and the outcome would never reach the card. The MCP call's
          // own timeout still bounds it.
          server?.timeout(request, 0);
          const r = await confirmPending(
            name,
            body.callId,
            body.args ?? {},
            user,
            "button",
          );
          return r.status === 200 ? r.body : status(r.status, r.body);
        },
        {
          params: t.Object({ name: t.String() }),
          body: t.Object({
            callId: t.String({ maxLength: 128 }),
            args: t.Optional(t.Record(t.String(), t.Any())),
          }),
          detail: {
            summary:
              "Confirm AND run one refused call of a T2 or high-danger T1 skill, with its exact arguments. Admin-only.",
            tags: ["skills"],
          },
        },
      )
      .post(
        "/:name/cancel",
        ({ params: { name }, body, status, user }) => {
          if (user.role !== "admin") {
            return status(403, { error: "admin_required" });
          }
          const r = cancelPending(name, body.callId, user);
          return r.status === 200 ? r.body : status(r.status, r.body);
        },
        {
          params: t.Object({ name: t.String() }),
          body: t.Object({ callId: t.String({ maxLength: 128 }) }),
          detail: {
            summary:
              "Drop one refused call waiting for confirmation; nothing runs. Admin-only.",
            tags: ["skills"],
          },
        },
      )
      .post(
        "/:name/dry-run",
        async ({ params: { name }, body, status }) => {
          const skill = await getSkill(name);
          if (!skill) return status(404, { error: "skill_not_found", name });

          const {
            ok,
            value: args,
            issues,
          } = validateArgs(skill.parameters, body as Record<string, unknown>);
          if (!ok) {
            return status(422, { error: "invalid_params", name, issues });
          }

          // Simulated preview — never dispatches to the bridge.
          return {
            dry_run: true,
            skill: name,
            would_invoke: { name, params: args },
            danger_level: skill.dangerLevel,
            expected_duration_seconds: skill.expectedDurationSeconds,
            cancellable: skill.cancellable,
            preconditions: skill.preconditions ?? [],
            works: skill.works,
            note: `Simulated. POST /skills/${name}/invoke to execute.`,
          };
        },
        {
          params: t.Object({ name: t.String() }),
          body: t.Record(t.String(), t.Any()),
          detail: {
            summary: "Dry-run a skill — validated preview, no bridge dispatch.",
            tags: ["skills"],
          },
        },
      ),
  );
