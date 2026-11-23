/**
 * The agent's models from `config.agent`: no fallback, a fallback, half a
 * fallback. In process — the env is read by `loadConfig`, once, into a value a
 * test builds itself (it used to be read at runtime.ts's import, so each case
 * ran in its own child process).
 */
import { expect, spyOn, test } from "bun:test";

import { testConfig } from "#back/testing/fakes.js";
import { createAgentModels } from "./models";

function models(env: Record<string, string> = {}) {
  const m = createAgentModels(testConfig(env).agent);
  return {
    wrapped:
      m.chatModel("tic-chat").constructor.name !==
      "OpenAICompatibleChatLanguageModel",
    active: m.activeProvider(),
  };
}

test("with no fallback env the agent's model is the plain primary, as before", () => {
  expect(models()).toEqual({ wrapped: false, active: "primary" });
});

test("with all three set the model fails over", () => {
  expect(
    models({
      AGENT_FALLBACK_BASE_URL: "http://127.0.0.1:9/v1",
      AGENT_FALLBACK_MODEL: "gemini-3.5-flash",
      AGENT_FALLBACK_API_KEY: "k",
    }),
  ).toEqual({ wrapped: true, active: "primary" });
});

test("a half-set fallback is off and says so", () => {
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  try {
    expect(models({ AGENT_FALLBACK_MODEL: "gemini-3.5-flash" }).wrapped).toBe(
      false,
    );
    expect(String(warn.mock.calls[0]?.[0])).toContain("failover is off");
  } finally {
    warn.mockRestore();
  }
});

test("no key: the turn names the variable to set, not a bare 401", () => {
  expect(createAgentModels(testConfig().agent).missingKey).toContain(
    "AGENT_API_KEY is not set",
  );
  expect(
    createAgentModels(testConfig({ AGENT_API_KEY: "k" }).agent).missingKey,
  ).toBeNull();
});
