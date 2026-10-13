/**
 * Voice transcripts in /chat — when to refresh them, and how to say how fresh
 * they are.
 *
 * A voice chat is written by the realtime engine (apps/back realtime.ts adds
 * user, assistant and tool rows while the session runs). /chat used to load it
 * once and show it as if complete while the robot kept talking and acting —
 * stale data presented as current. It is now re-fetched on an interval while
 * visible. That is safe precisely because voice chats are read-only here:
 * nothing ever streams into a `Chat` for them, so replacing the messages
 * cannot clobber a live turn.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import { formatSince } from "../robot/format";

export const TRANSCRIPT_POLL_MS = 4000;

export type SelectedStatus = "ok" | "not-found" | "error" | "none";

/** Poll only a voice chat that actually loaded. Visibility is the Poller's job. */
export function shouldPollTranscript(opts: {
  channel: string | null | undefined;
  selectedStatus: SelectedStatus;
}): boolean {
  return opts.channel === "voice" && opts.selectedStatus === "ok";
}

/**
 * A cheap fingerprint of a transcript, so a poll that brings back the same
 * rows doesn't replace the array (and re-render every message) every 4 s.
 * Includes each row's serialized size because rows are upserted in place.
 */
export function transcriptSignature(
  messages: readonly { id: string; parts: unknown }[],
): string {
  return messages
    .map((m) => `${m.id}:${JSON.stringify(m.parts ?? null).length}`)
    .join("|");
}

export type Freshness = {
  text: string;
  tone: "quiet" | "warn";
  /**
   * What a screen reader is told — a sentence per STATE, with no clock in it,
   * empty while all is well. `text` ticks every second ("hace 12 s", "hace
   * 13 s"); the page used to put the live region on it, so a failing poll was
   * re-announced once a second for as long as it kept failing.
   */
  announce: string;
};

const GONE = "Esta conversación ya no existe";
const FAILED = "No se pudo actualizar";

/**
 * "Actualizado hace 3 s" while polls succeed; once they fail, say so and how
 * old the copy on screen is — never keep claiming it is current.
 */
export function transcriptFreshness(opts: {
  lastOkAt: number | null;
  failures: number;
  gone: boolean;
  now: number;
}): Freshness {
  if (opts.gone) {
    return { text: GONE, tone: "warn", announce: GONE };
  }
  if (opts.failures > 0) {
    return {
      text:
        opts.lastOkAt === null
          ? FAILED
          : `${FAILED} · última copia ${formatSince(opts.lastOkAt, opts.now)}`,
      tone: "warn",
      announce:
        "La transcripción no se pudo actualizar: lo que ves puede no estar al día.",
    };
  }
  if (opts.lastOkAt === null) {
    return { text: "Actualizando…", tone: "quiet", announce: "" };
  }
  return {
    text: `Actualizado ${formatSince(opts.lastOkAt, opts.now)}`,
    tone: "quiet",
    announce: "",
  };
}
