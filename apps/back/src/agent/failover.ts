/**
 * Automatic provider failover for the agent's model calls.
 *
 * ia-tic goes down (502 `upstream_unreachable` on every model, 2026-11-11), and
 * the fix used to be restarting back with Gemini's env by hand. With a
 * fallback configured, a model call the primary fails BEFORE producing any
 * output is sent once to the fallback, and later calls stay there for
 * `stickyMs` so each step doesn't pay the primary's failure again.
 *
 * Only the call that failed is retried: the AI SDK calls the model once per
 * step, so tools a turn already ran are never run again. A stream that started
 * and then broke is not failed over: its text has already reached the operator.
 */

import { APICallError, wrapLanguageModel } from "ai";

type Model = ReturnType<typeof wrapLanguageModel>;

export type ProviderName = "primary" | "fallback";

/** Network errors, 5xx (the gateway's 502) and 429 fail over; a 4xx is our
 *  request's fault and would fail the same way on the fallback. */
export function shouldFailOver(err: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return false; // PARAR or the turn's own deadline
  if (err instanceof Error && err.name === "AbortError") return false;
  if (APICallError.isInstance(err)) {
    const status = err.statusCode;
    return status === undefined || status >= 500 || status === 429;
  }
  return err instanceof TypeError; // Bun's fetch: DNS, refused, reset
}

export function createFailover(opts: {
  stickyMs: number;
  /** A primary that sends no response headers by then counts as down. */
  headerTimeoutMs?: number;
  now?: () => number;
  log?: (event: string, detail: Record<string, unknown>) => void;
}) {
  const now = opts.now ?? Date.now;
  const headerTimeoutMs = opts.headerTimeoutMs ?? 20_000;
  const log =
    opts.log ??
    ((event, detail) =>
      console.log(`[agent] ${event}`, JSON.stringify(detail)));
  let fallbackUntil = 0;
  let onFallback = false;

  async function call<T>(
    params: { abortSignal?: AbortSignal },
    primary: (p: typeof params) => PromiseLike<T>,
    fallback: (p: typeof params) => PromiseLike<T>,
  ): Promise<T> {
    if (now() < fallbackUntil) return fallback(params);
    // Our own timer, cleared once headers arrive, so it never cuts a stream.
    const timer = new AbortController();
    const timeout = setTimeout(
      () => timer.abort(new Error("primary header timeout")),
      headerTimeoutMs,
    );
    const signal = params.abortSignal
      ? AbortSignal.any([params.abortSignal, timer.signal])
      : timer.signal;
    try {
      const out = await primary({ ...params, abortSignal: signal });
      if (onFallback) {
        onFallback = false;
        log("agent.provider_restored", { provider: "primary" });
      }
      return out;
    } catch (err) {
      const timedOut = timer.signal.aborted && !params.abortSignal?.aborted;
      if (!timedOut && !shouldFailOver(err, params.abortSignal)) throw err;
      fallbackUntil = now() + opts.stickyMs;
      onFallback = true;
      log("agent.provider_failover", {
        provider: "fallback",
        reason: timedOut ? "timeout" : String(err),
        stickyMs: opts.stickyMs,
      });
      return fallback(params);
    } finally {
      clearTimeout(timeout);
    }
  }

  return {
    /** Which provider the next call goes to first. */
    active: (): ProviderName =>
      now() < fallbackUntil ? "fallback" : "primary",
    wrap(primary: Model, fallback: Model): Model {
      return wrapLanguageModel({
        model: primary,
        middleware: {
          wrapStream: ({ params, model }) =>
            call(
              params,
              (p) => model.doStream({ ...params, ...p }),
              (p) => fallback.doStream({ ...params, ...p }),
            ),
          wrapGenerate: ({ params, model }) =>
            call(
              params,
              (p) => model.doGenerate({ ...params, ...p }),
              (p) => fallback.doGenerate({ ...params, ...p }),
            ),
        },
      });
    },
  };
}
