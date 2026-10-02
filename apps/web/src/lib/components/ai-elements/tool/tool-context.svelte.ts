export type ToolUIPartType = string;

/**
 * Mirrors the AI SDK's `ToolUIPart["state"]` union. The registry shipped only
 * the four non-approval states, which meant any part flowing straight out of
 * `chat.messages` failed to type-check.
 */
export type ToolUIPartState =
  | "input-streaming"
  | "input-available"
  | "approval-requested"
  | "approval-responded"
  | "output-available"
  | "output-error"
  | "output-denied";

export type ToolSchema = {
  type: ToolUIPartType;
  state: ToolUIPartState;
  input?: unknown;
  output?: unknown;
  errorText?: string;
  isOpen?: boolean;
};

// The registry also shipped a `ToolClass` + Svelte context here, but no
// component in the set ever read it — the parts come straight off
// `chat.messages`. It carried its own English status labels, and status
// wording now lives in `$lib/chat/tool-part.ts` (which reads the RESULT, not
// just the part state), so it is deliberately not vendored.
