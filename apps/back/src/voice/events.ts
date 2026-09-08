import { bridgeSiblingUrl } from "@back/bridge/url";

export type VoiceStatus = {
  mic_ever_open?: boolean;
  always_listening?: boolean;
};

export type VoiceEvent = {
  seq: number;
  kind: "speech" | "stop";
  text: string;
  age_s?: number;
};

/** Parse an SSE byte stream into voice events.
 *
 * The parser intentionally understands only the SSE field we produce (`data`).
 * Comments are keepalives; malformed events are ignored so one damaged frame
 * cannot permanently end a live voice session.
 */
export async function* parseVoiceEvents(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<VoiceEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");

      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const payload = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!payload) continue;

        try {
          const event = JSON.parse(payload) as Partial<VoiceEvent>;
          if (
            typeof event.seq === "number" &&
            (event.kind === "speech" || event.kind === "stop") &&
            typeof event.text === "string" &&
            event.text.trim()
          ) {
            yield event as VoiceEvent;
          }
        } catch {
          // Ignore one malformed event and keep the long-lived stream healthy.
        }
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function bridgeVoiceStatus(signal: AbortSignal): Promise<VoiceStatus> {
  const response = await fetch(bridgeSiblingUrl("/telemetry/voice"), {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error(`voice status unavailable (${response.status})`);
  return (await response.json()) as VoiceStatus;
}

/** Connect directly from the trusted backend to the bridge's event stream. */
export async function* bridgeVoiceEvents(
  signal: AbortSignal,
): AsyncGenerator<VoiceEvent> {
  const response = await fetch(bridgeSiblingUrl("/telemetry/voice/events"), {
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`voice event stream unavailable (${response.status})`);
  }
  yield* parseVoiceEvents(response.body);
}
