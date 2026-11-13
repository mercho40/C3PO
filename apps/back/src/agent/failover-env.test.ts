import { expect, test } from "bun:test";

// runtime.ts reads the fallback env at module load, so each case runs in its
// own process.
const RUNTIME = `${import.meta.dir}/runtime.ts`;
const PROBE = `const r = await import(${JSON.stringify(RUNTIME)});
const m = r.chatModel("tic-chat");
console.log(JSON.stringify({ wrapped: m.constructor.name !== "OpenAICompatibleChatLanguageModel", active: r.agentProvider() }));
process.exit(0);`;
const FALLBACK = [
  "AGENT_FALLBACK_BASE_URL",
  "AGENT_FALLBACK_MODEL",
  "AGENT_FALLBACK_API_KEY",
];

function probe(env: Record<string, string>) {
  const base = { ...process.env };
  for (const k of FALLBACK) delete base[k];
  const out = Bun.spawnSync(["bun", "-e", PROBE], {
    cwd: `${import.meta.dir}/../..`, // apps/back, for its .env
    env: { ...base, ...env },
  });
  const lines = out.stdout.toString().trim().split("\n");
  return {
    result: JSON.parse(lines[lines.length - 1]!) as {
      wrapped: boolean;
      active: string;
    },
    stderr: out.stderr.toString(),
  };
}

test("with no fallback env the agent's model is the plain primary, as before", () => {
  expect(probe({}).result).toEqual({ wrapped: false, active: "primary" });
});

test("with all three set the model fails over", () => {
  const { result } = probe({
    AGENT_FALLBACK_BASE_URL: "http://127.0.0.1:9/v1",
    AGENT_FALLBACK_MODEL: "gemini-3.5-flash",
    AGENT_FALLBACK_API_KEY: "k",
  });
  expect(result).toEqual({ wrapped: true, active: "primary" });
});

test("a half-set fallback is off and says so", () => {
  const { result, stderr } = probe({
    AGENT_FALLBACK_MODEL: "gemini-3.5-flash",
  });
  expect(result.wrapped).toBe(false);
  expect(stderr).toContain("failover is off");
});
