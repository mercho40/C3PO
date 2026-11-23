/**
 * The one transport to POST /agent, shared by /chat and the headset's voice.
 *
 * Run for real through the AI SDK's `sendMessages`, with `fetch` faked: what
 * reaches `fetch` is what reaches back. Pinned here: the URL and the cookie,
 * that only the headset's turns say `mode: "voice"` (which is what makes back
 * apply the voice exclusions), and that every request reports `null` and then
 * its status — /chat's 409-vs-502 advice reads it — before a 401 sends the
 * operator to log in.
 */

import { describe, expect, mock, test } from "bun:test";
import { plugin } from "bun";
import { readFileSync } from "node:fs";
import { compileModule } from "svelte/compiler";

plugin({
  name: "svelte-runes",
  setup(build) {
    build.onLoad({ filter: /\.svelte\.ts$/ }, ({ path }) => {
      const js = new Bun.Transpiler({ loader: "ts" }).transformSync(
        readFileSync(path, "utf8"),
      );
      const out = compileModule(js, { filename: path, generate: "client" });
      return { contents: out.js.code, loader: "js" };
    });
  },
});
mock.module("$app/env", () => ({ browser: true, dev: false }));
// Same keys and values as auth-params.test.ts: bun keeps the first mock's export names.
mock.module("$app/env/public", () => ({
  PUBLIC_API_URL: "http://localhost:3000",
  PUBLIC_ROBOT_CAM_URL: "",
  PUBLIC_TELEOP_HOST: "",
  PUBLIC_TELEOP_PORT: "",
}));
mock.module("$app/navigation", () => ({ goto: async () => {} }));

type Sent = { url: string; init: RequestInit };

async function send(
  opts: { mode?: "voice" },
  status: number,
): Promise<{ sent: Sent[]; statuses: (number | null)[] }> {
  const { agentTransport } = await import("../src/lib/chat/agent-transport");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  const saved = { fetch: g.fetch, window: g.window };
  const sent: Sent[] = [];
  const statuses: (number | null)[] = [];
  g.window = { location: { pathname: "/chat", search: "" } };
  g.fetch = async (url: unknown, init: RequestInit) => {
    sent.push({ url: String(url), init });
    return new Response("{}", { status });
  };
  try {
    const transport = agentTransport({
      ...opts,
      onStatus: (s) => statuses.push(s),
    });
    await transport
      .sendMessages({
        chatId: "chat-1",
        messages: [],
        abortSignal: undefined,
        trigger: "submit-message",
        messageId: undefined,
      } as never)
      .catch(() => {}); // a non-2xx rejects in the SDK; only the request matters here
  } finally {
    Object.assign(g, saved);
  }
  return { sent, statuses };
}

describe("agentTransport", () => {
  test("posts to back's /agent with the session cookie", async () => {
    const { sent } = await send({}, 200);
    expect(sent.length).toBe(1);
    expect(sent[0]!.url).toBe("http://localhost:3000/agent");
    expect(sent[0]!.init.credentials).toBe("include");
  });

  test("a typed turn says no mode; a spoken one says voice", async () => {
    const typed = await send({}, 200);
    expect(JSON.parse(String(typed.sent[0]!.init.body)).mode).toBeUndefined();
    const spoken = await send({ mode: "voice" }, 200);
    expect(JSON.parse(String(spoken.sent[0]!.init.body)).mode).toBe("voice");
  });

  test("every request reports null, then its status", async () => {
    expect((await send({}, 502)).statuses).toEqual([null, 502]);
    expect((await send({}, 409)).statuses).toEqual([null, 409]);
    expect((await send({}, 401)).statuses).toEqual([null, 401]);
  });
});
