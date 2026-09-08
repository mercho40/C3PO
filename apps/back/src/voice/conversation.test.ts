import { describe, expect, test } from "bun:test";
import { VoiceConversation } from "./conversation";

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
});
