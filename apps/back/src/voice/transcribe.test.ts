import { describe, expect, test } from "bun:test";
import { MAX_AUDIO_BYTES, TranscribeError, transcribe } from "./transcribe";

process.env.AGENT_API_KEY = "k";

describe("transcribe", () => {
  test("forwards the audio as multipart and returns the trimmed text", async () => {
    let sent: FormData | null = null;
    const fake = (async (_url: string, init: RequestInit) => {
      sent = init.body as FormData;
      return new Response(JSON.stringify({ text: " Andá hasta la silla. " }));
    }) as unknown as typeof fetch;
    const text = await transcribe(new ArrayBuffer(10), "audio/wav", fake);
    expect(text).toBe("Andá hasta la silla.");
    expect(sent!.get("model")).toBe("tic-transcript");
    expect(sent!.get("language")).toBe("es");
  });

  test("refuses empty and oversized audio before calling anyone", async () => {
    const never = (async () => {
      throw new Error("called");
    }) as unknown as typeof fetch;
    await expect(
      transcribe(new ArrayBuffer(0), "audio/wav", never),
    ).rejects.toThrow("empty_audio");
    await expect(
      transcribe(new ArrayBuffer(MAX_AUDIO_BYTES + 1), "audio/wav", never),
    ).rejects.toBeInstanceOf(TranscribeError);
  });

  test("a gateway failure is a 502, not a crash", async () => {
    const down = (async () =>
      new Response("bad", { status: 500 })) as unknown as typeof fetch;
    await expect(
      transcribe(new ArrayBuffer(4), "audio/wav", down),
    ).rejects.toMatchObject({
      status: 502,
    });
  });
});
