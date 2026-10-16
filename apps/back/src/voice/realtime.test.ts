import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  RealtimeVoiceSession,
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
    expect(tools.map((tool) => tool.name)).toEqual(["walk_to"]);
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
