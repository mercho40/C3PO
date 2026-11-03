/**
 * What the headset's "Hablar con el robot" panel shows, and which menu items it
 * offers. Pure: the glue (vr-voice.svelte.ts) holds the chat and the mic; this
 * decides the words, so it is tested without a headset.
 */
import {
  classifyToolPart,
  isToolPart,
  needsConfirmation,
  toolName,
  type ToolPartLike,
} from "#lib/chat/tool-part.js";
import { replyText, type MessageLike } from "#lib/chat/turn.js";
import type { MenuItem, VoiceLine } from "#lib/webxr/menu-layer.js";

export type VoicePhase = "idle" | "listening" | "transcribing" | "thinking";

/** Menu item names the voice mode owns. Never a skill name: they carry a colon. */
export const VOICE_OPEN = "voice:open";
export const VOICE_CLOSE = "voice:close";
export const VOICE_RESUME = "voice:resume";
export const VOICE_CONFIRM = "voice:confirm:";

const MAX = 52;
const cut = (s: string) => (s.length > MAX ? `${s.slice(0, MAX - 1)}…` : s);

function argSummary(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const values = Object.values(input as Record<string, unknown>);
  const first = values.find((v) => typeof v === "string") ?? values[0];
  if (first === undefined) return "";
  return typeof first === "string" ? first : JSON.stringify(first);
}

const PHASE_LINE: Record<Exclude<VoicePhase, "idle">, string> = {
  listening: "● Escuchando… soltá B/Y para enviar",
  transcribing: "Transcribiendo…",
  thinking: "Pensando…",
};

/** The last exchange: what you said, what the robot did, what it answered. */
export function voiceLines(
  messages: readonly MessageLike[],
  opts: { phase: VoicePhase; stopStanding: boolean; error: string | null },
): VoiceLine[] {
  const lines: VoiceLine[] = [];
  if (opts.stopStanding) {
    lines.push({
      text: "PARAR activo: el robot no se mueve. «Reanudar» abajo.",
      kind: "warn",
    });
  }
  if (opts.phase !== "idle")
    lines.push({ text: PHASE_LINE[opts.phase], kind: "info" });
  if (opts.error) lines.push({ text: cut(opts.error), kind: "warn" });

  const lastUser = messages.findLastIndex((m) => m.role === "user");
  if (lastUser === -1) {
    if (lines.length === 0) {
      lines.push({ text: "Mantené B/Y, hablá, y soltá.", kind: "info" });
    }
    return lines;
  }
  const said = replyText(messages[lastUser].parts);
  lines.push({ text: cut(`Vos: ${said}`), kind: "you" });
  const reply = messages
    .slice(lastUser + 1)
    .find((m) => m.role === "assistant");
  if (!reply) return lines;
  for (const part of reply.parts) {
    if (!isToolPart(part)) continue;
    const p = part as unknown as ToolPartLike;
    const d = classifyToolPart(p, { live: false });
    const arg = argSummary(p.input);
    lines.push({
      text: cut(`→ ${toolName(p)}${arg ? `(${arg})` : ""} · ${d.label}`),
      kind: d.tone === "error" ? "warn" : "tool",
    });
  }
  const text = replyText(reply.parts);
  if (text) lines.push({ text: cut(`Robot: ${text}`), kind: "robot" });
  return lines;
}

/** The T2 tool the last reply is waiting on, if any. */
export function pendingConfirmation(
  messages: readonly MessageLike[],
): string | null {
  const last = messages.at(-1);
  if (!last || last.role !== "assistant") return null;
  for (const part of [...last.parts].reverse()) {
    if (!isToolPart(part)) continue;
    const tool = needsConfirmation(part as unknown as ToolPartLike);
    if (tool) return tool;
  }
  return null;
}

/** The menu while talking: confirm / resume when relevant, and a way back. */
export function voiceMenu(
  pending: string | null,
  stopStanding: boolean,
): MenuItem[] {
  // "Volver" first: index 0 must never be the item that grants T2 or resumes.
  const items: MenuItem[] = [
    { name: VOICE_CLOSE, label: "Volver a gestos", verified: true },
  ];
  if (stopStanding)
    items.push({
      name: VOICE_RESUME,
      label: "Reanudar (quitar PARAR)",
      verified: true,
    });
  else if (pending) {
    items.push({
      name: `${VOICE_CONFIRM}${pending}`,
      label: `Confirmar ${pending}`,
      verified: true,
    });
  }
  return items;
}

/** The gestures menu plus the entry into voice mode. */
export function withVoiceEntry(items: readonly MenuItem[]): MenuItem[] {
  return [
    { name: VOICE_OPEN, label: "Hablar con el robot", verified: true },
    ...items,
  ];
}
