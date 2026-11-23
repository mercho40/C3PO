/**
 * The bridge's plain-HTTP routes — everything beside `/mcp` — in one client.
 *
 * WHY THIS FILE EXISTS. About twenty `fetch(bridgeSiblingUrl(...))` calls were
 * spread over a dozen files. `/safety/status` alone was fetched four times,
 * with four timeouts and four ways of reading the answer; the proof header
 * that privileged calls carry was assembled in three; and tests could only
 * reach any of it by patching `globalThis.fetch`. Now:
 *
 *   - one URL rule: every path is a sibling of `config.bridge.mcpUrl`
 *     (bridge/url.ts), query string dropped;
 *   - one timeout policy: every finite call names its own bound, which covers
 *     reading the body too (the same signal); streams take the caller's signal
 *     and nothing else. Where call sites used to disagree, each still passes its
 *     own value: unifying them would change timing nobody asked to change;
 *   - one error mapping: a request that never got an answer (refused,
 *     reset, timed out) throws `BridgeHttpUnavailableError`, which every caller
 *     reports as `bridge_unavailable`; an answer is returned as it came, status
 *     and all, for the caller to read;
 *   - the proof (`SAFETY_CONFIRM_SECRET` in the header the bridge checks with
 *     `safety.confirm_secret_ok`) built once, so /safety/confirm, /safety/ack
 *     and /face/* cannot drift apart. Without a secret `canProve()` is false and
 *     the caller answers 503 `confirmation_not_configured` BEFORE doing anything
 *     it cannot undo (taking a confirmation card, say).
 *
 * `fetch` is injected, so tests hand the client a fake bridge instead of
 * patching the global.
 */

import { bridgeSiblingUrl } from "./url";

/** The bridge never answered: refused, reset, or past the call's timeout. */
export class BridgeHttpUnavailableError extends Error {
  constructor(
    readonly path: string,
    cause?: unknown,
  ) {
    super("bridge_unavailable");
    this.name = "BridgeHttpUnavailableError";
    this.cause = cause;
  }
}

/**
 * For a caller that reports the transport's own words (the voice loop's and
 * the realtime session's `lastError`, `look`'s reason): rethrow what fetch
 * threw, not the mapped error. `.catch(rethrowCause)`.
 */
export function rethrowCause(err: unknown): never {
  throw err instanceof BridgeHttpUnavailableError && err.cause
    ? err.cause
    : err;
}

/** The header the bridge checks on privileged calls. */
export const PROOF_HEADER = "x-c3po-confirm-secret";

export type RequestOptions = {
  method?: "GET" | "POST" | "DELETE" | "PATCH";
  /** Sent as JSON, with its content-type. */
  json?: unknown;
  /** Sent as is (audio). */
  body?: BodyInit;
  headers?: Record<string, string>;
  query?: Record<string, string>;
  /** Carry the proof. Check `canProve()` first: without a secret this throws. */
  proof?: boolean;
  /** Bounds the whole exchange, body included. Omit only for a stream. */
  timeoutMs?: number;
  /** The caller's own signal (a stream's viewer leaving, a loop stopping). */
  signal?: AbortSignal;
  cache?: RequestCache;
};

/** An answer from the bridge: its status, and its JSON body if it had one. */
export type JsonReply<T = Record<string, unknown>> = {
  ok: boolean;
  status: number;
  /** undefined when the body was not JSON, or could not be read in time. */
  body: T | undefined;
};

/** The bridge's governor status (apps/bridge safety.py `status()`), as back reads it. */
export type SafetyStatus = {
  stopped_at?: number | null;
  stop_standing?: boolean;
  nearest_person_m?: number | null;
  [key: string]: unknown;
};

/** A relayed answer, for the proxy routes (telemetry, map, camera). */
export type Relay =
  | { ok: true; upstream: Response; body: ArrayBuffer }
  | { ok: false; status: 502 | 503; body: unknown };

async function readJson<T>(res: Response): Promise<T | undefined> {
  try {
    return (await res.json()) as T;
  } catch {
    return undefined;
  }
}

export function createBridgeHttp(opts: {
  mcpUrl: string;
  confirmSecret: string | null;
  fetch?: (input: string, init: RequestInit) => Promise<Response>;
}) {
  const doFetch =
    opts.fetch ??
    ((input: string, init: RequestInit) => globalThis.fetch(input, init));
  const proof: Record<string, string> | null = opts.confirmSecret
    ? { [PROOF_HEADER]: opts.confirmSecret }
    : null;

  function url(path: string, query?: Record<string, string>): string {
    const u = new URL(bridgeSiblingUrl(path, opts.mcpUrl));
    for (const [k, v] of Object.entries(query ?? {})) u.searchParams.set(k, v);
    return u.toString();
  }

  /** One request. Throws `BridgeHttpUnavailableError` when no answer came. */
  async function request(
    path: string,
    o: RequestOptions = {},
  ): Promise<Response> {
    if (o.proof && !proof) throw new Error("confirmation_not_configured");
    const signals = [
      ...(o.timeoutMs !== undefined ? [AbortSignal.timeout(o.timeoutMs)] : []),
      ...(o.signal ? [o.signal] : []),
    ];
    const headers: Record<string, string> = {
      ...(o.json !== undefined ? { "content-type": "application/json" } : {}),
      ...o.headers,
      ...(o.proof ? proof : {}),
    };
    try {
      return await doFetch(url(path, o.query), {
        method: o.method ?? "GET",
        headers,
        body: o.json !== undefined ? JSON.stringify(o.json) : o.body,
        ...(o.cache ? { cache: o.cache } : {}),
        ...(signals.length
          ? {
              signal:
                signals.length === 1 ? signals[0] : AbortSignal.any(signals),
            }
          : {}),
      });
    } catch (err) {
      throw new BridgeHttpUnavailableError(path, err);
    }
  }

  /** A request whose answer is JSON (or an error status with a JSON body). */
  async function requestJson<T = Record<string, unknown>>(
    path: string,
    o: RequestOptions,
  ): Promise<JsonReply<T>> {
    const res = await request(path, o);
    return { ok: res.ok, status: res.status, body: await readJson<T>(res) };
  }

  return {
    url,
    request,
    requestJson,

    /** Whether privileged calls can be made (SAFETY_CONFIRM_SECRET is set). */
    canProve: (): boolean => proof !== null,

    /**
     * The PARAR latch and the governor. Four readers, four bounds, kept: the
     * console's poll (3 s), stop-watch's 500 ms loop (1 s), the face step-back
     * wait (2 s).
     */
    safetyStatus: (timeoutMs: number) =>
      requestJson<SafetyStatus>("/safety/status", { timeoutMs }),

    /**
     * The robot's clock, from the HTTP `Date` header uvicorn stamps on every
     * answer (teleop tokens expire on it). null when the bridge does not
     * answer or sends no date; the body is never read.
     */
    async robotDateMs(timeoutMs: number): Promise<number | null> {
      try {
        const res = await request("/safety/status", { timeoutMs });
        void res.body?.cancel();
        const date = Date.parse(res.headers.get("date") ?? "");
        return Number.isFinite(date) ? date : null;
      } catch {
        return null;
      }
    },

    /** Grant ONE call of `tool` with exactly `args` (30 s, bridge safety.py). */
    safetyConfirm: (grant: { tool: string; args: unknown; via?: "face" }) =>
      requestJson("/safety/confirm", {
        method: "POST",
        proof: true,
        // `via` only labels the bridge's log line; the secret is the proof.
        json: {
          tool: grant.tool,
          args: grant.args,
          ...(grant.via ? { via: grant.via } : {}),
        },
        timeoutMs: 5000,
      }),

    /** «Reanudar»: clear the stop stamped `stoppedAt` (409 for a newer one). */
    safetyAck: (stoppedAt: number | null) =>
      requestJson("/safety/ack", {
        method: "POST",
        proof: true,
        json: { stopped_at: stoppedAt },
        timeoutMs: 5000,
      }),

    /** Whether an enrolled person's live face is at the camera. Grants nothing. */
    faceConfirmCheck: (tool: string) =>
      requestJson("/face/confirm_check", {
        method: "POST",
        proof: true,
        json: { tool },
        timeoutMs: 5000,
      }),

    /** The camera's face boxes and the last enrolled greeting. */
    faceSeen: (timeoutMs: number) => requestJson("/face/seen", { timeoutMs }),

    /** Everyone enrolled, the switches and the counter. */
    facePeople: () =>
      requestJson("/face/people", { proof: true, timeoutMs: 3000 }),

    /** A confirmed «Caras» call (routes/face.ts), relayed with the proof. */
    faceRelay: (
      method: "POST" | "DELETE" | "GET" | "PATCH",
      path: string,
      args: Record<string, unknown> | undefined,
      timeoutMs: number,
    ) =>
      request(path, {
        method,
        proof: true,
        ...(args !== undefined ? { json: args } : {}),
        timeoutMs,
      }),

    /**
     * A short, finite GET relayed whole: the proxy routes' one error mapping.
     * No answer, or a body that could not be read in time, is 502
     * `bridge_unavailable`; a 503 is the bridge saying why there is nothing
     * and passes through with its body (`unavailable` when it has none); any
     * other non-OK status is 502 `bridge_error`.
     */
    async relay(
      path: string,
      o: { timeoutMs: number; unavailableBody: unknown },
    ): Promise<Relay> {
      let upstream: Response;
      try {
        upstream = await request(path, { timeoutMs: o.timeoutMs });
      } catch {
        return {
          ok: false,
          status: 502,
          body: { error: "bridge_unavailable" },
        };
      }
      if (upstream.status === 503)
        return {
          ok: false,
          status: 503,
          body: (await readJson(upstream)) ?? o.unavailableBody,
        };
      if (!upstream.ok)
        return { ok: false, status: 502, body: { error: "bridge_error" } };
      // The timeout covers reading the BODY too: headers that arrive just
      // before it leave `arrayBuffer()` to throw, which escaped as a 500 — a
      // server bug, to the console — instead of the bridge being slow.
      const body = await upstream.arrayBuffer().catch(() => null);
      if (body === null)
        return {
          ok: false,
          status: 502,
          body: { error: "bridge_unavailable" },
        };
      return { ok: true, upstream, body };
    },
  };
}

export type BridgeHttp = ReturnType<typeof createBridgeHttp>;
