/**
 * The headset's push-to-talk run for real: the rune module compiled by Svelte
 * (as in vr-control-runtime.test.ts), with a fake AudioContext, worklet node
 * and Chat. What reaches `fetch` is what gets uploaded; what reaches
 * `sendMessage` is what the agent hears.
 *
 * Pinned here: silence is trimmed and a silent hold uploads nothing, a reply
 * that never comes releases the panel after 60 s, PARAR releases it at once,
 * and in both cases the late reply is dropped.
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
// Vite's `?url` import; the worklet itself needs AudioWorkletGlobalScope.
mock.module("../src/lib/robot/mic-capture.worklet.js?url&no-inline", () => ({
  default: "/mic-capture.worklet.js",
}));

class FakeChat {
  static last: FakeChat | null = null;
  id: string;
  status = "ready";
  messages: unknown[] = [];
  sent: string[] = [];
  constructor(opts: { id: string }) {
    this.id = opts.id;
    FakeChat.last = this;
  }
  async sendMessage(m: { text: string }) {
    this.sent.push(m.text);
  }
  async stop() {}
}
mock.module("@ai-sdk/svelte", () => ({ Chat: FakeChat }));

class FakeNode {
  static last: FakeNode | null = null;
  port = {
    onmessage: null as ((e: { data: Float32Array }) => void) | null,
    postMessage() {},
  };
  onprocessorerror: (() => void) | null = null;
  constructor() {
    FakeNode.last = this;
  }
  connect() {}
  disconnect() {}
}
class FakeContext {
  state = "running";
  sampleRate = 48_000;
  destination = {};
  audioWorklet = { addModule: async () => {} };
  createMediaStreamSource() {
    return { connect() {} };
  }
  async resume() {}
  async close() {}
}

const RATE = 48_000;
/** `s` seconds at 48 kHz: silence, with `loud` seconds of tone from `at`. */
function recording(s: number, at = 0, loud = 0): Float32Array {
  const out = new Float32Array(Math.round(s * RATE));
  for (let i = Math.round(at * RATE); i < Math.round((at + loud) * RATE); i++)
    out[i] = 0.3 * Math.sin(i / 10);
  return out;
}

type Upload = {
  url: string;
  bytes: number;
  answer: (body: unknown) => void;
};

async function harness() {
  // Imported before any global is replaced, so a failed import leaks nothing.
  const { VrVoice } = await import("../src/lib/robot/vr-voice.svelte");
  const { estop } = await import("../src/lib/robot/estop.svelte");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  const saved = {
    fetch: g.fetch,
    setTimeout: g.setTimeout,
    clearTimeout: g.clearTimeout,
    AudioContext: g.AudioContext,
    AudioWorkletNode: g.AudioWorkletNode,
    mediaDevices: Object.getOwnPropertyDescriptor(g.navigator, "mediaDevices"),
  };
  const uploads: Upload[] = [];
  g.fetch = (input: unknown, init?: RequestInit) =>
    new Promise<Response>((resolve) => {
      uploads.push({
        url: String(input),
        bytes: (init?.body as Blob | undefined)?.size ?? 0,
        answer: (body) => resolve(Response.json(body)),
      });
    });
  // The 60 s give-up, fired by the test instead of by the clock.
  const longTimers = new Map<object, () => void>();
  g.setTimeout = (fn: () => void, ms?: number, ...rest: unknown[]) => {
    if ((ms ?? 0) < 60_000) return saved.setTimeout(fn, ms, ...rest);
    const id = {};
    longTimers.set(id, fn);
    return id;
  };
  g.clearTimeout = (id: unknown) =>
    longTimers.delete(id as object) || saved.clearTimeout(id);
  g.AudioContext = FakeContext;
  g.AudioWorkletNode = FakeNode;
  Object.defineProperty(g.navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: async () => ({
        getAudioTracks: () => [{}],
        getTracks: () => [{ stop() {} }],
      }),
    },
  });

  const latch = { standing: false, error: null, resume: async () => {} };
  const voice = new VrVoice(latch as never);
  await voice.enableMic();
  expect(voice.micReady).toBe(true);
  const chat = FakeChat.last!;

  return {
    voice,
    chat,
    estop,
    uploads,
    /** Hold B/Y over these samples, then let go. */
    talk(samples: Float32Array) {
      voice.onTalk(true);
      FakeNode.last!.port.onmessage!({ data: samples });
      voice.onTalk(false);
    },
    /** As if 60 s passed with no answer. */
    giveUp() {
      const due = [...longTimers.values()];
      longTimers.clear();
      for (const fn of due) fn();
    },
    restore() {
      voice.destroy();
      estop.phase = "idle";
      g.fetch = saved.fetch;
      g.setTimeout = saved.setTimeout;
      g.clearTimeout = saved.clearTimeout;
      g.AudioContext = saved.AudioContext;
      g.AudioWorkletNode = saved.AudioWorkletNode;
      if (saved.mediaDevices)
        Object.defineProperty(g.navigator, "mediaDevices", saved.mediaDevices);
      else delete g.navigator.mediaDevices;
    },
  };
}

/** A refused T2 call on screen, so the wait reads «si dijiste «confirmo»…». */
const refusedCall = [
  { id: "u", role: "user", parts: [{ type: "text", text: "Extendé la mano" }] },
  {
    id: "a",
    role: "assistant",
    parts: [
      {
        type: "tool-reach",
        toolCallId: "call_1",
        state: "output-available",
        input: { side: "right" },
        output: {
          error:
            "tool_error: refused: reach is a T2 tool and needs an operator's confirmation first.",
        },
      },
    ],
  },
];

describe("silence around the utterance", () => {
  test("a hold with nothing said uploads nothing", async () => {
    const h = await harness();
    try {
      h.talk(recording(1));
      await Bun.sleep(0);
      expect(h.uploads).toEqual([]);
      expect(h.voice.phase).toBe("idle");
      expect(h.voice.error).toContain("No se oyó nada");
    } finally {
      h.restore();
    }
  });

  test("only the speech, with a little margin, is uploaded", async () => {
    const h = await harness();
    try {
      h.talk(recording(3, 1, 0.5)); // 1 s of nothing, 0.5 s said, 1.5 s of nothing
      expect(h.uploads.length).toBe(1);
      const seconds = (h.uploads[0]!.bytes - 44) / 2 / 16_000;
      expect(seconds).toBeGreaterThan(0.5);
      expect(seconds).toBeLessThan(1);
    } finally {
      h.restore();
    }
  });
});

describe("a reply that does not come", () => {
  test("after 60 s the panel is free again, and the late reply is dropped", async () => {
    const h = await harness();
    try {
      h.talk(recording(1, 0, 1));
      expect(h.voice.phase).toBe("transcribing");
      h.giveUp();
      expect(h.voice.phase).toBe("idle");
      expect(h.voice.error).toContain("60 s");
      h.uploads[0]!.answer({ text: "andá hasta la silla" });
      await Bun.sleep(0);
      expect(h.chat.sent).toEqual([]);
      expect(h.voice.phase).toBe("idle");
      h.voice.onTalk(true); // a new utterance is accepted
      expect(h.voice.phase).toBe("listening");
    } finally {
      h.restore();
    }
  });

  test("PARAR frees it at once, and a «confirmo» answered late is not relayed", async () => {
    const h = await harness();
    try {
      h.chat.messages = refusedCall;
      h.talk(recording(1, 0, 1));
      expect(h.voice.phase).toBe("deciding");
      expect(h.uploads[0]!.url).toContain("pending=call_1");
      h.estop.phase = "sending";
      await Bun.sleep(0);
      expect(h.voice.phase).toBe("idle");
      // The give-up of a request PARAR already ended changes nothing.
      h.giveUp();
      expect(h.voice.error).toBeNull();
      h.uploads[0]!.answer({
        text: "confirmo",
        confirmation: {
          action: "confirmed",
          tool: "reach",
          message: "El operador confirmó y se ejecutó reach.",
        },
      });
      await Bun.sleep(0);
      expect(h.chat.sent).toEqual([]);
      expect(h.voice.phase).toBe("idle");
    } finally {
      h.restore();
    }
  });

  test("an answer in time still reaches the agent, and each stage is timed", async () => {
    const h = await harness();
    const logged: string[] = [];
    const info = console.info;
    console.info = (line: unknown) => void logged.push(String(line));
    try {
      h.talk(recording(1, 0, 1));
      h.uploads[0]!.answer({ text: "andá hasta la silla" });
      await Bun.sleep(0);
      expect(h.chat.sent).toEqual(["andá hasta la silla"]);
      await Bun.sleep(0);
      const all = logged.join("\n");
      expect(/record \d+ ms \(1\.\d s sent\)/.test(all)).toBe(true);
      expect(/upload\+transcribe \d+ ms/.test(all)).toBe(true);
      expect(/agent \d+ ms/.test(all)).toBe(true);
      h.giveUp(); // answered: the give-up was cleared
      expect(h.voice.error).toBeNull();
    } finally {
      console.info = info;
      h.restore();
    }
  });
});
