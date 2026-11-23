/**
 * The stop, sent from apps/back. ONE path for every route that stops the robot.
 *
 * PARAR reached the bridge two ways — `/skills/stop_everything/invoke` (the
 * console's button, the headset) and `/tasks/:id/cancel` with `mode: "estop"` —
 * and they had drifted: only the first bounded its wait, so a hung bridge held
 * the estop for the MCP default while the button answered 504 after 8 s. Both
 * call `stopRobot` now, so a fix to one is a fix to the other.
 *
 * `dispatchDirect` is the audited dispatch underneath it, shared with every
 * other `/invoke`: `dispatchSkill` (skills/dispatch.ts) with the answer shaped
 * the way the console reads it. The audit row is written when the call SETTLES
 * and is never awaited.
 */

import type { Turns } from "#back/agent/turns.js";
import type {
  AuditWho,
  DispatchFailure,
  Dispatcher,
} from "#back/skills/dispatch.js";

export type { DispatchFailure };

/** How long a stop waits for the bridge before answering 504 (the stop keeps running). */
export const STOP_TIMEOUT_MS = 8000;

export type DispatchOutcome =
  { status: 200; body: unknown } | { status: 502 | 504; body: DispatchFailure };

export function createStop(deps: {
  dispatch: Pick<Dispatcher, "dispatchSkill">;
  turns: Pick<Turns, "abortAll">;
  /** Tests shorten it. */
  timeoutMs?: number;
}) {
  const timeoutMs = deps.timeoutMs ?? STOP_TIMEOUT_MS;

  /**
   * Call `name` on the bridge and audit it when it settles, however long that
   * takes, so the log records what really happened, not when we stopped
   * waiting.
   *
   * With `waitMs`, the bound is on our WAITING, not on the call: the SDK's own
   * timeout sends `notifications/cancelled`, which made the bridge cancel the
   * stop_everything it was in the middle of — zero velocity may already have
   * gone out and a damp may still land. So the call keeps running and the
   * caller is told the truth: no answer yet (504), not "the order never left".
   */
  async function dispatchDirect(
    name: string,
    args: Record<string, unknown>,
    who: AuditWho,
    waitMs?: number,
  ): Promise<DispatchOutcome> {
    const r = await deps.dispatch.dispatchSkill(name, args, { who, waitMs });
    if (r.ok) return { status: 200, body: r.result };
    return {
      status: r.failure.error === "bridge_timeout" ? 504 : 502,
      body: r.failure,
    };
  }

  /**
   * Stop the robot: end every agent turn in flight, then send `tool`
   * (stop_everything or soft_stop) and wait at most `timeoutMs` for it.
   *
   * The turns end BEFORE the stop is even sent: the model must not plan a next
   * step around it. A stop must answer quickly or not at all: unbounded, a hung
   * bridge held PARAR on "Parando…" for the MCP SDK's 60 s default.
   */
  function stopRobot(tool: string, who: AuditWho): Promise<DispatchOutcome> {
    const aborted = deps.turns.abortAll(tool);
    if (aborted)
      console.warn(
        `[stop] ${tool} (${who.source}) aborted ${aborted} agent turn(s)`,
      );
    return dispatchDirect(tool, {}, who, timeoutMs);
  }

  return { dispatchDirect, stopRobot, timeoutMs };
}

export type Stop = ReturnType<typeof createStop>;
