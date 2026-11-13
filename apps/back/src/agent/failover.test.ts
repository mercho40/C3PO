import { describe, expect, test } from "bun:test";
import { APICallError, streamText } from "ai";
import { MockLanguageModelV4, convertArrayToReadableStream } from "ai/test";

import { createFailover } from "./failover";

const say = (text: string) => [
  { type: "text-start" as const, id: "t1" },
  { type: "text-delta" as const, id: "t1", delta: text },
  { type: "text-end" as const, id: "t1" },
  {
    type: "finish" as const,
    finishReason: { unified: "stop" as const, raw: "stop" },
    usage: {
      inputTokens: {
        total: 1,
        noCache: 1,
        cacheRead: undefined,
        cacheWrite: undefined,
      },
      outputTokens: { total: 1, text: 1, reasoning: undefined },
    },
  },
];

const apiError = (statusCode?: number) =>
  new APICallError({
    message: statusCode
      ? `HTTP ${statusCode}`
      : "Cannot connect to API: ECONNREFUSED",
    url: "https://ia-tic.ort.edu.ar/api/v1/chat/completions",
    requestBodyValues: {},
    statusCode,
    isRetryable: true,
  });

/** A primary failing with `err` (or answering "tic" when null), and a fallback
 *  answering "gemini"; both count their calls. */
function setup(err: unknown | null, opts: { stickyMs?: number } = {}) {
  let t = 1_000;
  const calls = { primary: 0, fallback: 0 };
  const events: string[] = [];
  const failover = createFailover({
    stickyMs: opts.stickyMs ?? 300_000,
    now: () => t,
    log: (e) => void events.push(e),
  });
  const state = { err };
  const primary = new MockLanguageModelV4({
    doStream: async () => {
      calls.primary++;
      if (state.err) throw state.err;
      return { stream: convertArrayToReadableStream(say("tic")) };
    },
  });
  const fallback = new MockLanguageModelV4({
    doStream: async () => {
      calls.fallback++;
      return { stream: convertArrayToReadableStream(say("gemini")) };
    },
  });
  const model = failover.wrap(primary, fallback);
  const ask = async () => {
    const r = streamText({
      model,
      prompt: "hola",
      maxRetries: 0,
      onError: () => {},
    });
    let text = "";
    for await (const part of r.textStream) text += part;
    return text;
  };
  return {
    ask,
    calls,
    events,
    failover,
    state,
    advance: (ms: number) => void (t += ms),
  };
}

describe("agent provider failover", () => {
  test("a 502 from ia-tic is answered by the fallback", async () => {
    const s = setup(apiError(502));
    expect(await s.ask()).toBe("gemini");
    expect(s.calls).toEqual({ primary: 1, fallback: 1 });
    expect(s.events).toEqual(["agent.provider_failover"]);
    expect(s.failover.active()).toBe("fallback");
  });

  test("a 429 and a network error fail over too", async () => {
    for (const err of [
      apiError(429),
      apiError(undefined),
      new TypeError("Unable to connect"),
    ]) {
      const s = setup(err);
      expect(await s.ask()).toBe("gemini");
    }
  });

  test("a 400 is our request's fault and does not fail over", async () => {
    const s = setup(apiError(400));
    expect(await s.ask()).toBe("");
    expect(s.calls).toEqual({ primary: 1, fallback: 0 });
    expect(s.failover.active()).toBe("primary");
  });

  test("a stream that broke after output started is not sent again", async () => {
    const failover = createFailover({ stickyMs: 300_000, log: () => {} });
    let fallbackCalls = 0;
    const primary = new MockLanguageModelV4({
      doStream: async () => ({
        stream: convertArrayToReadableStream([
          { type: "text-start" as const, id: "t1" },
          { type: "text-delta" as const, id: "t1", delta: "ho" },
          { type: "error" as const, error: apiError(502) },
        ]),
      }),
    });
    const fallback = new MockLanguageModelV4({
      doStream: async () => {
        fallbackCalls++;
        return { stream: convertArrayToReadableStream(say("gemini")) };
      },
    });
    const r = streamText({
      model: failover.wrap(primary, fallback),
      prompt: "hola",
      maxRetries: 0,
      onError: () => {},
    });
    let text = "";
    for await (const part of r.textStream) text += part;
    expect(text).toBe("ho");
    expect(fallbackCalls).toBe(0);
    expect(failover.active()).toBe("primary");
  });

  test("an aborted turn (PARAR) never fails over", async () => {
    const abort = new AbortController();
    const failover = createFailover({ stickyMs: 300_000, log: () => {} });
    let fallbackCalls = 0;
    const primary = new MockLanguageModelV4({
      doStream: async () => {
        abort.abort();
        throw Object.assign(new Error("aborted"), { name: "AbortError" });
      },
    });
    const fallback = new MockLanguageModelV4({
      doStream: async () => {
        fallbackCalls++;
        return { stream: convertArrayToReadableStream(say("gemini")) };
      },
    });
    const r = streamText({
      model: failover.wrap(primary, fallback),
      prompt: "hola",
      maxRetries: 0,
      abortSignal: abort.signal,
      onError: () => {},
    });
    for await (const _ of r.textStream) void _;
    expect(fallbackCalls).toBe(0);
  });

  test("after a failover, calls stay on the fallback for the sticky window", async () => {
    const s = setup(apiError(502), { stickyMs: 300_000 });
    await s.ask();
    s.state.err = null; // ia-tic is back, but the window has not passed
    s.advance(299_000);
    expect(await s.ask()).toBe("gemini");
    expect(s.calls).toEqual({ primary: 1, fallback: 2 });
  });

  test("after the window the primary is tried again and restored", async () => {
    const s = setup(apiError(502), { stickyMs: 300_000 });
    await s.ask();
    s.state.err = null;
    s.advance(300_001);
    expect(s.failover.active()).toBe("primary");
    expect(await s.ask()).toBe("tic");
    expect(s.events).toEqual([
      "agent.provider_failover",
      "agent.provider_restored",
    ]);
  });

  test("a primary that sends no headers in time counts as down", async () => {
    const failover = createFailover({
      stickyMs: 1000,
      headerTimeoutMs: 20,
      log: () => {},
    });
    const primary = new MockLanguageModelV4({
      doStream: ({ abortSignal }) =>
        new Promise((_, reject) =>
          abortSignal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          ),
        ),
    });
    const fallback = new MockLanguageModelV4({
      doStream: async () => ({
        stream: convertArrayToReadableStream(say("gemini")),
      }),
    });
    const r = streamText({
      model: failover.wrap(primary, fallback),
      prompt: "hola",
      maxRetries: 0,
    });
    let text = "";
    for await (const part of r.textStream) text += part;
    expect(text).toBe("gemini");
  });
});
