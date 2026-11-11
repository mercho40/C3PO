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

export class TranscribeError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function transcribe(
  audio: ArrayBuffer,
  contentType: string,
  fetchImpl: typeof fetch = fetch,
  timeoutMs = 30_000,
): Promise<string> {
  const key = process.env.AGENT_API_KEY;
  if (!key) throw new TranscribeError(503, "AGENT_API_KEY is not set");
  if (audio.byteLength === 0) throw new TranscribeError(400, "empty_audio");
  if (audio.byteLength > MAX_AUDIO_BYTES)
    throw new TranscribeError(413, "audio_too_long");
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
