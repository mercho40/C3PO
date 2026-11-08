import { describe, expect, test } from "bun:test";
import {
  VoiceConversation,
  headsetSpeaksReplies,
  shouldSpeak,
  speakReply,
} from "./conversation";

function textOf(message: {
  parts?: Array<{ type: string; text?: string }>;
}): string {
  return message.parts?.find((part) => part.type === "text")?.text ?? "";
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("VoiceConversation", () => {
  test("speaks exactly one answer for a non-streaming conversational turn", async () => {
    const spoken: string[] = [];
    const conversation = new VoiceConversation({
      complete: async () => "Hola, ¿cómo estás?",
      speak: async (text) => {
        spoken.push(text);
      },
      newId: (() => {
        let id = 0;
        return () => String(++id);
      })(),
    });

    const metrics = await conversation.turn("hola");

    expect(spoken).toEqual(["Hola, ¿cómo estás?"]);
    expect(metrics.speechCalls).toBe(1);
    expect(metrics.characters).toBe("Hola, ¿cómo estás?".length);
    expect(metrics.totalMs).toBeGreaterThanOrEqual(metrics.completionMs);
    expect(conversation.state()).toEqual({ phase: "idle", lastTurn: metrics });
  });

  test("speaks streamed Spanish sentences early and serializes speech", async () => {
    const spoken: string[] = [];
    const firstSpeechGate = deferred();
    const streamFinished = deferred();

    const conversation = new VoiceConversation({
      complete: async function* () {
        yield "El Dr.";
        expect(spoken).toEqual([]);

        yield " Pérez llegó. ";
        expect(spoken).toEqual(["El Dr. Pérez llegó."]);

        yield "¿Cómo estás? Todo ";
        yield "bien";
        streamFinished.resolve();
      },
      speak: async (text) => {
        spoken.push(text);
        if (spoken.length === 1) await firstSpeechGate.promise;
      },
      newId: () => crypto.randomUUID(),
    });

    const turn = conversation.turn("hola");
    await streamFinished.promise;

    // Later sentences are ready, but cannot overlap the first speech call.
    expect(spoken).toEqual(["El Dr. Pérez llegó."]);
    firstSpeechGate.resolve();

    const metrics = await turn;
    expect(spoken).toEqual([
      "El Dr. Pérez llegó.",
      "¿Cómo estás?",
      "Todo bien",
    ]);
    expect(metrics.speechCalls).toBe(3);
    expect(metrics.firstSpeechMs).not.toBeNull();
    expect(metrics.completionMs).toBeGreaterThanOrEqual(
      metrics.modelFirstDeltaMs ?? 0,
    );
    expect(textOf(conversation.history()[1]!)).toBe(
      "El Dr. Pérez llegó. ¿Cómo estás? Todo bien",
    );
  });

  test("passes earlier turns into follow-up context", async () => {
    const contexts: string[][] = [];
    const conversation = new VoiceConversation({
      complete: async (messages) => {
        contexts.push(messages.map(textOf));
        return contexts.length === 1
          ? "Me llamo C3PO."
          : "Sí, ese es mi nombre.";
      },
      speak: async () => {},
      newId: () => crypto.randomUUID(),
    });

    await conversation.turn("¿Cómo te llamás?");
    await conversation.turn("¿Podés repetirlo?");

    expect(contexts[1]).toEqual([
      "¿Cómo te llamás?",
      "Me llamo C3PO.",
      "¿Podés repetirlo?",
    ]);
  });

  test("keeps complete replies in bounded history", async () => {
    let turn = 0;
    const conversation = new VoiceConversation(
      {
        complete: async function* () {
          turn += 1;
          yield `Primera parte ${turn}. `;
          yield `Segunda parte ${turn}.`;
        },
        speak: async () => {},
        newId: () => crypto.randomUUID(),
      },
      2,
    );

    await conversation.turn("primera");
    await conversation.turn("segunda");

    expect(conversation.history().map(textOf)).toEqual([
      "segunda",
      "Primera parte 2. Segunda parte 2.",
    ]);
  });

  test("reset starts a fresh conversation", async () => {
    const sizes: number[] = [];
    const conversation = new VoiceConversation({
      complete: async (messages) => {
        sizes.push(messages.length);
        return "respuesta";
      },
      speak: async () => {},
      newId: () => crypto.randomUUID(),
    });

    await conversation.turn("primera");
    conversation.reset();
    await conversation.turn("nueva sesión");
    expect(sizes).toEqual([1, 1]);
  });

  test("an empty stream is not silently treated as a completed turn", async () => {
    let speaks = 0;
    const conversation = new VoiceConversation({
      complete: async function* () {},
      speak: async () => {
        speaks += 1;
      },
      newId: () => crypto.randomUUID(),
    });

    await expect(conversation.turn("hola")).rejects.toThrow("no spoken reply");
    expect(speaks).toBe(0);
    expect(conversation.history()).toEqual([]);
    expect(conversation.state()).toEqual({ phase: "idle", lastTurn: null });
  });

  test("an aborted turn speaks nothing more and is not remembered", async () => {
    const spoken: string[] = [];
    const abort = new AbortController();
    let received: AbortSignal | undefined;
    let n = 0;
    // streamText ends its text stream normally on abort, so the stream here
    // keeps going after the abort, as a not-yet-noticed one would.
    async function* aborted() {
      yield "Primera frase. ";
      abort.abort();
      yield "Segunda frase. ";
      yield "Tercera";
    }
    const conversation = new VoiceConversation({
      complete: async (_messages, signal) => {
        received = signal;
        return ++n === 1 ? "Hola." : aborted();
      },
      speak: async (text) => {
        spoken.push(text);
      },
      newId: () => crypto.randomUUID(),
    });

    await conversation.turn("hola");
    const before = conversation.history();

    await expect(
      conversation.turn("contame algo", abort.signal),
    ).rejects.toThrow();
    await new Promise((r) => setTimeout(r, 5));
    expect(received).toBe(abort.signal);
    expect(spoken).toEqual(["Hola.", "Primera frase."]);
    expect(conversation.history()).toEqual(before);
    expect(conversation.state().phase).toBe("idle");
  });
});

describe("speakReply (B8: the robot says the headset's reply)", () => {
  test("says the text parts, joined, and skips tool parts", async () => {
    const spoken: string[] = [];
    const n = await speakReply(
      [
        { type: "step-start" },
        { type: "text", text: " Voy a la puerta. " },
        { type: "tool-walk_to", input: { x: 1 } } as never,
        { type: "text", text: "Llegué." },
        { type: "reasoning", text: "no decir esto" },
      ],
      async (text) => void spoken.push(text),
    );
    expect(spoken).toEqual(["Voy a la puerta.\n\nLlegué."]);
    expect(n).toBe(1);
  });

  test("a reply over the say tool's 500 characters goes out in order, one chunk at a time", async () => {
    const words = Array.from({ length: 300 }, (_, i) => `p${i}`).join(" ");
    const events: string[] = [];
    let inFlight = 0;
    const chunks: string[] = [];
    await speakReply([{ type: "text", text: words }], async (text) => {
      inFlight += 1;
      expect(inFlight).toBe(1);
      events.push(`start ${chunks.length}`);
      await new Promise((r) => setTimeout(r, 1));
      chunks.push(text);
      events.push(`end ${chunks.length - 1}`);
      inFlight -= 1;
    });
    expect(chunks.length).toBeGreaterThan(1);
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(500);
    expect(chunks.join(" ")).toBe(words);
    expect(events.slice(0, 4)).toEqual([
      "start 0",
      "end 0",
      "start 1",
      "end 1",
    ]);
  });

  test("no text, no call", async () => {
    let calls = 0;
    const speak = async () => void (calls += 1);
    await speakReply([], speak);
    await speakReply([{ type: "text", text: "   " }], speak);
    await speakReply([{ type: "tool-look" } as never], speak);
    expect(calls).toBe(0);
  });

  test("a failing chunk rejects and the rest are not sent", async () => {
    const words = Array.from({ length: 300 }, (_, i) => `p${i}`).join(" ");
    let calls = 0;
    await expect(
      speakReply([{ type: "text", text: words }], async () => {
        calls += 1;
        throw new Error("spanish_tts_unavailable");
      }),
    ).rejects.toThrow("spanish_tts_unavailable");
    expect(calls).toBe(1);
  });

  test("PARAR after the first chunk: the second is never sent", async () => {
    const words = Array.from({ length: 300 }, (_, i) => `p${i}`).join(" ");
    const stop = new AbortController();
    const spoken: string[] = [];
    const n = await speakReply(
      [{ type: "text", text: words }],
      async (text) => {
        spoken.push(text);
        stop.abort(new Error("PARAR"));
      },
      stop.signal,
    );
    expect(spoken).toHaveLength(1);
    expect(n).toBe(1);
  });

  test("an already-aborted turn says nothing", async () => {
    let calls = 0;
    await speakReply(
      [{ type: "text", text: "hola" }],
      async () => void (calls += 1),
      AbortSignal.abort(),
    );
    expect(calls).toBe(0);
  });
});

describe("shouldSpeak", () => {
  test("only a completed voice turn with the flag on", () => {
    const completed = { status: "completed" };
    expect(
      shouldSpeak({ enabled: true, mode: "voice", outcome: completed }),
    ).toBe(true);
    expect(
      shouldSpeak({ enabled: false, mode: "voice", outcome: completed }),
    ).toBe(false);
    expect(
      shouldSpeak({ enabled: true, mode: "operator", outcome: completed }),
    ).toBe(false);
    // A headset disconnect is `unknown`, a mid-turn failure `failed`.
    for (const status of ["aborted", "failed", "unknown"])
      expect(
        shouldSpeak({ enabled: true, mode: "voice", outcome: { status } }),
      ).toBe(false);
  });

  test("the flag is off unless HEADSET_SPEAK_REPLY=1", () => {
    expect(headsetSpeaksReplies({})).toBe(false);
    expect(headsetSpeaksReplies({ HEADSET_SPEAK_REPLY: "0" })).toBe(false);
    expect(headsetSpeaksReplies({ HEADSET_SPEAK_REPLY: "true" })).toBe(false);
    expect(headsetSpeaksReplies({ HEADSET_SPEAK_REPLY: "1" })).toBe(true);
  });
});
