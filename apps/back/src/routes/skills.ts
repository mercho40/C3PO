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
 * state/agent are), the session would resolve twice per request (verified:
 * two separate `auth.api.getSession()` calls, doubling DB/session-store
 * load on the one route that actually moves the robot). Every other guarded
 * route doesn't need typed `user`, so the outer guard alone is still right
 * for them.
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
          const skill = await getSkill(name);
          if (!skill) return status(404, { error: "skill_not_found", name });

          // Admin-only, except safety-classified skills (see file docstring)
          // — this bypasses the agent's reasoning loop entirely and
          // dispatches straight to the bridge.
          if (skill.classification !== "safety" && user.role !== "admin") {
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
          const {
            ok,
            value: args,
            issues,
          } = validateArgs(skill.parameters, body as Record<string, unknown>);
          if (!ok) {
            return status(422, { error: "invalid_params", name, issues });
          }

          try {
            return await callTool(name, args);
          } catch (err) {
            if (err instanceof BridgeUnavailableError)
              return status(502, { error: "bridge_unavailable", name });
            if (err instanceof BridgeToolError)
              return status(502, {
                error: "tool_error",
                name,
                detail: err.detail,
              });
            return status(502, { error: "bridge_error", name });
          }
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
