/**
 * The operator's decision on a call the bridge refused for want of a
 * confirmation: one action. «Confirmar» makes apps/back grant AND run that
 * exact call (routes/skills.ts `confirmPending`); «Cancelar» drops it. The
 * answer carries `message`, the words the agent must read next, so it
 * continues from the outcome instead of retrying. Shared by the chat card, the
 * /voice page and the headset.
 *
 * Plain module, no runes, so `bun test` can load it.
 */
import {
  classifyHttpError,
  classifySkillResult,
  type SkillOutcome,
} from "../robot/skill-outcome";

export type Decision = "confirm" | "cancel";

export type DecisionResult = {
  outcome: SkillOutcome;
  /** For the agent; null when nothing ran (or the answer never arrived). */
  message: string | null;
};

export async function decideCall(
  base: string,
  tool: string,
  callId: string,
  args: unknown,
  decision: Decision,
  fetchImpl: typeof fetch = fetch,
): Promise<DecisionResult> {
  let res: Response;
  try {
    res = await fetchImpl(
      `${base}/skills/${encodeURIComponent(tool)}/${decision}`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          decision === "confirm" ? { callId, args: args ?? {} } : { callId },
        ),
      },
    );
  } catch {
    // It may have run: back stopped answering, not necessarily the robot.
    return { outcome: classifyHttpError(null, null), message: null };
  }
  const body = (await res.json().catch(() => null)) as {
    result?: unknown;
    message?: unknown;
  } | null;
  const message = typeof body?.message === "string" ? body.message : null;
  if (!res.ok) return { outcome: classifyHttpError(res.status, body), message };
  return {
    outcome:
      decision === "cancel"
        ? {
            kind: "warn",
            text: "Cancelado: no se ejecutó",
            detail: null,
            code: "cancelled",
            simulated: false,
          }
        : classifySkillResult(body?.result),
    message,
  };
}

/** The pending call's arguments, as the operator must see them before deciding. */
export const argsText = (args: unknown) => JSON.stringify(args ?? {});
