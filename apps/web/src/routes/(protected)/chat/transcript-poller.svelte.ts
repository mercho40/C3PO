/**
 * Keeps a voice transcript in /chat current while its realtime session runs.
 *
 * See `$lib/chat/transcript.ts` for why this exists and why it is safe. It
 * extends the shared Poller, so it inherits the console's rules: one request in
 * flight, a timeout on every request, a 401 goes to the login page (never
 * "robot offline"), and it pauses while the tab is hidden — a background tab
 * has no business re-reading a transcript nobody is looking at.
 */

import type { UIMessage } from "ai";
import { createApi } from "$lib/api";
import { Poller, type PollResult } from "$lib/robot/poller.svelte";
import { transcriptSignature } from "$lib/chat/transcript";

type TranscriptMessage = { id: string; role: string; parts: unknown };

export class TranscriptPoller extends Poller {
  /** The latest transcript. Starts as the server load's copy. */
  messages = $state.raw<UIMessage[]>([]);
  /** The latest title (the realtime engine may set it after the first turn). */
  title = $state<string | null>(null);
  /** The chat was deleted (404) while being watched. */
  gone = $state(false);

  readonly #id: string;
  #signature: string;

  constructor(id: string, initial: TranscriptMessage[], title: string | null) {
    super({ timeoutMs: 5000, pauseWhenHidden: true });
    this.#id = id;
    this.messages = initial as UIMessage[];
    this.title = title;
    this.#signature = transcriptSignature(initial);
    // The server load just fetched it, so it is as fresh as anything a first
    // poll would bring back.
    this.lastOkAt = Date.now();
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const { data, error } = await createApi(fetch)
      .chats({ id: this.#id })
      .get({ fetch: { signal } });
    if (error) {
      if (error.status === 401) return "unauthorized";
      if (error.status === 404) {
        this.gone = true;
        this.stop();
      }
      return "error";
    }
    if (!data) return "error";
    this.gone = false;
    this.title = data.title;
    const next = data.messages.map((m) => ({
      id: m.id,
      role: m.role,
      parts: m.parts,
    }));
    const signature = transcriptSignature(next);
    // Same rows as last time: keep the array, so nothing re-renders.
    if (signature !== this.#signature) {
      this.#signature = signature;
      this.messages = next as UIMessage[];
    }
    return "ok";
  }
}
