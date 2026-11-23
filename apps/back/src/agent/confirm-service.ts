/**
 * The operator's decision on a call the bridge refused for want of one:
 * Confirmar (a click, a spoken «confirmo», or a face at the camera) grants AND
 * runs that exact call once; Cancelar drops it. Either way the model is told
 * what happened, in words, and never retries (agent/confirmations.ts).
 *
 * Callers are admin routes only — POST /skills/:name/{confirm,confirm-face,
 * cancel} and the headset's push-to-talk (voice/service.ts). No agent tool
 * reaches this, so no model can confirm itself.
 *
 * It lived in routes/skills.ts, above the routes, where nothing but those
 * routes and an import from routes/voice.ts could reach it.
 */

import type { BridgeHttp } from "#back/bridge/http.js";
import { resultFailure } from "#back/db/chats.js";
import type { InvokeUser, Reply, SkillInvoker } from "#back/skills/invoke.js";
import {
  cancelMessage,
  outcomeMessage,
  type Confirmations,
} from "./confirmations";

/** A face-confirmed arm/hand call waits this long for everyone to step out of reach. */
export const FACE_STEP_BACK = { timeoutMs: 20_000, everyMs: 500 };

// `detail` is for the operator; `message` is reserved for what the agent reads.
const NOT_PENDING = {
  error: "not_pending",
  detail:
    "Esa confirmación ya no está pendiente (ya se ejecutó, se canceló o pasaron más de 2 min): pedíselo de nuevo al agente.",
};

export function createConfirmService(deps: {
  confirmations: Pick<
    Confirmations,
    "take" | "peek" | "drop" | "restore" | "publish"
  >;
  invoker: Pick<SkillInvoker, "invokeSkill">;
  http: Pick<BridgeHttp, "canProve" | "faceConfirmCheck" | "safetyStatus">;
  /** The robot says it (the face step-back line). Errors are ignored. */
  say: (text: string) => Promise<unknown>;
  /** Tests shorten it. */
  faceStepBack?: { timeoutMs: number; everyMs: number };
}) {
  const { confirmations } = deps;
  const stepBack = deps.faceStepBack ?? FACE_STEP_BACK;

  /**
   * The operator's Confirmar: grant AND run the exact call the bridge refused,
   * once, and say what happened in words for the model. The pending record is
   * taken before any await, so a second press (or a voice "confirmo" racing a
   * click) finds nothing and runs nothing.
   */
  async function confirmPending(
    name: string,
    callId: string,
    args: unknown,
    user: InvokeUser,
    via: "button" | "voice" | "face",
  ): Promise<Reply> {
    const p = confirmations.take(callId, user.id, name, args);
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
    const r = await deps.invoker.invokeSkill(name, p.args, user, {
      confirmed: via,
    });
    // Nothing left for the bridge (the grant failed): the card stays usable.
    if (!r.dispatched) confirmations.restore(p);
    const ok = r.status === 200;
    // A refused or failed task is still a 200 (the bridge never raises): the
    // card reads that body, but the model and the room must hear it failed.
    // A stop during the bridge's spoken warning (or PARAR mid-task) ends it
    // "cancelled": a warning to the console, but nothing the room may hear as done.
    const cancelled =
      ok &&
      (r.body as { status?: unknown } | undefined)?.status === "cancelled";
    const bodyFailure = ok
      ? (resultFailure(r.body) ?? (cancelled ? "cancelled" : null))
      : null;
    const ran = ok && !bodyFailure;
    const failure = r.body as
      | { error?: string; detail?: string; result?: { note?: unknown } }
      | undefined;
    const message = outcomeMessage(
      name,
      ran
        ? { ok: true, result: r.body }
        : {
            ok: false,
            error: bodyFailure
              ? `${bodyFailure}: ${JSON.stringify(r.body)}`
              : [failure?.error, failure?.detail].filter(Boolean).join(": "),
          },
    );
    // pick's planner leaves a short Spanish hint («Acercate ~0.14 m…») here.
    const note = failure?.result?.note;
    if (p.loop && r.dispatched)
      confirmations.publish({
        kind: "loop",
        message,
        spoken: ran
          ? `Confirmado: ejecuté ${name}.`
          : cancelled
            ? `Confirmado, pero ${name} se canceló.`
            : `Confirmado, pero ${name} falló${typeof note === "string" && note ? `: ${note}` : "."}`,
      });
    if (p.chatId && r.dispatched)
      confirmations.publish({ kind: "chat", chatId: p.chatId, message });
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

  /** True once the bridge sees nobody closer than `reach` (none seen counts), false at the deadline. */
  async function outOfReach(reach: number): Promise<boolean> {
    const deadline = Date.now() + stepBack.timeoutMs;
    while (Date.now() < deadline) {
      try {
        const r = await deps.http.safetyStatus(2000);
        const s = r.body;
        if (
          r.ok &&
          s !== undefined &&
          (s.nearest_person_m == null || s.nearest_person_m >= reach)
        )
          return true;
      } catch {
        // A missed poll is not "out of reach": try again until the deadline.
      }
      await Bun.sleep(stepBack.everyMs);
    }
    return false;
  }

  /**
   * The face instead of Confirmar: while the console shows a refused call, it
   * asks here every few seconds. The bridge looks for an enrolled person's
   * live face (/face/confirm_check, which grants nothing); on a pass this is
   * `confirmPending` with via "face", so the call is taken once, granted and
   * run exactly as a click would. `passed: false` means keep waiting. An
   * arm/hand call (`reach_m`) first waits for that person to step back out of
   * reach, the robot saying so: the face had to be inside it. A PARAR
   * meanwhile clears the card, so nothing runs after it.
   */
  async function confirmPendingByFace(
    name: string,
    callId: string,
    args: unknown,
    user: InvokeUser,
  ): Promise<Reply> {
    const p = confirmations.peek(callId, user.id, name, args);
    if (p === "not_pending") return { status: 409, body: NOT_PENDING };
    if (p === "args_mismatch")
      return { status: 409, body: { error: "args_mismatch" } };
    if (!deps.http.canProve())
      return { status: 503, body: { error: "confirmation_not_configured" } };
    let upstream: Awaited<ReturnType<BridgeHttp["faceConfirmCheck"]>>;
    try {
      upstream = await deps.http.faceConfirmCheck(name);
    } catch {
      return { status: 502, body: { error: "bridge_unavailable", name } };
    }
    const check = (upstream.body ?? {}) as {
      passed?: boolean;
      person?: { name?: string | null };
      reach_m?: number | null;
      [k: string]: unknown;
    };
    if (!upstream.ok) return { status: upstream.status, body: check };
    if (check.passed !== true)
      return { status: 200, body: { passed: false, reason: check.reason } };
    if (typeof check.reach_m === "number") {
      void deps
        .say(
          // Not «Confirmado»: that word is kept for a call that ran and succeeded.
          `Te reconocí. Alejate un poco y ${name === "set_hand" ? "muevo la mano" : "lo hago"}.`,
        )
        .catch(() => {});
      if (!(await outOfReach(check.reach_m)))
        return {
          status: 200,
          body: { passed: false, reason: "still_in_reach" },
        };
    }
    const r = await confirmPending(name, callId, args, user, "face");
    return {
      status: r.status,
      body: { ...r.body, passed: true, person: check.person?.name ?? null },
    };
  }

  /** The operator's Cancelar: the refused call is dropped, and nothing runs. */
  function cancelPending(
    name: string,
    callId: string,
    user: { id: string },
  ): Reply {
    const p = confirmations.drop(callId, user.id, name);
    if (!p) return { status: 409, body: NOT_PENDING };
    console.log(`[confirm] ${name} cancelled by ${user.id}`);
    const message = cancelMessage(name);
    if (p.loop)
      confirmations.publish({
        kind: "loop",
        message,
        spoken: `Cancelado: no ejecuto ${name}.`,
      });
    if (p.chatId)
      confirmations.publish({ kind: "chat", chatId: p.chatId, message });
    return { status: 200, body: { tool: name, cancelled: true, message } };
  }

  return { confirmPending, confirmPendingByFace, cancelPending };
}

export type ConfirmService = ReturnType<typeof createConfirmService>;
