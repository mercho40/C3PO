/**
 * Decisions about a chat TURN that the page used to make inline — when to show
 * "Pensando…", what a reply's copyable text is, and above all what recovering
 * from a failed turn is allowed to do.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import { isToolPart, toolName } from "./tool-part";

export type PartLike = {
  type: string;
  state?: string;
  text?: string;
  toolName?: string;
};

export type MessageLike = {
  id: string;
  role: string;
  parts: PartLike[];
};

export type ChatStatusLike = "submitted" | "streaming" | "ready" | "error";

const FINISHED_TOOL = new Set([
  "output-available",
  "output-error",
  "output-denied",
]);

/**
 * Is the model working with nothing on screen to show for it?
 *
 * True while the request is out (`submitted`), before the assistant message
 * exists, and — the case the page used to miss — BETWEEN steps of a multi-step
 * turn: after a tool card has come back, TIC AI takes seconds to decide the
 * next move, the status is `streaming`, and the last part is the finished tool
 * (or the next step's `step-start`). Without this the composer's stop button
 * was the only sign the turn was still running.
 */
export function awaitingModel(
  status: ChatStatusLike,
  messages: readonly MessageLike[],
): boolean {
  if (status === "submitted") return true;
  if (status !== "streaming") return false;
  const last = messages.at(-1);
  if (!last || last.role !== "assistant") return true;
  const part = last.parts.at(-1);
  if (!part) return true;
  if (part.type === "step-start") return true;
  return isToolPart(part) && FINISHED_TOOL.has(part.state ?? "");
}

/** A reply's text parts, joined — what "Copiar respuesta" puts on the clipboard. */
export function replyText(parts: readonly PartLike[]): string {
  return parts
    .filter((p) => p.type === "text" && typeof p.text === "string")
    .map((p) => (p.text as string).trim())
    .filter((t) => t.length > 0)
    .join("\n\n");
}

/**
 * How a failed turn may be recovered.
 *
 * - `retry`: nothing in the failed turn reached the robot. One click re-asks.
 * - `continue`: some skill was dispatched. `chat.regenerate()` would DELETE the
 *   card showing it and ask the agent the same question from scratch — which
 *   may walk the robot a second time (a TIC AI 502 on step 2, after `walk_to`
 *   ran in step 1, is the textbook case). The operator gets "Continuar"
 *   instead, which keeps the partial reply and tells the agent what already
 *   ran; re-running the whole turn needs an explicit confirmation.
 *
 * `ran` = the call came back (`output-available`). `maybe` = it was handed to
 * the bridge and the stream died before the answer (`input-available`, or an
 * approved call): it may well have moved the robot. A part still in
 * `input-streaming` never had complete arguments, so it was never dispatched.
 */
export type RecoveryPlan =
  | { kind: "retry" }
  | { kind: "continue"; ran: string[]; maybe: string[] };

export function planRecovery(messages: readonly MessageLike[]): RecoveryPlan {
  const last = messages.at(-1);
  // The request failed before any reply existed (HTTP error, gateway down at
  // step 0): nothing was dispatched.
  if (!last || last.role !== "assistant") return { kind: "retry" };

  const ran: string[] = [];
  const maybe: string[] = [];
  for (const part of last.parts) {
    if (!isToolPart(part)) continue;
    const name = toolName(part);
    if (part.state === "output-available") {
      if (!ran.includes(name)) ran.push(name);
    } else if (
      part.state === "input-available" ||
      part.state === "approval-responded"
    ) {
      if (!maybe.includes(name)) maybe.push(name);
    }
  }
  // A skill that both ran and may-have-run is just "ran".
  const onlyMaybe = maybe.filter((n) => !ran.includes(n));
  if (ran.length === 0 && onlyMaybe.length === 0) return { kind: "retry" };
  return { kind: "continue", ran, maybe: onlyMaybe };
}

/**
 * The message "Continuar" sends. It goes into the transcript as the operator's
 * own words, so it is written to be read by both the agent and a human later.
 */
export function continuationPrompt(plan: {
  ran: readonly string[];
  maybe: readonly string[];
}): string {
  const lines = ["El turno anterior se cortó por un error."];
  if (plan.ran.length > 0) {
    lines.push(`Ya se ejecutaron: ${plan.ran.join(", ")}.`);
  }
  if (plan.maybe.length > 0) {
    lines.push(
      `Puede que se hayan ejecutado (no llegó la respuesta): ${plan.maybe.join(", ")}.`,
    );
  }
  lines.push(
    "No repitas ninguna de esas habilidades sin antes revisar el estado del robot. Seguí desde donde quedaste.",
  );
  return lines.join(" ");
}
