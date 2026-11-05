/**
 * Does the agent's model actually SEE an image? The `look` tool depends on it,
 * and "multimodal" on a gateway's model list is a claim, not a test.
 *
 *   bun apps/back/scripts/smoke-vision.ts [frame.jpg]
 *
 * With no argument it fetches the robot's newest frame from the bridge
 * (BRIDGE_URL's host, `/camera/frame.jpg` — open the tunnel first). The check
 * that matters is reading the reply against the picture yourself: a model that
 * ignores image parts still answers fluently, it just describes nothing that is
 * there. Sends through the installed AI SDK, not curl, because what the SDK
 * serialises is what the agent will send.
 *
 * Reads apps/back/.env itself (relative to this file) so it runs from anywhere.
 */

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { generateText } from "ai";

const envFile = Bun.file(`${import.meta.dir}/../.env`);
if (await envFile.exists()) {
  for (const line of (await envFile.text()).split("\n")) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m && !process.env[m[1]!]) process.env[m[1]!] = m[2]!;
  }
}

const BASE_URL =
  process.env.AGENT_BASE_URL ?? "https://ia-tic.ort.edu.ar/api/v1";
const MODEL = process.env.VISION_MODEL || process.env.AGENT_MODEL || "tic-chat";
const API_KEY = process.env.AGENT_API_KEY;
if (!API_KEY) {
  console.error("AGENT_API_KEY is empty — set it in apps/back/.env.");
  process.exit(1);
}

let image: Uint8Array;
const path = process.argv[2];
if (path) {
  image = new Uint8Array(await Bun.file(path).arrayBuffer());
} else {
  const bridge = new URL(process.env.BRIDGE_URL ?? "http://127.0.0.1:8001/mcp");
  bridge.pathname = "/camera/frame.jpg";
  const res = await fetch(bridge);
  if (!res.ok) {
    console.error(`no frame from ${bridge} (HTTP ${res.status})`);
    process.exit(1);
  }
  image = new Uint8Array(await res.arrayBuffer());
}
console.log(
  `base   ${BASE_URL}\nmodel  ${MODEL}\nimage  ${image.byteLength} bytes\n`,
);

const provider = createOpenAICompatible({
  name: "vision-smoke",
  baseURL: BASE_URL,
  apiKey: API_KEY,
});

const started = performance.now();
const { text, usage } = await generateText({
  model: provider.chatModel(MODEL),
  messages: [
    {
      role: "user",
      content: [
        {
          type: "text",
          text: "Describí en dos frases qué hay en esta imagen. Nombrá cada objeto que veas.",
        },
        { type: "file", data: image, mediaType: "image/jpeg" },
      ],
    },
  ],
});
console.log(text);
console.log(
  `\n${Math.round(performance.now() - started)} ms · input tokens ${usage.inputTokens ?? "?"}`,
);
