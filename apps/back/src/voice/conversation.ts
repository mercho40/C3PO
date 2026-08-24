import type { UIMessage } from "ai";

export type VoiceConversationDeps = {
  complete: (
    messages: UIMessage[],
  ) => string | AsyncIterable<string> | Promise<string | AsyncIterable<string>>;
  speak: (text: string) => Promise<void>;
  newId: () => string;
  now?: () => number;
};

export type VoiceTurnMetrics = {
  modelFirstDeltaMs: number | null;
  firstSentenceMs: number | null;
  firstSpeechMs: number | null;
  completionMs: number;
  totalMs: number;
  speechCalls: number;
  characters: number;
};

export type VoiceConversationState = {
  phase: "idle" | "streaming" | "speaking";
  lastTurn: VoiceTurnMetrics | null;
};

const MAX_SPEECH_CHARACTERS = 500;
const SPANISH_ABBREVIATIONS = new Set([
  "dr",
  "dra",
  "etc",
  "sr",
  "sra",
  "srta",
  "ud",
  "uds",
]);

function isSentenceBoundary(text: string, index: number): boolean {
  const punctuation = text[index];
  if (punctuation === "!" || punctuation === "?" || punctuation === "…") {
    return true;
  }
  if (punctuation !== ".") return false;

  const previous = text[index - 1];
  const next = text[index + 1];
  if (/\d/u.test(previous ?? "") && (next === undefined || /\d/u.test(next))) {
    return false;
  }

  const prefix = text.slice(0, index + 1);
  const word = prefix.match(/([\p{L}]+)\.$/u)?.[1]?.toLocaleLowerCase("es");
  return !word || (word.length > 1 && !SPANISH_ABBREVIATIONS.has(word));
}

function takeCompleteSentences(text: string): {
  sentences: string[];
  remainder: string;
} {
  const sentences: string[] = [];
  let consumed = 0;

  for (let index = 0; index < text.length; index += 1) {
    if (!isSentenceBoundary(text, index)) continue;

    let end = index + 1;
    while (end < text.length && /[.!?…]/u.test(text[end] ?? "")) end += 1;
    while (end < text.length && /["'»”’\)\]\}]/u.test(text[end] ?? ""))
      end += 1;

    const sentence = text.slice(consumed, end).trim();
    if (sentence) sentences.push(sentence);
    consumed = end;
    index = end - 1;
  }

  return {
    sentences,
    remainder: text.slice(consumed).trimStart(),
  };
}

function splitForSpeech(text: string): string[] {
  const chunks: string[] = [];
  let remainder = text.trim();

  while (remainder.length > MAX_SPEECH_CHARACTERS) {
    const candidate = remainder.slice(0, MAX_SPEECH_CHARACTERS + 1);
    const whitespace = candidate.lastIndexOf(" ");
    const splitAt = whitespace > 0 ? whitespace : MAX_SPEECH_CHARACTERS;
    chunks.push(remainder.slice(0, splitAt).trim());
    remainder = remainder.slice(splitAt).trimStart();
  }

  if (remainder) chunks.push(remainder);
  return chunks;
}

async function* deltasOf(
  completion: string | AsyncIterable<string>,
): AsyncGenerator<string> {
  if (typeof completion === "string") {
    yield completion;
    return;
  }
  yield* completion;
}

/** One bounded, in-memory spoken conversation.
 *
 * Complete sentences are spoken as soon as the model makes them available.
 * Speech remains serialized even while the model continues streaming.
 */
export class VoiceConversation {
  private messages: UIMessage[] = [];
  private phase: VoiceConversationState["phase"] = "idle";
  private lastTurn: VoiceTurnMetrics | null = null;

  constructor(
    private readonly deps: VoiceConversationDeps,
    private readonly maxMessages = 20,
  ) {}

  reset(): void {
    this.messages = [];
    this.lastTurn = null;
  }

  history(): UIMessage[] {
    return [...this.messages];
  }

  state(): VoiceConversationState {
    return { phase: this.phase, lastTurn: this.lastTurn };
  }

  async turn(utterance: string): Promise<VoiceTurnMetrics> {
    const now = this.deps.now ?? (() => performance.now());
    const startedAt = now();
    let firstDeltaAt: number | null = null;
    let firstSentenceAt: number | null = null;
    let firstSpeechAt: number | null = null;
    let speechCalls = 0;
    let speechTail: Promise<void> | null = null;
    let fullReply = "";
    let pendingSentence = "";

    const enqueueSpeech = (text: string): void => {
      for (const chunk of splitForSpeech(text)) {
        firstSentenceAt ??= now();
        speechCalls += 1;
        const speak = async (): Promise<void> => {
          firstSpeechAt ??= now();
          await this.deps.speak(chunk);
        };
        speechTail = speechTail ? speechTail.then(speak) : speak();
      }
    };

    const userMessage: UIMessage = {
      id: this.deps.newId(),
      role: "user",
      parts: [{ type: "text", text: utterance }],
    };
    const context = [...this.messages, userMessage];

    this.phase = "streaming";
    try {
      const completion = await this.deps.complete(context);
      for await (const delta of deltasOf(completion)) {
        if (!delta) continue;
        firstDeltaAt ??= now();
        fullReply += delta;
        pendingSentence += delta;

        const parsed = takeCompleteSentences(pendingSentence);
        pendingSentence = parsed.remainder;
        for (const sentence of parsed.sentences) enqueueSpeech(sentence);
      }

      const reply = fullReply.trim();
      if (!reply) throw new Error("voice agent returned no spoken reply");
      if (pendingSentence.trim()) enqueueSpeech(pendingSentence);

      const streamEndedAt = now();
      const assistantMessage: UIMessage = {
        id: this.deps.newId(),
        role: "assistant",
        parts: [{ type: "text", text: reply }],
      };
      this.messages = [...context, assistantMessage].slice(-this.maxMessages);

      if (speechTail) {
        this.phase = "speaking";
        await speechTail;
      }

      const endedAt = now();
      const metrics: VoiceTurnMetrics = {
        modelFirstDeltaMs:
          firstDeltaAt === null ? null : firstDeltaAt - startedAt,
        firstSentenceMs:
          firstSentenceAt === null ? null : firstSentenceAt - startedAt,
        firstSpeechMs:
          firstSpeechAt === null ? null : firstSpeechAt - startedAt,
        completionMs: streamEndedAt - startedAt,
        totalMs: endedAt - startedAt,
        speechCalls,
        characters: reply.length,
      };
      this.lastTurn = metrics;
      return metrics;
    } finally {
      this.phase = "idle";
    }
  }
}
