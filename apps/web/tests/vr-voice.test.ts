import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
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
import { reselect } from "../src/lib/webxr/menu-layer";

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
    expect(pendingConfirmation(msgs)).toEqual({
      tool: "reach",
      input: refused.input,
      id: "reach",
    });
    // Volver first: index 0 is never the item that grants T2 or resumes.
    expect(voiceMenu("reach", false).map((i) => i.label)).toEqual([
      "Volver a gestos",
      "Confirmar reach",
    ]);
    expect(voiceMenu("reach", true).map((i) => i.name)).toEqual([
      VOICE_CLOSE,
      VOICE_RESUME,
    ]);
    expect(pendingConfirmation([...msgs, user("otra cosa")])).toBeNull();
  });

  test("Confirmar names one refused call and shows the arguments it grants", () => {
    const first = { ...refused, toolCallId: "call_1" };
    const pending = pendingConfirmation([user("Extendé"), assistant(first)]);
    expect(pending?.id).toBe("call_1");
    const menu = voiceMenu(pending!.tool, false, pending!);
    expect(menu[1].name).toBe("voice:confirm:call_1");
    expect(menu[1].label).toBe('Confirmar reach {"side":"right","x_m":0.3}');

    // A newer refusal of the same tool, other arguments: a different item, so
    // a highlight armed on the old one disarms instead of granting the new.
    const second = {
      ...refused,
      toolCallId: "call_2",
      input: { side: "left" },
    };
    const next = pendingConfirmation([user("Extendé"), assistant(second)])!;
    const swapped = voiceMenu(next.tool, false, next);
    expect(reselect(menu[1], true, swapped).armed).toBe(false);
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

describe("VR menu reselection after a list swap (R5)", () => {
  const item = (name: string) => ({ name, label: name, verified: true });
  const voice = voiceMenu(null, false); // [Volver]
  const withConfirm = voiceMenu("move_arm", false); // [Volver, Confirmar]
  const gestures = withVoiceEntry([item("wave"), item("hug")]);

  test("the highlight follows the item by name, so Volver stays Volver", () => {
    const r = reselect(voice[0], true, withConfirm);
    expect(withConfirm[r.selected].name).toBe(VOICE_CLOSE);
    expect(r.armed).toBe(true);
  });

  test("a vanished item falls back to a harmless one and disarms the trigger", () => {
    // Confirmar was highlighted, then the PARAR menu replaced it.
    const r = reselect(withConfirm[1], true, voiceMenu(null, true));
    expect(voiceMenu(null, true)[r.selected].name).toBe(VOICE_CLOSE);
    expect(r.armed).toBe(false);
  });

  test("returning to gestures never lands armed on a preset", () => {
    const r = reselect(voice[0], true, gestures);
    expect(gestures[r.selected].name).toBe(VOICE_OPEN);
    expect(r.armed).toBe(false);
  });

  test("a disarmed highlight stays disarmed across a swap that keeps it", () => {
    const r = reselect(withConfirm[0], false, voice);
    expect(r.armed).toBe(false);
  });

  test("the first list ever arms as before", () => {
    expect(reselect(null, true, gestures).armed).toBe(true);
  });
});

describe("the push-to-talk AudioWorklet (C4)", () => {
  type Port = {
    sent: unknown[];
    onmessage: ((e: { data: unknown }) => void) | null;
  };
  type Proc = { port: Port; process(inputs: Float32Array[][]): boolean };
  // AudioWorkletGlobalScope, stubbed: the worklet registers itself on import.
  let Processor: (new () => Proc) | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  g.AudioWorkletProcessor = class {
    port: Port = {
      sent: [],
      onmessage: null,
      postMessage(this: Port, m: unknown) {
        this.sent.push(m);
      },
    } as Port & { postMessage(m: unknown): void };
  };
  g.registerProcessor = (name: string, cls: new () => Proc) => {
    if (name === "mic-capture") Processor = cls;
  };
  const loaded = import("../src/lib/robot/mic-capture.worklet.js").then(() => {
    delete g.AudioWorkletProcessor;
    delete g.registerProcessor;
    return Processor!;
  });
  const recordingTo = (p: Proc, on: boolean) => p.port.onmessage!({ data: on });

  test("posts nothing while idle, and always keeps running", async () => {
    const p = new (await loaded)();
    expect(p.process([[new Float32Array([0.1, 0.2])]])).toBe(true);
    expect(p.port.sent.length).toBe(0);
  });

  test("while recording, posts a copy of each block; stops on false", async () => {
    const p = new (await loaded)();
    recordingTo(p, true);
    const block = new Float32Array([0.1, 0.2]);
    expect(p.process([[block]])).toBe(true);
    block[0] = 9; // the engine reuses the array for the next block
    expect(Array.from(p.port.sent[0] as Float32Array)).toEqual([
      Math.fround(0.1),
      Math.fround(0.2),
    ]);
    recordingTo(p, false);
    p.process([[block]]);
    expect(p.port.sent.length).toBe(1);
  });

  test("an input with no channels neither throws nor posts", async () => {
    const p = new (await loaded)();
    recordingTo(p, true);
    expect(p.process([[]])).toBe(true);
    expect(p.process([])).toBe(true);
    expect(p.port.sent.length).toBe(0);
  });
});

// Source, because AudioContext and getUserMedia do not exist in this harness.
const voiceSrc = readFileSync(
  fileURLToPath(
    new URL("../src/lib/robot/vr-voice.svelte.ts", import.meta.url),
  ),
  "utf8",
);

describe("the headset microphone's lifecycle (C4, C5)", () => {
  test("no ScriptProcessor; the context is built before getUserMedia is awaited", () => {
    expect(voiceSrc).not.toContain("createScriptProcessor");
    const enable = voiceSrc.slice(voiceSrc.indexOf("async enableMic()"));
    expect(enable.indexOf("new AudioContext(")).toBeGreaterThan(-1);
    expect(enable.indexOf("new AudioContext(")).toBeLessThan(
      enable.indexOf("getUserMedia"),
    );
  });

  test("a failed open closes the context and stops the stream", () => {
    const enable = voiceSrc.slice(voiceSrc.indexOf("async enableMic()"));
    const caught = enable.slice(enable.indexOf("} catch (e) {"));
    expect(caught.indexOf("void ctx?.close();")).toBeGreaterThan(-1);
    expect(caught.indexOf("t.stop()")).toBeGreaterThan(-1);
  });

  test("the AudioContext is built inside the try, so a throw reaches micError", () => {
    const enable = voiceSrc.slice(voiceSrc.indexOf("async enableMic()"));
    expect(enable.indexOf("try {")).toBeLessThan(
      enable.indexOf("new AudioContext("),
    );
  });

  test("a second tap while the prompt is up opens nothing", () => {
    const enable = voiceSrc.slice(voiceSrc.indexOf("async enableMic()"));
    expect(enable).toContain("if (this.micReady || this.#opening) return;");
    expect(enable).toContain("} finally {\n      this.#opening = false;");
  });

  test("a dead worklet is reported, not read as 'Muy corto'", () => {
    expect(voiceSrc).toContain("node.onprocessorerror = () => {");
    expect(voiceSrc).toContain("El micrófono no entrega audio");
  });

  test("a block arriving after the release is dropped", () => {
    expect(voiceSrc).toContain("if (!this.#recording) return;");
  });

  test("an ended track releases the mic only, not the agent turn", () => {
    expect(voiceSrc).toContain("track.onended = () => this.#releaseMic();");
    const release = voiceSrc.slice(
      voiceSrc.indexOf("#releaseMic(): void {"),
      voiceSrc.indexOf("destroy(): void {"),
    );
    expect(release).toContain("this.micReady = false;");
    expect(release).not.toContain("#chat.stop()");
  });
});
