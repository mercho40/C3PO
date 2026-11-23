/**
 * `/skills/:name/invoke`'s dispatch, shared with the operator's Confirmar
 * (agent/confirm-service.ts): look the skill up, gate it (admin unless a
 * stop), validate its arguments, grant it on the bridge when the bridge gates
 * it, and dispatch it — a stop through `stopRobot`, the one stop path.
 *
 * /invoke is a raw low-level control surface (no LLM reasoning in the loop)
 * restricted to admins, like /agent and /voice: any session could otherwise
 * dispatch real robot motion directly over HTTP with zero safety reasoning
 * applied. Exception: `classification: "safety"` skills skip the admin check —
 * an e-stop is the one action that should never get *more* friction.
 */

import type { BridgeHttp } from "#back/bridge/http.js";
import type { Stop } from "#back/bridge/stop.js";
import type { LogToolCall } from "#back/db/chats.js";
import type { Catalogue } from "./catalogue";
import { validateArgs } from "./validate";

/** How the operator confirmed a refused call: a click, a spoken «confirmo», or their face. */
export type Via = "button" | "voice" | "face";

//: Skills that must dispatch even when the catalogue cannot be read.
//:
//: Deliberately a hardcoded name and not a heuristic: this list exists to keep
//: the e-stop working when the thing that normally answers "is this a safety
//: skill?" is exactly what has failed. It is checked against the catalogue's
//: own classification whenever the catalogue IS available, so the two cannot
//: drift silently — see the test.
export const SAFETY_SKILLS = new Set(["stop_everything", "soft_stop"]);

export type InvokeUser = { id: string; role?: string | null };

export type Reply = { status: number; body: Record<string, unknown> };

export function createSkillInvoker(deps: {
  catalogue: Pick<Catalogue, "getSkill">;
  stop: Pick<Stop, "dispatchDirect" | "stopRobot" | "timeoutMs">;
  http: Pick<BridgeHttp, "canProve" | "safetyConfirm">;
  logToolCall: LogToolCall;
}) {
  /**
   * Ask the bridge to grant ONE call of `name` with exactly `args`, and audit
   * who asked. Arguments are not validated here: the bridge canonicalises them
   * against the tool's own schema and refuses what does not fit.
   */
  async function grantOnBridge(
    name: string,
    args: Record<string, unknown>,
    userId: string,
    /** Set when the operator's Confirmar of a refused call asked: button, voice or face. */
    via?: Via,
  ): Promise<Reply> {
    if (!deps.http.canProve()) {
      return { status: 503, body: { error: "confirmation_not_configured" } };
    }
    const startedAt = Date.now();
    let upstream: Awaited<ReturnType<BridgeHttp["safetyConfirm"]>>;
    try {
      upstream = await deps.http.safetyConfirm({
        tool: name,
        args,
        ...(via === "face" ? { via } : {}),
      });
    } catch {
      return { status: 502, body: { error: "bridge_unavailable", name } };
    }
    const body = upstream.body ?? {};
    // Who authorised which arm motion is the row worth having after the fact.
    void deps.logToolCall({
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
    user: InvokeUser,
    opts: { confirmed?: Via } = {},
  ): Promise<{ status: number; body: unknown; dispatched?: boolean }> {
    const knownSafety = SAFETY_SKILLS.has(name);
    // The stop never waits on the catalogue. `getSkill` asks the bridge
    // for its tool list first, and a bridge that accepts connections but
    // never answers held PARAR behind that list for the MCP default
    // timeout — the stop was not even attempted until it expired. A
    // known safety skill needs no schema (no arguments) and no admin
    // check, so it skips the lookup and is dispatched immediately.
    const skill = knownSafety ? undefined : await deps.catalogue.getSkill(name);

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
          body: {
            error: "invalid_params",
            name,
            // What the operator reads; the confirm path forwards only `detail`.
            detail: validated.issues
              .map((i) => `${i.path} ${i.message}`)
              .join("; "),
            issues: validated.issues,
          },
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

    // Audited when it settles, never awaited (bridge/stop.ts). A stop goes
    // through `stopRobot`, the one stop path the /tasks estop shares: it ends
    // every agent turn first, and answers 504 after 8 s while the stop keeps
    // running. Other skills keep no bound — gestures and walks legitimately
    // run for tens of seconds — except one the catalogue classifies as safety.
    const who = { userId: user.id, source: "direct" as const };
    const r = SAFETY_SKILLS.has(name)
      ? await deps.stop.stopRobot(name, who)
      : await deps.stop.dispatchDirect(
          name,
          args,
          who,
          isSafety ? deps.stop.timeoutMs : undefined,
        );
    return { ...r, dispatched: true };
  }

  return { invokeSkill };
}

export type SkillInvoker = ReturnType<typeof createSkillInvoker>;
