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
 * The dispatch itself (`invokeSkill`, the bridge grant, SAFETY_SKILLS) is in
 * ../skills/invoke.ts, and the Confirmar / Cancelar workflow in
 * ../agent/confirm-service.ts: this file is the HTTP surface over them.
 *
 * Auth: this plugin guards ALL of its own routes with a single local
 * `.guard({ auth: true }, ...)` below, and `app.ts` mounts it *outside*
 * its own outer guard. Reason: `/invoke` needs `user` typed, which requires
 * the `auth` macro's resolver to run within this plugin's own scope — if
 * this plugin were also nested inside app.ts's outer guard (like tasks/
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

import type { ConfirmService } from "#back/agent/confirm-service.js";
import type { AuthPlugin } from "#back/lib/auth-plugin.js";
import type { Catalogue } from "../skills";
import type { SkillInvoker } from "../skills/invoke";
import { validateArgs } from "../skills/validate";

export const skillsRoutes = (deps: {
  authPlugin: AuthPlugin;
  catalogue: Pick<Catalogue, "getCatalogue" | "getSkill">;
  invoker: Pick<SkillInvoker, "invokeSkill">;
  confirm: ConfirmService;
}) =>
  new Elysia({ prefix: "/skills" })
    .use(deps.authPlugin)
    .guard({ auth: true }, (app) =>
      app
        .get(
          "/",
          async () => {
            // `source` and `age_seconds` ride the envelope so a consumer can
            // tell a live catalogue from a cached one. The bridge is on
            // Wi-Fi, on DHCP, and gets power-cycled; pretending otherwise
            // would just move the surprise.
            const snap = await deps.catalogue.getCatalogue();
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
            const skill = await deps.catalogue.getSkill(name);
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
            const r = await deps.invoker.invokeSkill(name, body, user);
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
            const r = await deps.confirm.confirmPending(
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
          "/:name/confirm-face",
          async ({ params: { name }, body, status, user, server, request }) => {
            // Same rules as /confirm, the click replaced by an enrolled live face
            // in front of the robot (agent/confirm-service.ts confirmPendingByFace).
            if (user.role !== "admin") {
              return status(403, { error: "admin_required" });
            }
            server?.timeout(request, 0);
            const r = await deps.confirm.confirmPendingByFace(
              name,
              body.callId,
              body.args ?? {},
              user,
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
                "Confirm AND run one refused high-danger T1 call when an enrolled person's live face is at the robot. Admin-only.",
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
            const r = deps.confirm.cancelPending(name, body.callId, user);
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
            const skill = await deps.catalogue.getSkill(name);
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
              summary:
                "Dry-run a skill — validated preview, no bridge dispatch.",
              tags: ["skills"],
            },
          },
        ),
    );
