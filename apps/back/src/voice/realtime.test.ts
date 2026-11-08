import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import * as chats from "#back/db/chats.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  RealtimeVoiceSession,
  isRecoverableRealtimeError,
  realtimeSessionUpdate,
  realtimeTools,
} from "./realtime";

const skill = (name: string, dangerLevel: "low" | "medium" | "high") =>
  ({
    name,
    dangerLevel,
    description: `${name} description`,
    parameters: { type: "object", properties: {} },
  }) as never;

describe("OpenAI Realtime session contract", () => {
  test("keeps audio ownership and high-danger actions outside model tools", () => {
    const tools = realtimeTools([
      skill("listen", "low"),
      skill("say", "low"),
      skill("walk_to", "medium"),
      skill("dangerous_posture", "high"),
    ]);
    // `look` and `verify` are back's own tools, appended after the bridge's.
    expect(tools.map((tool) => tool.name)).toEqual([
      "walk_to",
      "look",
      "verify",
    ]);
  });

  test("the bridge's own look and verify are not offered next to back's: one name, one tool", () => {
    const tools = realtimeTools([
      skill("look", "low"),
      skill("verify", "low"),
      skill("walk_to", "medium"),
    ]);
    expect(tools.map((tool) => tool.name)).toEqual([
      "walk_to",
      "look",
      "verify",
    ]);
    for (const t of tools.slice(1))
      expect(t.description).not.toBe(`${t.name} description`);
  });

  test("refuses to start without putting a missing API key on another machine", async () => {
    const previous = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    try {
      await expect(
        new RealtimeVoiceSession().start({
          chatId: "chat",
          ownerId: "operator",
        }),
      ).rejects.toThrow("OPENAI_API_KEY");
    } finally {
      if (previous === undefined) delete process.env.OPENAI_API_KEY;
      else process.env.OPENAI_API_KEY = previous;
    }
  });

  test("uses PCM, Spanish transcription, semantic VAD and interruption", () => {
    const event = realtimeSessionUpdate("instructions", [skill("wave", "low")]);
    expect(event.type).toBe("session.update");
    expect(event.session.output_modalities).toEqual(["audio"]);
    expect(event.session.audio.input.format).toEqual({
      type: "audio/pcm",
      rate: 24_000,
    });
    expect(event.session.audio.input.transcription.language).toBe("es");
    expect(event.session.audio.input.turn_detection).toMatchObject({
      type: "semantic_vad",
      create_response: true,
      interrupt_response: true,
    });
    expect(event.session.tools[0]?.name).toBe("wave");
  });
});

describe("a function call the session never declared", () => {
  // Structural: `executeTool` is private and needs a live OpenAI socket. What
  // is pinned is that the declared set is recorded where the session is
  // configured, and checked before anything reaches the bridge.
  const src = readFileSync(
    fileURLToPath(new URL("./realtime.ts", import.meta.url)),
    "utf8",
  );

  test("the declared names are recorded from the same list sent to OpenAI", () => {
    expect(src).toContain(
      "this.declaredTools = new Set(realtimeTools(skills).map((t) => t.name));",
    );
  });

  test("an undeclared name is refused before callTool", () => {
    const body = src.slice(src.indexOf("private async executeTool"));
    const guard = body.indexOf("if (!this.declaredTools.has(name)) {");
    expect(guard).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(body.indexOf("await callTool(name, args)"));
  });
});

describe("an operator's decision on a call this session asked for", () => {
  // No database in tests: record what would be written instead. Restored after
  // each test: bun shares modules across files, and a spy left on db/chats.js
  // would swallow every other file's writes.
  const written: { chatId: string; role: string; parts: unknown[] }[] = [];
  let append: ReturnType<typeof spyOn> | null = null;
  beforeEach(() => {
    append = spyOn(chats, "appendMessage").mockImplementation(async (m) => {
      written.push(m);
      return "msg";
    });
  });
  afterEach(() => {
    append?.mockRestore();
    written.length = 0;
  });
  // The socket and state are private and need a live OpenAI connection; a fake
  // open socket stands in for it.
  function session(running: boolean) {
    const sent: unknown[] = [];
    const s = new RealtimeVoiceSession();
    const inner = s as unknown as {
      socket: unknown;
      state: { running: boolean; chatId: string | null };
    };
    inner.socket = {
      readyState: 1, // ws OPEN
      send: (raw: string) => sent.push(JSON.parse(raw)),
    };
    inner.state.running = running;
    inner.state.chatId = "chat_rt";
    return { s, sent };
  }

  test("its own chat's outcome reaches the model, which is asked to answer", () => {
    const { s, sent } = session(true);
    s.informOutcome("chat_rt", "El operador confirmó y se ejecutó pick.");
    expect(sent).toEqual([
      {
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "system",
          content: [
            {
              type: "input_text",
              text: "El operador confirmó y se ejecutó pick.",
            },
          ],
        },
      },
      { type: "response.create" },
    ]);
  });

  test("the outcome is written into that voice chat's transcript", async () => {
    const { s } = session(true);
    s.informOutcome("chat_rt", "El operador canceló pick: no se ejecutó.");
    await (s as unknown as { eventTail: Promise<void> }).eventTail;
    expect(written).toEqual([
      {
        id: expect.any(String),
        chatId: "chat_rt",
        role: "system",
        parts: [
          { type: "text", text: "El operador canceló pick: no se ejecutó." },
        ],
      } as never,
    ]);
    const other = session(true);
    other.s.informOutcome("chat_other", "x");
    await (other.s as unknown as { eventTail: Promise<void> }).eventTail;
    expect(written).toHaveLength(1);
  });

  test("another chat, or a stopped session, hears nothing", () => {
    const other = session(true);
    other.s.informOutcome("chat_other", "x");
    expect(other.sent).toEqual([]);
    const stopped = session(false);
    stopped.s.informOutcome("chat_rt", "x");
    expect(stopped.sent).toEqual([]);
  });
});

describe("a Realtime error event", () => {
  // fail() asks the bridge to drop queued audio; no bridge here.
  let fetchSpy: ReturnType<typeof spyOn> | null = null;
  let warnSpy: ReturnType<typeof spyOn> | null = null;
  let errorSpy: ReturnType<typeof spyOn> | null = null;
  beforeEach(() => {
    fetchSpy = spyOn(globalThis, "fetch").mockImplementation(
      (async () => new Response(null, { status: 204 })) as never,
    );
    warnSpy = spyOn(console, "warn").mockImplementation(() => {});
    errorSpy = spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    fetchSpy?.mockRestore();
    warnSpy?.mockRestore();
    errorSpy?.mockRestore();
  });

  function running() {
    const s = new RealtimeVoiceSession();
    const inner = s as unknown as {
      socket: unknown;
      state: { running: boolean; phase: string };
      receive: (raw: Buffer) => void;
    };
    let closed = false;
    inner.socket = {
      readyState: 1,
      send: () => {},
      close: () => {
        closed = true;
      },
    };
    inner.state.running = true;
    inner.state.phase = "listening";
    const receive = (error: {
      type?: string;
      code?: string;
      message?: string;
    }) => inner.receive(Buffer.from(JSON.stringify({ type: "error", error })));
    return { s, receive, closed: () => closed };
  }

  test("a collision with an active response leaves the session listening", () => {
    const { s, receive, closed } = running();
    receive({
      type: "invalid_request_error",
      code: "conversation_already_has_active_response",
      message: "Conversation already has an active response",
    });
    expect(s.snapshot()).toMatchObject({
      running: true,
      phase: "listening",
      lastError: null,
    });
    expect(closed()).toBe(false);
    expect(warnSpy).toHaveBeenCalled();
  });

  test("an unknown or code-less error still ends the session", () => {
    for (const error of [
      { type: "invalid_request_error", code: "session_expired", message: "x" },
      { type: "server_error", message: "The server had an error" },
    ]) {
      const { s, receive, closed } = running();
      receive(error);
      expect(s.snapshot()).toMatchObject({
        running: false,
        phase: "error",
        lastError: error.message,
      });
      expect(closed()).toBe(true);
    }
  });

  test("only named codes are recoverable", () => {
    for (const code of [
      "conversation_already_has_active_response",
      "response_cancel_not_active",
      "input_audio_buffer_commit_empty",
      "rate_limit_exceeded",
    ])
      expect(isRecoverableRealtimeError({ code })).toBe(true);
    for (const error of [
      undefined,
      {},
      { type: "invalid_request_error" },
      { code: "invalid_api_key" },
      { code: "session_expired" },
    ])
      expect(isRecoverableRealtimeError(error)).toBe(false);
  });
});
