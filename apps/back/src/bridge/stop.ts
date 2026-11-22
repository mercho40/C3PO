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
 * other `/invoke`: the audit row is written when the call SETTLES and is never
 * awaited, and the error mapping is the one the console reads.
 */

import { abortAllTurns } from "#back/agent/turns.js";
import { logToolCall } from "#back/db/chats.js";

import type { AuditWho } from "./audited";
import { BridgeToolError, BridgeUnavailableError, callTool } from "./client";

/**
 * How long a stop waits for the bridge before answering 504 (the stop keeps
 * running). An object so tests can shorten it.
 */
export const STOP_TIMEOUT = { ms: 8000 };

export type DispatchFailure =
  | { error: "bridge_unavailable"; name: string }
  | { error: "tool_error"; name: string; detail: string }
  | { error: "bridge_error"; name: string }
  | { error: "bridge_timeout"; name: string };

export type DispatchOutcome =
  { status: 200; body: unknown } | { status: 502 | 504; body: DispatchFailure };

/**
 * Call `name` on the bridge and audit it when it settles, however long that
 * takes, so the log records what really happened, not when we stopped waiting.
 *
 * With `waitMs`, the bound is on our WAITING, not on the call: the SDK's own
 * timeout sends `notifications/cancelled`, which made the bridge cancel the
 * stop_everything it was in the middle of — zero velocity may already have
 * gone out and a damp may still land. So the call keeps running and the caller
 * is told the truth: no answer yet (504), not "the order never left".
 */
export async function dispatchDirect(
  name: string,
  args: Record<string, unknown>,
  who: AuditWho,
  waitMs?: number,
): Promise<DispatchOutcome> {
  // Audited whatever happens next. `void`, never `await`: an audit row is not
  // allowed to sit between an operator and PARAR. `logToolCall` never throws,
  // and reads a 200 body that reports a failure (`status: "failed"`, a
  // non-empty `error`) as `error`.
  const startedAt = Date.now();
  const audit = (
    outcome:
      { result: unknown } | { error: string; body: Record<string, unknown> },
  ) =>
    void logToolCall({
      ...who,
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

  const failureBody = (err: unknown): DispatchFailure =>
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

  const dispatch = callTool(name, args).then(
    (result) => {
      audit({ result });
      return { ok: true as const, result };
    },
    (err: unknown) => ({ ok: false as const, body: auditFailure(err) }),
  );

  let timer: ReturnType<typeof setTimeout> | undefined;
  const settled = await (waitMs === undefined
    ? dispatch
    : Promise.race([
        dispatch,
        new Promise<{ ok: "timeout" }>((resolve) => {
          timer = setTimeout(() => resolve({ ok: "timeout" }), waitMs);
        }),
      ]));
  if (timer) clearTimeout(timer);

  if (settled.ok === "timeout")
    return { status: 504, body: { error: "bridge_timeout", name } };
  if (settled.ok) return { status: 200, body: settled.result };
  return { status: 502, body: settled.body };
}

/**
 * Stop the robot: end every agent turn in flight, then send `tool`
 * (stop_everything or soft_stop) and wait at most `STOP_TIMEOUT.ms` for it.
 *
 * The turns end BEFORE the stop is even sent: the model must not plan a next
 * step around it. A stop must answer quickly or not at all: unbounded, a hung
 * bridge held PARAR on "Parando…" for the MCP SDK's 60 s default.
 */
export function stopRobot(
  tool: string,
  who: AuditWho,
): Promise<DispatchOutcome> {
  const aborted = abortAllTurns(tool);
  if (aborted)
    console.warn(
      `[stop] ${tool} (${who.source}) aborted ${aborted} agent turn(s)`,
    );
  return dispatchDirect(tool, {}, who, STOP_TIMEOUT.ms);
}
