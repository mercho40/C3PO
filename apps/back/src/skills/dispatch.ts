/**
 * The one dispatch: send a skill to the bridge and write its audit row.
 *
 * WHY ONE. It was written four times — the agent's tools (agent/runtime.ts),
 * the realtime session (voice/realtime.ts), `auditedCall` (bridge/audited.ts:
 * task cancel, the voice loop, spoken replies) and the /invoke path
 * (`dispatchDirect`, bridge/stop.ts) — and the copies disagreed about what
 * the audit row says when a call fails. They agree now because there is one:
 *
 *   tool_error: <the bridge's reason>   the tool ran and reported failure
 *   bridge_unavailable                  no session, or the session was lost
 *   bridge_error                        anything else (a bug on our side)
 *
 * and the row's `result` is the failure body the console reads
 * (`{error, name, detail?}`), on every path. The agent used to log an
 * unexpected error as `String(err)`; it is `bridge_error` now, like everywhere
 * else, and the error itself goes to the server log — the same rule the app's
 * onError follows (app.ts): a stable code where it is stored and shown, the
 * real error where the person debugging it looks.
 *
 * Audited whatever happens, and never awaited: the row is written when the
 * call SETTLES (so it records what really happened, not when anyone stopped
 * waiting) and an audit row is not allowed to sit between an operator and
 * PARAR. `logToolCall` never throws, and reads a 200 body that reports a
 * failure (`status: "failed"`, a non-empty `error`) as `error`.
 */

import type { LogToolCall } from "#back/db/chats.js";
import type { ToolCallSource } from "#back/db/schema.js";
import {
  BridgeToolError,
  BridgeUnavailableError,
  type BridgeMcp,
} from "#back/bridge/client.js";

/**
 * Who dispatched, by which path — stamped on the audit row. `userId`
 * undefined (not null) on a chat-backed call means "the chat's owner"
 * (`logToolCall` resolves it); null records "nobody we know".
 */
export type AuditWho = {
  userId?: string | null;
  source: ToolCallSource;
  chatId?: string | null;
};

export type DispatchFailure =
  | { error: "bridge_unavailable"; name: string }
  | { error: "tool_error"; name: string; detail: string }
  | { error: "bridge_error"; name: string }
  | { error: "bridge_timeout"; name: string };

export type Dispatched =
  | { ok: true; result: unknown }
  | {
      ok: false;
      failure: DispatchFailure;
      /** What the bridge client threw; absent on `bridge_timeout`. */
      cause?: unknown;
    };

/** bridge/safety.py tool_refusal's wording; apps/web needsConfirmation reads it too. */
export const NEEDS_CONFIRMATION = "needs an operator's confirmation";

/** A failed call, as the console and the audit row name it. */
export function failureOf(name: string, err: unknown): DispatchFailure {
  if (err instanceof BridgeUnavailableError)
    return { error: "bridge_unavailable", name };
  if (err instanceof BridgeToolError)
    return { error: "tool_error", name, detail: err.detail };
  return { error: "bridge_error", name };
}

/** The audit row's `error` for a failure. */
export function auditError(f: DispatchFailure): string {
  return f.error === "tool_error" ? `tool_error: ${f.detail}` : f.error;
}

/** Whether the bridge refused the call for want of an operator's «Confirmar». */
export function needsConfirmation(f: DispatchFailure): boolean {
  return f.error === "tool_error" && f.detail.includes(NEEDS_CONFIRMATION);
}

export type DispatchOptions = {
  who: AuditWho;
  /**
   * Cancels the call ON THE BRIDGE (`notifications/cancelled`): for an agent
   * turn nobody follows any more. Never pass one for a stop.
   */
  signal?: AbortSignal;
  /**
   * Stop WAITING after this long and answer `bridge_timeout`; the call keeps
   * running and is audited when it settles. The SDK's own timeout would
   * cancel it on the bridge — mid-stop, for PARAR.
   */
  waitMs?: number;
  /** The bridge refused it pending an operator's confirmation. */
  onRefused?: () => void;
  /**
   * The settled call, for a caller that must wait for its robot calls
   * (an agent turn's `toolsSettled`). Called synchronously, before any wait.
   */
  track?: (settled: Promise<unknown>) => void;
};

export function createDispatcher(deps: {
  bridge: Pick<BridgeMcp, "callTool">;
  logToolCall: LogToolCall;
}) {
  async function dispatchSkill(
    name: string,
    args: Record<string, unknown>,
    opts: DispatchOptions,
  ): Promise<Dispatched> {
    const startedAt = Date.now();
    const row = {
      ...(opts.who.chatId !== undefined ? { chatId: opts.who.chatId } : {}),
      // Spread only when given: undefined must stay undefined so
      // `logToolCall` falls back to the chat's owner.
      ...(opts.who.userId !== undefined ? { userId: opts.who.userId } : {}),
      source: opts.who.source,
      skillName: name,
      params: args,
      startedAt,
    };

    const dispatch: Promise<Dispatched> = deps.bridge
      .callTool(name, args, opts.signal)
      .then(
        (result) => {
          void deps.logToolCall({
            ...row,
            result,
            status: "ok",
            durationMs: Date.now() - startedAt,
          });
          return { ok: true as const, result };
        },
        (cause: unknown) => {
          const failure = failureOf(name, cause);
          if (failure.error === "bridge_error")
            console.error(`[dispatch] ${name} failed:`, cause);
          // The row first: a caller's callback that throws must not lose it.
          void deps.logToolCall({
            ...row,
            result: failure,
            status: "error",
            error: auditError(failure),
            durationMs: Date.now() - startedAt,
          });
          if (needsConfirmation(failure)) opts.onRefused?.();
          return { ok: false as const, failure, cause };
        },
      );
    opts.track?.(dispatch);

    if (opts.waitMs === undefined) return dispatch;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const settled = await Promise.race([
      dispatch,
      new Promise<"timeout">((resolve) => {
        timer = setTimeout(() => resolve("timeout"), opts.waitMs);
      }),
    ]);
    clearTimeout(timer);
    return settled === "timeout"
      ? { ok: false, failure: { error: "bridge_timeout", name } }
      : settled;
  }

  /**
   * `dispatchSkill` for a caller that wants the result or the bridge client's
   * own error thrown (task cancel, the voice loop, spoken replies).
   */
  async function auditedCall(
    name: string,
    args: Record<string, unknown>,
    who: AuditWho,
  ): Promise<unknown> {
    const r = await dispatchSkill(name, args, { who });
    if (r.ok) return r.result;
    throw r.cause;
  }

  return { dispatchSkill, auditedCall };
}

export type Dispatcher = ReturnType<typeof createDispatcher>;
