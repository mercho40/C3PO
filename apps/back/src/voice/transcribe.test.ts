import { beforeEach, describe, expect, test } from "bun:test";
import { testConfig } from "../testing/fakes";
import {
  MAX_AUDIO_BYTES,
  TranscribeError,
  createTranscriber,
  transcribeTimeoutMs,
} from "./transcribe";

// Each test states its own config, never the process's: a fresh transcriber
// (and its failover state) per call to `transcriber`.
let fallback = false;
beforeEach(() => {
  fallback = false;
});
function transcriber(fetchImpl: typeof fetch, now?: () => number) {
  const config = testConfig({
    AGENT_API_KEY: "k",
    ...(fallback
      ? {
          AGENT_FALLBACK_BASE_URL: "https://fb.test/v1",
          AGENT_FALLBACK_MODEL: "gemini-x",
          AGENT_FALLBACK_API_KEY: "fk",
        }
      : {}),
  }).transcribe;
  return createTranscriber(config, { fetch: fetchImpl, now });
}
/** One call on a fresh transcriber, as the old module function was called. */
const transcribe = (
  audio: ArrayBuffer,
  contentType: string,
  fetchImpl: typeof fetch,
  timeoutMs?: number,
) => transcriber(fetchImpl)(audio, contentType, timeoutMs);

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

describe("transcribeTimeoutMs", () => {
  test("grows with the clip: 4 s + 0.25 s per second, capped at 12 s", () => {
    const wav = (seconds: number) => 44 + seconds * 32_000;
    expect(transcribeTimeoutMs(wav(1))).toBe(4_250);
    expect(transcribeTimeoutMs(wav(10))).toBe(6_500);
    expect(transcribeTimeoutMs(wav(30))).toBe(11_500);
    expect(transcribeTimeoutMs(wav(60))).toBe(12_000);
    expect(transcribeTimeoutMs(0)).toBe(4_000);
  });
});

describe("transcribe failover", () => {
  function withFallback() {
    fallback = true;
  }
  function gateway(primaryStatus: number, calls: string[]) {
    return (async (url: string, init: RequestInit) => {
      calls.push(url);
      if (url.endsWith("/audio/transcriptions"))
        return new Response("down", { status: primaryStatus });
      const body = JSON.parse(String(init.body));
      expect(body.model).toBe("gemini-x");
      expect(body.messages[0].content[1].input_audio.format).toBe("wav");
      return new Response(
        JSON.stringify({ choices: [{ message: { content: " Saludá. " } }] }),
      );
    }) as unknown as typeof fetch;
  }

  test("ia-tic down: the same WAV is transcribed by the fallback", async () => {
    withFallback();
    const calls: string[] = [];
    const text = await transcribe(
      new ArrayBuffer(8),
      "audio/wav",
      gateway(502, calls),
    );
    expect(text).toBe("Saludá.");
    expect(calls).toHaveLength(2);
    expect(calls[0]).toEndWith("/audio/transcriptions");
    expect(calls[1]).toBe("https://fb.test/v1/chat/completions");
  });

  test("after a failover the next clip skips ia-tic until the sticky window ends", async () => {
    withFallback();
    let t = 1_000;
    const calls: string[] = [];
    const one = transcriber(gateway(503, calls), () => t);
    await one(new ArrayBuffer(8), "audio/wav", 5_000);
    calls.length = 0;
    t += 1_000;
    await one(new ArrayBuffer(8), "audio/wav", 5_000);
    expect(calls).toEqual(["https://fb.test/v1/chat/completions"]);
    t += 400_000;
    calls.length = 0;
    await one(new ArrayBuffer(8), "audio/wav", 5_000);
    expect(calls[0]).toEndWith("/audio/transcriptions");
  });

  test("a 4xx from ia-tic does not fail over", async () => {
    withFallback();
    const calls: string[] = [];
    await expect(
      transcribe(new ArrayBuffer(8), "audio/wav", gateway(400, calls)),
    ).rejects.toThrow("transcription_failed_400");
    expect(calls).toHaveLength(1);
  });

  test("no fallback configured, or webm audio: ia-tic's failure stands", async () => {
    const calls: string[] = [];
    await expect(
      transcribe(new ArrayBuffer(8), "audio/wav", gateway(502, calls)),
    ).rejects.toThrow("transcription_failed_502");
    withFallback();
    await expect(
      transcribe(new ArrayBuffer(8), "audio/webm", gateway(502, calls)),
    ).rejects.toThrow("transcription_failed_502");
    expect(calls.every((u) => u.endsWith("/audio/transcriptions"))).toBe(true);
  });
});
