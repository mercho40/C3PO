/**
 * Does the Realtime model call a tool for a plain request? Sends the SAME
 * session.update the voice engine builds (instructions, tools, tool_choice),
 * then one TEXT user turn per phrase, and prints what the model did.
 *
 *   bun apps/back/scripts/probe-realtime.ts ["saludá a la gente" ...]
 *   TOOLS_JSON=all-tools.json bun apps/back/scripts/probe-realtime.ts
 *
 * The catalogue is a fixture (probe-realtime.tools.json: walk_to, wave, say,
 * go_to_object as the bridge's tools/list returns them), or TOOLS_JSON, same
 * shape. AGENT_TOOLS filters it as on the robot. No bridge is ever called: a
 * function call is printed, never executed. Needs OPENAI_API_KEY in the
 * environment (OPENAI_REALTIME_MODEL optional).
 */

import WebSocket from "ws";

// db and auth modules load transitively; the probe never touches them.
for (const k of [
  "DATABASE_URL",
  "WEB_URL",
  "BETTER_AUTH_URL",
  "BETTER_AUTH_SECRET",
])
  process.env[k] ??= "http://unused.invalid";
const { realtimeSessionUpdate } = await import("../src/voice/realtime");
const { buildSystemPrompt } = await import("../src/agent/runtime");
const { toEntry } = await import("../src/skills/catalogue");

const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey) {
  console.error("OPENAI_API_KEY is not set.");
  process.exit(1);
}
const tools = JSON.parse(
  await Bun.file(
    process.env.TOOLS_JSON ?? `${import.meta.dir}/probe-realtime.tools.json`,
  ).text(),
);
const skills = tools.map(toEntry);
const update = realtimeSessionUpdate(
  await buildSystemPrompt("voice", skills),
  skills,
);
const phrases = process.argv.slice(2);
if (!phrases.length) phrases.push("saludá a la gente");

console.log(`model ${update.session.model}`);
console.log(
  `declared tools: ${update.session.tools.map((t) => t.name).join(", ")}`,
);

type Item = {
  type: string;
  call_id?: string;
  name?: string;
  arguments?: string;
  content?: { transcript?: string; text?: string }[];
};
type Event = {
  type: string;
  error?: unknown;
  response?: { status?: string; output?: Item[] };
};

/** Stand-in results; anything not listed answers {"status":"ok"}. */
const CANNED: Record<string, unknown> = {
  get_state: {
    env: "real",
    posture: "standing",
    fsm: "walk",
    battery_pct: 80,
    faults: [],
    x_meters_world: 0,
    y_meters_world: 0,
    yaw_degrees: 0,
  },
};

/** One fresh session per phrase, as each stand session started fresh. */
async function probe(phrase: string): Promise<void> {
  const url = new URL("wss://api.openai.com/v1/realtime");
  url.searchParams.set("model", update.session.model);
  const socket = new WebSocket(url, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  const events: Event[] = [];
  let wake = () => {};
  socket.on("message", (raw) => {
    events.push(JSON.parse(raw.toString()));
    wake();
  });
  const next = async (types: string[]): Promise<Event> => {
    for (;;) {
      const i = events.findIndex((e) => types.includes(e.type));
      if (i >= 0) return events.splice(0, i + 1)[i]!;
      await new Promise<void>((r) => (wake = r));
    }
  };
  await new Promise((resolve, reject) => {
    socket.once("open", resolve);
    socket.once("error", reject);
  });
  console.log(`\n> ${phrase}`);
  try {
    socket.send(JSON.stringify(update));
    const ack = await next(["session.updated", "error"]);
    if (ack.type === "error")
      return console.log(
        `  session.update rejected: ${JSON.stringify(ack.error)}`,
      );
    socket.send(
      JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [{ type: "input_text", text: phrase }],
        },
      }),
    );
    // Up to 4 responses: each call gets a canned output (never the bridge),
    // so a get_state first step shows what the model does next.
    for (let round = 0; round < 4; round++) {
      socket.send(JSON.stringify({ type: "response.create" }));
      const done = await next(["response.done", "error"]);
      if (done.type === "error")
        return console.log(`  error: ${JSON.stringify(done.error)}`);
      const output = done.response?.output ?? [];
      console.log(`  status: ${done.response?.status}`);
      for (const o of output)
        console.log(
          o.type === "function_call"
            ? `  WOULD CALL ${o.name}(${o.arguments})  [not sent]`
            : `  ${o.type}: ${(o.content ?? []).map((c) => c.transcript ?? c.text).join(" ")}`,
        );
      const calls = output.filter((o) => o.type === "function_call");
      if (!calls.length) return console.log("  (no function_call)");
      for (const c of calls)
        socket.send(
          JSON.stringify({
            type: "conversation.item.create",
            item: {
              type: "function_call_output",
              call_id: c.call_id,
              output: JSON.stringify(CANNED[c.name!] ?? { status: "ok" }),
            },
          }),
        );
    }
  } finally {
    socket.close();
  }
}

// A session that never answers must not hang the terminal.
setTimeout(() => {
  console.error("no answer within 60 s per phrase");
  process.exit(1);
}, 60_000 * phrases.length);
for (const phrase of phrases) await probe(phrase);
process.exit(0);
