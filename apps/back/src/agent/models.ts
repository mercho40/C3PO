/**
 * The agent's language models: the gateway, its optional fallback, and the
 * vision model `look` and `verify` use. Built from `config.agent` once, by
 * createApp; tests hand the runtime models of their own instead.
 *
 * Provider: TIC AI (`https://proyectos-tic.ort.edu.ar/c3po/api/v1`), ORT's
 * OpenAI-compatible gateway in front of models running locally on campus.
 * Nothing here is TIC-specific beyond the defaults — point AGENT_BASE_URL /
 * AGENT_MODEL / AGENT_API_KEY somewhere else and any OpenAI-compatible server
 * works.
 *
 * Two traps this file exists to keep closed:
 *
 * 1. The AI SDK majors move together. `ai`, `@ai-sdk/openai-compatible` and
 *    `@ai-sdk/svelte` (apps/web) each carry a provider-specification version:
 *    `ai@7` speaks "v4" and refuses a "v3" model. Bump one without the others
 *    and the first request throws UnsupportedModelVersionError — a runtime
 *    failure that typechecks and compiles clean. npm publishes matching
 *    dist-tags (`ai-v6`, `ai-v5`) if a line ever has to be walked back.
 * 2. HTTPS only. Port 80 is administratively blocked from the campus Wi-Fi
 *    VLAN while 443 answers — the opposite of what this comment used to claim
 *    (commit 63c3149). Test both ports before calling the host unreachable,
 *    and keep the scheme in env so it can flip without a code change.
 *
 * And one thing to expect rather than fix: the gateway is not always up. Its
 * nginx front end answers 502 with an HTML body when the upstream drops; the
 * SDK retries twice and then fails the turn with "Failed after 3 attempts.
 * Last error: Bad Gateway" after ~6s (measured). The HTML stays in
 * `APICallError.responseBody` and never reaches the operator. Retries cover a
 * blip, not an outage.
 */

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModel } from "ai";

import type { AgentConfig } from "#back/config.js";
import { createFailover, type ProviderName } from "./failover";

export type AgentModels = {
  /** The agent's model `id`, failing over when a fallback is configured. */
  chatModel: (id: string) => LanguageModel;
  /** The agent's own model id (AGENT_MODEL). */
  model: string;
  /** The model `look` sends camera frames to. */
  visionModel: string;
  /** Null when there is a key; else what the operator is told to set. */
  missingKey: string | null;
  /** For /health: where the agent's next model call goes first. */
  activeProvider: () => ProviderName;
};

/**
 * Built once — cheap and stateless. Deliberately NOT validated here: a
 * missing key should fail the /agent turn that needs it, not take the whole
 * backend down at boot alongside /health, auth and /state. The check lives in
 * `runAgentChat` instead, and it has to: `createOpenAICompatible` accepts
 * `apiKey: undefined` silently, so without it the operator would get a bare
 * gateway 401 rather than the name of the variable to set.
 */
export function createAgentModels(config: AgentConfig): AgentModels {
  const provider = createOpenAICompatible({
    name: "tic-ai",
    baseURL: config.baseUrl,
    apiKey: config.apiKey ?? undefined, // sent as `Authorization: Bearer <key>`
  });

  // Optional second OpenAI-compatible provider (e.g. Gemini's
  // `.../v1beta/openai`) for when ia-tic is down. All three unset: no
  // failover, exactly as before. See `failover.ts` for what fails over.
  const failover = createFailover({ stickyMs: config.fallbackStickyMs });
  const fallback = config.fallback;
  const fallbackProvider = fallback
    ? createOpenAICompatible({
        name: "agent-fallback",
        baseURL: fallback.baseUrl,
        apiKey: fallback.apiKey,
      })
    : null;
  if (config.fallbackIncomplete)
    console.warn(
      "[agent] AGENT_FALLBACK_BASE_URL, AGENT_FALLBACK_MODEL and AGENT_FALLBACK_API_KEY must all be set; failover is off",
    );

  return {
    chatModel(id) {
      const primary = provider.chatModel(id);
      return fallbackProvider && fallback
        ? failover.wrap(primary, fallbackProvider.chatModel(fallback.model))
        : primary;
    },
    model: config.model,
    // Defaults to the agent's own model: `tic-chat` read a robot frame
    // correctly on 2026-10-27 (`scripts/smoke-vision.ts`), so no second
    // provider is needed. Same gateway and key — a vision model elsewhere
    // would be a provider change, not env.
    visionModel: config.visionModel,
    missingKey: config.apiKey
      ? null
      : `AGENT_API_KEY is not set — ${config.baseUrl} rejects unauthenticated requests.`,
    activeProvider: () => (fallbackProvider ? failover.active() : "primary"),
  };
}
