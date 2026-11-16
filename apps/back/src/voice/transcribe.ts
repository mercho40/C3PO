/**
 * Speech -> text for the headset's "Hablar con el robot".
 *
 * The Quest records the operator's own microphone (push-to-talk, so the room
 * is not the prompt) and sends a WAV; this forwards it to the same
 * OpenAI-compatible gateway the agent uses (TIC AI's `tic-transcript`), and
 * the text then goes through the ordinary agent turn and every bridge gate.
 * The gateway key stays on this server.
 */
const BASE_URL = process.env.AGENT_BASE_URL ?? "https://ia.ort.edu.ar/api/v1";
const MODEL = process.env.AGENT_TRANSCRIBE_MODEL ?? "tic-transcript";
/** ~60 s of 16 kHz mono 16-bit WAV; a push-to-talk utterance is far shorter. */
export const MAX_AUDIO_BYTES = 2_000_000;

/** Budget for transcribing one 16 kHz mono 16-bit WAV: 4 s plus 0.25 s per
 *  second of audio, capped at 12 s. A flat 5 s lost every long utterance. */
export function transcribeTimeoutMs(wavBytes: number): number {
  const seconds = Math.max(0, wavBytes - 44) / 32_000;
  return Math.min(12_000, Math.round(4_000 + 250 * seconds));
}

export class TranscribeError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** After the primary fails, later clips go straight to the fallback for this long. */
const STICKY_MS = Number(process.env.AGENT_FALLBACK_STICKY_S ?? 300) * 1000;
let fallbackUntil = 0;

/** Test hook: forget an earlier failover. */
export function resetTranscribeFailover(): void {
  fallbackUntil = 0;
}

export async function transcribe(
  audio: ArrayBuffer,
  contentType: string,
  fetchImpl: typeof fetch = fetch,
  timeoutMs = 30_000,
  now: () => number = Date.now,
): Promise<string> {
  const key = process.env.AGENT_API_KEY;
  if (!key) throw new TranscribeError(503, "AGENT_API_KEY is not set");
  if (audio.byteLength === 0) throw new TranscribeError(400, "empty_audio");
  if (audio.byteLength > MAX_AUDIO_BYTES)
    throw new TranscribeError(413, "audio_too_long");
  // ia-tic down took transcription with it (2026-11-11): the agent already fails over
  // (agent/failover.ts), so the robot mic and the headset follow it to the same fallback.
  const fallback = fallbackConfig(contentType);
  if (fallback && now() < fallbackUntil)
    return transcribeFallback(audio, fallback, fetchImpl, timeoutMs);
  try {
    return await transcribePrimary(audio, contentType, key, fetchImpl, timeoutMs);
  } catch (err) {
    // Only an unreachable or overloaded gateway; a 4xx is our request and fails there too.
    const down =
      err instanceof TranscribeError &&
      (err.message === "transcription_unavailable" ||
        /^transcription_failed_(5\d\d|429)$/.test(err.message));
    if (!fallback || !down) throw err;
    fallbackUntil = now() + STICKY_MS;
    console.log("[voice] transcribe.provider_failover", JSON.stringify({ reason: err.message }));
    return transcribeFallback(audio, fallback, fetchImpl, timeoutMs);
  }
}

type Fallback = { baseUrl: string; model: string; key: string };

function fallbackConfig(contentType: string): Fallback | null {
  const baseUrl = process.env.AGENT_FALLBACK_BASE_URL;
  const model = process.env.AGENT_FALLBACK_MODEL;
  const key = process.env.AGENT_FALLBACK_API_KEY;
  // shortcut: the chat endpoint's input_audio takes wav/mp3, not the headset's webm; webm keeps ia-tic only.
  if (!baseUrl || !model || !key || contentType.includes("webm")) return null;
  return { baseUrl, model, key };
}

/** A model fallback has no /audio/transcriptions: send the WAV as chat input_audio. */
async function transcribeFallback(
  audio: ArrayBuffer,
  fb: Fallback,
  fetchImpl: typeof fetch,
  timeoutMs: number,
): Promise<string> {
  let res: Response;
  try {
    res = await fetchImpl(`${fb.baseUrl}/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${fb.key}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: fb.model,
        messages: [
          {
            role: "user",
            content: [
              {
                type: "text",
                text: "Transcribí literalmente este audio en español. Respondé solo con lo que se dijo, sin comillas ni comentarios. Si no se entiende ninguna palabra, respondé vacío.",
              },
              {
                type: "input_audio",
                input_audio: { data: Buffer.from(audio).toString("base64"), format: "wav" },
              },
            ],
          },
        ],
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new TranscribeError(502, "transcription_unavailable");
  }
  if (!res.ok) throw new TranscribeError(502, `transcription_failed_${res.status}`);
  const body = (await res.json().catch(() => ({}))) as {
    choices?: { message?: { content?: unknown } }[];
  };
  const text = body.choices?.[0]?.message?.content;
  return typeof text === "string" ? text.trim() : "";
}

async function transcribePrimary(
  audio: ArrayBuffer,
  contentType: string,
  key: string,
  fetchImpl: typeof fetch,
  timeoutMs: number,
): Promise<string> {
  const form = new FormData();
  form.append("model", MODEL);
  form.append("language", "es");
  const ext = contentType.includes("webm") ? "webm" : "wav";
  form.append(
    "file",
    new Blob([audio], { type: contentType || "audio/wav" }),
    `utterance.${ext}`,
  );
  let res: Response;
  try {
    res = await fetchImpl(`${BASE_URL}/audio/transcriptions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}` },
      body: form,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new TranscribeError(502, "transcription_unavailable");
  }
  if (!res.ok)
    throw new TranscribeError(502, `transcription_failed_${res.status}`);
  const body = (await res.json().catch(() => ({}))) as { text?: unknown };
  return typeof body.text === "string" ? body.text.trim() : "";
}
