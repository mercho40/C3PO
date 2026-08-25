import { describe, expect, test } from "bun:test";
import { RealtimeVoiceSession, realtimeSessionUpdate, realtimeTools } from "./realtime";

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
    expect(tools.map((tool) => tool.name)).toEqual(["walk_to"]);
  });

  test("refuses to start without putting a missing API key on another machine", async () => {
    const previous = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    try {
      await expect(
        new RealtimeVoiceSession().start({ chatId: "chat", ownerId: "operator" }),
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
    expect(event.session.audio.input.format).toEqual({ type: "audio/pcm", rate: 24_000 });
    expect(event.session.audio.input.transcription.language).toBe("es");
    expect(event.session.audio.input.turn_detection).toMatchObject({
      type: "semantic_vad",
      create_response: true,
      interrupt_response: true,
    });
    expect(event.session.tools[0]?.name).toBe("wave");
  });
});
