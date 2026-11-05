import { describe, expect, test } from "bun:test";
import { availableEngines, chooseEngine, defaultEngine } from "./engine";

describe("defaultEngine", () => {
  test("legacy unless VOICE_ENGINE=realtime", () => {
    expect(defaultEngine({})).toBe("legacy");
    expect(defaultEngine({ VOICE_ENGINE: "legacy" })).toBe("legacy");
    expect(defaultEngine({ VOICE_ENGINE: "realtime" })).toBe("openai-realtime");
  });
});

describe("availableEngines", () => {
  test("realtime is offered only with a key", () => {
    expect(availableEngines({})).toEqual(["legacy"]);
    // An empty `OPENAI_API_KEY=` line, as .env.example ships, is no key.
    expect(availableEngines({ OPENAI_API_KEY: "" })).toEqual(["legacy"]);
    expect(availableEngines({ OPENAI_API_KEY: "sk-x" })).toEqual([
      "legacy",
      "openai-realtime",
    ]);
  });
});

describe("chooseEngine", () => {
  const both = ["legacy", "openai-realtime"] as const;

  test("an omitted engine falls back to the default", () => {
    expect(
      chooseEngine({
        requested: undefined,
        running: null,
        fallback: "openai-realtime",
        available: both,
      }),
    ).toEqual({ ok: true, engine: "openai-realtime" });
  });

  test("the operator's choice overrides the default", () => {
    expect(
      chooseEngine({
        requested: "legacy",
        running: null,
        fallback: "openai-realtime",
        available: both,
      }),
    ).toEqual({ ok: true, engine: "legacy" });
  });

  test("a different engine than the running one is refused", () => {
    expect(
      chooseEngine({
        requested: "openai-realtime",
        running: "legacy",
        fallback: "legacy",
        available: both,
      }),
    ).toEqual({ ok: false, status: 409, error: "other_engine_running" });
  });

  test("the running engine again is allowed (start is idempotent)", () => {
    expect(
      chooseEngine({
        requested: "legacy",
        running: "legacy",
        fallback: "legacy",
        available: both,
      }),
    ).toEqual({ ok: true, engine: "legacy" });
  });

  test("realtime without a key is refused before anything starts", () => {
    expect(
      chooseEngine({
        requested: "openai-realtime",
        running: null,
        fallback: "legacy",
        available: ["legacy"],
      }),
    ).toEqual({ ok: false, status: 503, error: "engine_unavailable" });
  });
});
