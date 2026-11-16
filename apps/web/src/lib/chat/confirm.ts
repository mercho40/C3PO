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
  /**
   * False when the call is still pending on back: nothing reached the bridge
   * (a failed grant), or the answer never arrived. Pressing again is safe: back
   * takes a pending call once, so it can never run twice.
   */
  final: boolean;
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
    return {
      outcome: classifyHttpError(null, null),
      message: null,
      final: false,
    };
  }
  const body = (await res.json().catch(() => null)) as {
    result?: unknown;
    message?: unknown;
  } | null;
  const message = typeof body?.message === "string" ? body.message : null;
  // Back restores the call whenever nothing ran, and then has no `message`; a
  // 409 says it is no longer pending.
  if (!res.ok)
    return {
      outcome: classifyHttpError(res.status, body),
      message,
      final: message !== null || res.status === 409,
    };
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
    final: true,
  };
}

/** The pending call's arguments, as the operator must see them before deciding. */
export const argsText = (args: unknown) => JSON.stringify(args ?? {});

/** How often a shown card asks whether an enrolled face is at the robot (one check ≤ 1.5 s). */
export const FACE_POLL_MS = 2000;

/**
 * The face instead of «Confirmar», for as long as the card is on screen: asks
 * back (`/skills/:tool/confirm-face`) every FACE_POLL_MS. `state("waiting")`
 * once a check could run, so the card shows the hint; `state("unavailable")`
 * and it stops when the face cannot confirm this call (face confirm off, no
 * longer pending). An arm/hand call waits inside one request for the person to
 * step back (back says so aloud). `done` once a face ran it, with the same result a click
 * gives. Network trouble just tries again. Returns the stop.
 */
export function watchFace(
  base: string,
  tool: string,
  callId: string,
  args: unknown,
  on: {
    state: (s: "waiting" | "unavailable") => void;
    done: (r: DecisionResult) => void;
  },
  fetchImpl: typeof fetch = fetch,
  everyMs = FACE_POLL_MS,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const next = () => {
    if (!stopped) timer = setTimeout(tick, everyMs);
  };
  async function tick(): Promise<void> {
    let res: Response;
    try {
      res = await fetchImpl(
        `${base}/skills/${encodeURIComponent(tool)}/confirm-face`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ callId, args: args ?? {} }),
        },
      );
    } catch {
      return next();
    }
    if (stopped) return;
    const body = (await res.json().catch(() => null)) as {
      passed?: boolean;
      result?: unknown;
      message?: unknown;
    } | null;
    if (res.ok && body?.passed === false) {
      on.state("waiting");
      return next();
    }
    const message = typeof body?.message === "string" ? body.message : null;
    if (body?.passed === true) {
      stopped = true;
      on.done(
        res.ok
          ? { outcome: classifySkillResult(body.result), message, final: true }
          : {
              outcome: classifyHttpError(res.status, body),
              message,
              final: message !== null || res.status === 409,
            },
      );
      return;
    }
    if (res.status >= 500) return next();
    stopped = true;
    on.state("unavailable");
  }
  void tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
