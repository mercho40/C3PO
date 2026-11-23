/**
 * The one transport to the backend's internal agent, `POST {API}/agent`, which
 * streams the model's tokens and tool calls back as a UI message stream and
 * persists both sides of the turn.
 *
 * Shared by /chat (typed turns) and the headset's «Hablar con el robot»
 * (`vr-voice.svelte.ts`, spoken turns). They used to build it twice, with the
 * same 401 handling copied into each.
 *
 * A generic "Failed to fetch"-style error from the AI SDK doesn't tell an
 * operator whether their session expired or the robot link is down — so a 401
 * is caught HERE, before the SDK's own error handling folds every failure
 * into one opaque `new Error(responseBody)`, and the operator is sent to log in
 * again and brought back, instead of being offered a retry that would just
 * fail the same way. The SDK's folding also loses the HTTP status, which is
 * why `onStatus` reports it: a bare "Conflict" from a read-only voice chat
 * (409) and a gateway that is down (502) need different advice.
 */

import { DefaultChatTransport, type UIMessage } from "ai";
import { PUBLIC_API_URL } from "$app/env/public";
import { redirectToLogin } from "#lib/robot/poller.svelte.js";

export type AgentTransportOptions = {
  /**
   * `"voice"` for a spoken turn: back applies the voice exclusions (damp,
   * high danger). Omitted for a typed one.
   */
  mode?: "voice";
  /**
   * Each POST /agent's HTTP status: `null` as a request goes out, then the
   * status it answered with. Called before the 401 redirect.
   */
  onStatus?: (status: number | null) => void;
};

export function agentTransport(
  opts: AgentTransportOptions = {},
): DefaultChatTransport<UIMessage> {
  return new DefaultChatTransport({
    api: `${PUBLIC_API_URL}/agent`,
    credentials: "include",
    ...(opts.mode ? { body: { mode: opts.mode } } : {}),
    fetch: async (input, init) => {
      opts.onStatus?.(null);
      const res = await fetch(input, init);
      opts.onStatus?.(res.status);
      if (res.status === 401) redirectToLogin("expired");
      return res;
    },
  });
}
