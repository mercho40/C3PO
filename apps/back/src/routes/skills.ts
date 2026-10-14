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
 * /invoke is a raw low-level control surface (no LLM reasoning in the loop,
 * unlike /agent) restricted to admins: any session could otherwise dispatch
 * real robot motion directly over HTTP with zero safety reasoning applied.
 * Same "tighten before multi-tenant use" gap `/agent` already documents —
 * here it's more direct, so it's enforced now rather than deferred.
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
import { betterAuth } from "@back/lib/auth-plugin";
import { auth } from "@back/lib/auth";
import { env } from "@back/lib/env";
import { hasSignedSessionCookie } from "@back/lib/stop-auth";
import { logToolCall } from "@back/db/chats";

//: Skills that must dispatch even when the catalogue cannot be read.
//:
//: Deliberately a hardcoded name and not a heuristic: this list exists to keep
//: the e-stop working when the thing that normally answers "is this a safety
//: skill?" is exactly what has failed. It is checked against the catalogue's
//: own classification whenever the catalogue IS available, so the two cannot
//: drift silently — see the test.
export const SAFETY_SKILLS = new Set(["stop_everything"]);

/** How long PARAR waits for the bridge before answering 504 (the stop keeps running). */
const SAFETY_TIMEOUT_MS = 8000;

type StatusFn = (code: number, body: unknown) => unknown;

/**
 * Dispatch one skill, audit it when it settles, and answer.
 *
 * Shared by `/:name/invoke` and the dedicated `/stop_everything/invoke`, so the
 * two cannot drift in how a stop is timed, audited or reported. `userId` is
 * null only on the stop's database-down path (see that route).
 */
async function dispatchAudited({
  name,
  args,
  userId,
  isSafety,
  status,
}: {
  name: string;
  args: Record<string, unknown>;
  userId: string | null;
  isSafety: boolean;
  status: StatusFn;
}): Promise<unknown> {
  // Audited whatever happens next. `void`, never `await`: an audit
  // row is not allowed to sit between an operator and PARAR.
  // `logToolCall` never throws, and reads a 200 body that reports a
  // failure (`status: "failed"`, a non-empty `error`) as `error`.
  const startedAt = Date.now();
  const audit = (
    outcome:
      | { result: unknown }
      | { error: string; body: Record<string, unknown> },
  ) =>
    void logToolCall({
      userId,
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
    return status(504, { error: "bridge_timeout", name });
  }
  if (settled.ok) return settled.result;
  return status(502, settled.body);
}

export const skillsRoutes = new Elysia({ prefix: "/skills" })
  .use(betterAuth)
  // PARAR, on its own route so it survives a database outage.
  //
  // Every other route resolves the session through the `auth` macro, which
  // needs Postgres once the 5-minute cookie cache is stale — and a lookup that
  // THROWS (database down, Neon waking up past the deadline) became a 500, so
  // the console's stop never reached the robot. Here the lookup runs the same
  // way; only if it throws does the stop fall back to a session cookie this
  // server signed (`lib/stop-auth.ts`). No cookie, or a bad one: the original
  // error stands. "No session" is still a 401, as before. Nothing else is
  // allowed this path — a stale cookie can at worst stop the robot.
  //
  // The router prefers this static path over `/:name/invoke`, so this is the
  // only route a stop takes; the dispatch itself is the shared one.
  .post(
    "/stop_everything/invoke",
    async ({ request, status }) => {
      let userId: string | null;
      try {
        const session = await auth.api.getSession({ headers: request.headers });
        if (!session) return status(401);
        userId = session.user.id;
      } catch (err) {
        const signed = await hasSignedSessionCookie(
          request.headers.get("cookie"),
          env.BETTER_AUTH_SECRET,
        );
        if (!signed) throw err;
        console.error(
          "[skills] session lookup failed; PARAR dispatched on the signed session cookie",
          err,
        );
        // Unknown WHO: the user row is in the database that just failed.
        userId = null;
      }
      return dispatchAudited({
        name: "stop_everything",
        args: {},
        userId,
        isSafety: true,
        status,
      });
    },
    {
      body: t.Record(t.String(), t.Any()),
      detail: {
        summary:
          "PARAR. Dispatched even when the session database is unreachable, on a signed session cookie.",
        tags: ["skills"],
      },
    },
  )
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
        async ({ params: { name }, body, status, user }) => {
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
            return status(404, { error: "skill_not_found", name });
          }

          // Admin-only, except safety-classified skills (see file docstring)
          // — this bypasses the agent's reasoning loop entirely and
          // dispatches straight to the bridge. `knownSafety` covers the
          // catalogue being unavailable: an e-stop must not become
          // admin-gated because a fetch failed.
          const isSafety = knownSafety || skill?.classification === "safety";
          if (!isSafety && user.role !== "admin") {
            return status(403, {
              error: "admin_required",
              message:
                "Direct skill invocation is restricted to admins. Use /agent to drive the robot through the reasoning agent instead.",
            });
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
              return status(422, {
                error: "invalid_params",
                name,
                issues: validated.issues,
              });
            }
            args = validated.value;
          }

          return dispatchAudited({
            name,
            args,
            userId: user.id,
            isSafety,
            status,
          });
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
