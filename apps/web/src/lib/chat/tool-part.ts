/**
 * What a tool card in /chat should SAY about one tool call.
 *
 * Two lies this exists to stop telling:
 *
 *  1. "Ejecutando", forever. A part only reaches `output-*` if the stream that
 *     carried it finished. After chat.stop(), a stream error, or a reload of a
 *     turn the backend saved while aborted (routes/agent.ts saves those on
 *     purpose — runtime.ts documents the stuck `input-available` parts it
 *     leaves), the part stays in `input-available` and the registry kept
 *     pulsing "Ejecutando" over a call that ended an hour ago. A part is only
 *     running when it is in the LAST message AND the chat is busy right now.
 *     Otherwise it was interrupted — and the robot may still have acted on it,
 *     because the bridge call is not cancelled with the HTTP stream.
 *
 *  2. A green "Completado" for anything that came back. The bridge never
 *     raises: a walk that timed out short of its goal, a cancelled task, a
 *     bring-up step the firmware acked and ignored, a stub that only logged —
 *     all arrive as successful tool results. `classifySkillResult` is the one
 *     reading of that shape shared with /vr-control and the e-stop, so the same
 *     result cannot be a success here and a failure there.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import { classifySkillResult } from "../robot/skill-outcome";

/** The slice of an AI SDK tool UI part this module reads. */
export type ToolPartLike = {
  type: string;
  state: string;
  toolName?: string;
  input?: unknown;
  output?: unknown;
  errorText?: string;
};

/**
 * Badge colour. `running` is the only animated one; `neutral` is "sent, nobody
 * checked" and must read as neither success nor failure.
 */
export type ToolTone = "running" | "ok" | "warn" | "error" | "neutral";

export type ToolDisplay = {
  /** Badge text, Spanish. */
  label: string;
  tone: ToolTone;
  /** A sentence for the top of the card body, Spanish. */
  note: string | null;
  /** The bridge's own words (often English), shown verbatim under the note. */
  detail: string | null;
  /** Failure sentence, Spanish — rendered as the error block. */
  errorText: string | null;
  /** Result to render as JSON; undefined when there is nothing worth showing. */
  output: unknown;
  /** The bridge says it only pretended (stub / logged_only). */
  simulated: boolean;
  /** Expanded on first render: running now, or failed. */
  defaultOpen: boolean;
};

/** `tool-walk_to` → `walk_to`; dynamic tools carry the name separately. */
export function toolName(part: { type: string; toolName?: string }): string {
  if (part.type === "dynamic-tool") return part.toolName ?? "herramienta";
  return part.type.startsWith("tool-") ? part.type.slice(5) : part.type;
}

export function isToolPart(part: { type: string }): boolean {
  return part.type.startsWith("tool-") || part.type === "dynamic-tool";
}

/** States in which the call has been handed out but has not come back. */
const IN_FLIGHT = new Set([
  "input-streaming",
  "input-available",
  "approval-responded",
]);

export const INTERRUPTED_NOTE = "Puede haberse ejecutado en el robot.";

const base = (
  over: Partial<ToolDisplay> & Pick<ToolDisplay, "label" | "tone">,
): ToolDisplay => ({
  note: null,
  detail: null,
  errorText: null,
  output: undefined,
  simulated: false,
  defaultOpen: false,
  ...over,
});

/**
 * runtime.ts catches every bridge failure and returns `{error: detail}` as a
 * normal tool result, so the agent can read it and recover instead of the
 * stream aborting. That wrapper — and only that wrapper — is recognised here;
 * a full bridge body that happens to carry an `error` key goes through
 * `classifySkillResult`, which knows the firmware codes.
 */
function runtimeFailure(output: unknown): string | null {
  if (!output || typeof output !== "object" || Array.isArray(output)) {
    return null;
  }
  const keys = Object.keys(output);
  if (keys.length !== 1 || keys[0] !== "error") return null;
  const { error } = output as { error: unknown };
  return typeof error === "string" && error.length > 0 ? error : null;
}

/**
 * Classify one tool part for display.
 *
 * `live` is true only for parts of the last message while the chat is busy —
 * the caller computes it, because only the caller knows both.
 */
export function classifyToolPart(
  part: ToolPartLike,
  { live }: { live: boolean },
): ToolDisplay {
  const { state } = part;

  if (state === "approval-requested") {
    // Waiting on the operator, not on the stream — the chat reads "ready" while
    // an approval is pending, so this is never "interrupted".
    return base({
      label: "Requiere aprobación",
      tone: "warn",
      defaultOpen: true,
    });
  }

  if (IN_FLIGHT.has(state)) {
    if (live) {
      return base({
        label: state === "input-streaming" ? "Preparando" : "Ejecutando",
        tone: "running",
        // Open while it drives the robot, so the operator sees which skill is
        // moving it right now; not while its arguments are still arriving.
        defaultOpen: state !== "input-streaming",
      });
    }
    return base({
      label: "Interrumpido",
      tone: "warn",
      note: INTERRUPTED_NOTE,
    });
  }

  if (state === "output-denied") {
    return base({
      label: "Rechazado",
      tone: "warn",
      note: "No se ejecutó: la aprobación fue rechazada.",
    });
  }

  if (state === "output-error") {
    return base({
      label: "Error",
      tone: "error",
      errorText: "La herramienta devolvió un error.",
      detail: part.errorText || null,
      defaultOpen: true,
    });
  }

  if (state === "output-available") {
    const wrapped = runtimeFailure(part.output);
    if (wrapped !== null) {
      if (wrapped === "bridge_unavailable") {
        return base({
          label: "Error",
          tone: "error",
          errorText: "El puente no respondió.",
          detail: wrapped,
          defaultOpen: true,
        });
      }
      const toolError = /^tool_error:\s*/.exec(wrapped);
      return base({
        label: "Error",
        tone: "error",
        errorText: toolError ? "El puente devolvió un error." : "Falló.",
        detail: toolError ? wrapped.slice(toolError[0].length) : wrapped,
        defaultOpen: true,
      });
    }

    const outcome = classifySkillResult(part.output);
    const shared = {
      output: part.output,
      simulated: outcome.simulated,
    };
    switch (outcome.kind) {
      case "error":
        return base({
          ...shared,
          label: "Error",
          tone: "error",
          errorText: outcome.text,
          detail: outcome.detail ?? outcome.code,
          defaultOpen: true,
        });
      case "warn":
        return base({
          ...shared,
          label: outcome.text,
          tone: "warn",
          detail: outcome.detail,
        });
      case "unverified":
        return base({
          ...shared,
          label: outcome.text,
          tone: "neutral",
          detail: outcome.detail,
        });
      case "ok":
        return base({
          ...shared,
          label: outcome.text,
          tone: "ok",
          detail: outcome.detail,
        });
    }
  }

  // A state this console has never seen. Say so rather than guess.
  return base({ label: state, tone: "neutral" });
}
