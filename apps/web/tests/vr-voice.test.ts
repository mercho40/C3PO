import { describe, expect, test } from "bun:test";
import { concat, downsample, encodeWav } from "../src/lib/robot/wav";
import type { MessageLike } from "../src/lib/chat/turn";
import {
  VOICE_CLOSE,
  VOICE_OPEN,
  VOICE_RESUME,
  pendingConfirmation,
  voiceLines,
  voiceMenu,
  withVoiceEntry,
} from "../src/lib/robot/voice-panel";

describe("WAV for /voice/transcribe", () => {
  test("downsamples 48 kHz to 16 kHz by averaging", () => {
    const out = downsample(
      new Float32Array([0.3, 0.3, 0.3, -0.6, -0.6, -0.6]),
      48_000,
    );
    expect(Array.from(out).map((v) => Number(v.toFixed(3)))).toEqual([
      0.3, -0.6,
    ]);
    const same = new Float32Array([1, 2]);
    expect(downsample(same, 16_000)).toBe(same);
  });

  test("encodes a valid 16 kHz mono PCM16 header and clips samples", () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 2]));
    const v = new DataView(wav.buffer);
    const tag = (at: number) => String.fromCharCode(...wav.slice(at, at + 4));
    expect([tag(0), tag(8), tag(12), tag(36)]).toEqual([
      "RIFF",
      "WAVE",
      "fmt ",
      "data",
    ]);
    expect(v.getUint16(20, true)).toBe(1); // PCM
    expect(v.getUint16(22, true)).toBe(1); // mono
    expect(v.getUint32(24, true)).toBe(16_000);
    expect(v.getUint16(34, true)).toBe(16);
    expect(v.getUint32(40, true)).toBe(8);
    expect(wav.length).toBe(44 + 8);
    expect([2, 3, 4].map((i) => v.getInt16(44 + i * 2 - 2, true))).toEqual([
      32767, -32768, 32767,
    ]);
  });

  test("concatenates recorder chunks in order", () => {
    expect(
      Array.from(concat([new Float32Array([1, 2]), new Float32Array([3])])),
    ).toEqual([1, 2, 3]);
  });
});

const user = (text: string): MessageLike => ({
  id: "u",
  role: "user",
  parts: [{ type: "text", text }],
});
const assistant = (...parts: Record<string, unknown>[]): MessageLike =>
  ({ id: "a", role: "assistant", parts }) as unknown as MessageLike;
const refused = {
  type: "tool-reach",
  state: "output-available",
  input: { side: "right", x_m: 0.3 },
  output: {
    error:
      "tool_error: refused: reach is a T2 tool and needs an operator's confirmation first.",
  },
};

describe("the headset conversation panel", () => {
  test("shows what you said, what the robot did, and its answer", () => {
    const lines = voiceLines(
      [
        user("Andá hasta la silla"),
        assistant(
          {
            type: "tool-go_to_object",
            state: "output-available",
            input: { query: "silla" },
            output: { status: "completed" },
          },
          { type: "text", text: "Llegué a la silla." },
        ),
      ],
      { phase: "idle", stopStanding: false, error: null },
    );
    expect(lines.map((l) => l.kind)).toEqual(["you", "tool", "robot"]);
    expect(lines[0].text).toBe("Vos: Andá hasta la silla");
    expect(lines[1].text.startsWith("→ go_to_object(silla)")).toBe(true);
    expect(lines[2].text).toBe("Robot: Llegué a la silla.");
  });

  test("a standing PARAR and the current phase come first", () => {
    const lines = voiceLines([], {
      phase: "listening",
      stopStanding: true,
      error: null,
    });
    expect(lines[0].kind).toBe("warn");
    expect(lines[0].text).toContain("PARAR");
    expect(lines[1].text).toContain("Escuchando");
  });

  test("a refused T2 call offers Confirmar; a standing PARAR offers Reanudar instead", () => {
    const msgs = [user("Extendé la mano"), assistant(refused)];
    expect(pendingConfirmation(msgs)).toBe("reach");
    expect(voiceMenu("reach", false).map((i) => i.label)).toEqual([
      "Confirmar reach",
      "Volver a gestos",
    ]);
    expect(voiceMenu("reach", true).map((i) => i.name)).toEqual([
      VOICE_RESUME,
      VOICE_CLOSE,
    ]);
    expect(pendingConfirmation([...msgs, user("otra cosa")])).toBeNull();
  });

  test("the gestures menu opens with the voice entry, never a skill name", () => {
    const items = withVoiceEntry([
      { name: "wave", label: "Saludar", verified: true },
    ]);
    expect(items[0].name).toBe(VOICE_OPEN);
    expect(
      items.every((i) => i.name === "wave" || i.name.startsWith("voice:")),
    ).toBe(true);
  });
});
