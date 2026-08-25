/**
 * Provider-level smoke test for the internal agent's LLM link. Answers the two
 * questions the Anthropic → TIC AI switch actually turns on, without needing
 * the bridge, the DB or a port:
 *
 *   1. Does AGENT_API_KEY authenticate against AGENT_BASE_URL, and is
 *      AGENT_MODEL one of the models the gateway publishes?
 *   2. Does that model emit a real tool call? The whole agent loop is tool
 *      calls — a model that only chats is useless here, and a local model is a
 *      much weaker bet on this than a frontier one.
 *
 *   bun apps/back/scripts/smoke-agent.ts
 *
 * Reads apps/back/.env itself (relative to this file) so it runs from anywhere.
 */

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { streamText, tool, stepCountIs, jsonSchema } from "ai";

// Bun only auto-loads .env from the cwd, and this is usually run from the repo
// root. Load explicitly, without clobbering anything already exported.
const envFile = Bun.file(`${import.meta.dir}/../.env`);
if (await envFile.exists()) {
  for (const line of (await envFile.text()).split("\n")) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m && !process.env[m[1]!]) process.env[m[1]!] = m[2]!;
  }
}

const BASE_URL = process.env.AGENT_BASE_URL ?? "http://ia.ort.edu.ar/api/v1";
const MODEL = process.env.AGENT_MODEL ?? "tic-chat";
const API_KEY = process.env.AGENT_API_KEY;

console.log(
  `base   ${BASE_URL}\nmodel  ${MODEL}\nkey    ${API_KEY ? `set (${API_KEY.length} chars)` : "MISSING"}\n`,
);

if (!API_KEY) {
  console.error(
    "AGENT_API_KEY is empty — set it in apps/back/.env and re-run.",
  );
  process.exit(1);
}

// 1. Catalogue.
const res = await fetch(`${BASE_URL}/models`, {
  headers: { Authorization: `Bearer ${API_KEY}` },
});
// Read the body ONCE as text and parse after: a failing response here is not
// necessarily JSON — when the gateway's upstream is down, nginx answers with an
// HTML 502, and `.json()` consumes the body before you can fall back to text.
const raw = await res.text();
let body: { data?: { id: string }[] } | null = null;
try {
  body = JSON.parse(raw);
} catch {
  /* not JSON — `raw` is the whole story */
}
console.log(`GET /models -> ${res.status}`);
if (!res.ok) {
  console.error(raw.slice(0, 400).trim());
  process.exit(1);
}
const ids: string[] = (body?.data ?? []).map((m: { id: string }) => m.id);
console.log(`  models: ${ids.join(", ") || "(none listed)"}`);
if (ids.length && !ids.includes(MODEL)) {
  console.warn(`  ⚠ AGENT_MODEL="${MODEL}" is not in that list`);
}

// 2. Tool calling — the capability the agent loop is built on.
const provider = createOpenAICompatible({
  name: "tic-ai",
  baseURL: BASE_URL,
  apiKey: API_KEY,
});

// An array, not a nullable: TS cannot narrow a `let` assigned inside a tool
// callback, and reads it back as `never`.
const calls: { name: string; args: unknown }[] = [];

const result = streamText({
  model: provider.chatModel(MODEL),
  system:
    "You are C3PO, controlling a humanoid robot. Use the provided tools to answer.",
  prompt: "How much battery does the robot have left? Check before answering.",
  tools: {
    // Deliberately shaped like a real skill: TypeBox emits exactly this at
    // runtime, and it goes through jsonSchema() untouched in runtime.ts.
    get_state: tool({
      description:
        "Read the robot's live state: pose, posture, battery percent, faults.",
      inputSchema: jsonSchema({
        type: "object",
        properties: {},
        additionalProperties: false,
      }),
      execute: async (args) => {
        calls.push({ name: "get_state", args });
        return { env: "stub", battery_percent: 61, posture: "standing" };
      },
    }),
  },
  stopWhen: stepCountIs(4),
});

process.stdout.write("\nstream: ");
for await (const chunk of result.textStream) process.stdout.write(chunk);
console.log("\n");

console.log(
  `tool calls: ${calls.length ? calls.map((c) => `${c.name}(${JSON.stringify(c.args)})`).join(", ") : "NONE — this model did not call the tool"}`,
);
console.log(`steps:     ${(await result.steps).length}`);
console.log(`usage:     ${JSON.stringify(await result.usage)}`);
console.log(`finish:    ${await result.finishReason}`);

if (!calls.length) {
  console.error(
    "\n✗ No tool call. The agent loop cannot drive the robot with this model.",
  );
  process.exit(1);
}
console.log("\n✓ auth, model and tool calling all work.");
