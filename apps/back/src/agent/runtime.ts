/**
 * Internal agent runtime — the backend drives the robot through an LLM.
 *
 * The "internal agent" driver (docs/ARCHITECTURE.md §3): instead of an external MCP client (Claude
 * Code over stdio) deciding which skills to call, `apps/back` hosts the
 * conversation. We expose the skill registry as tools to the model; each tool
 * call is dispatched to the Python bridge over MCP/HTTP via `callTool`, the
 * result is fed back, and the loop continues until the model stops calling
 * tools.
 *
 * SDK: the Vercel AI SDK (`ai` + `@ai-sdk/openai-compatible`). The SDK survived
 * the move off Anthropic unchanged, which is why it was chosen over a bare
 * vendor SDK: Elysia streams an AI SDK result straight from a route
 * (`toUIMessageStreamResponse()`) and `@ai-sdk/svelte`'s Chat consumes that same
 * wire format on the SvelteKit console — one stack for agent-loop + token
 * streaming + UI, and swapping the provider touches only this file. The skill
 * catalogue arrives from the bridge over MCP as plain JSON Schema (pydantic's
 * output), so it maps onto AI SDK tools via `jsonSchema()` with no conversion.
 *
 * Provider: TIC AI (`http://ia.ort.edu.ar/api/v1`), ORT's OpenAI-compatible
 * gateway in front of models running locally on campus. Nothing here is
 * TIC-specific beyond the defaults — point AGENT_BASE_URL / AGENT_MODEL /
 * AGENT_API_KEY somewhere else and any OpenAI-compatible server works.
 *
 * Two traps this file exists to keep closed:
 *
 * 1. The AI SDK majors move together. `ai`, `@ai-sdk/openai-compatible` and
 *    `@ai-sdk/svelte` (apps/web) each carry a provider-specification version:
 *    `ai@7` speaks "v4" and refuses a "v3" model. Bump one without the others
 *    and the first request throws UnsupportedModelVersionError — a runtime
 *    failure that typechecks and compiles clean. npm publishes matching
 *    dist-tags (`ai-v6`, `ai-v5`) if a line ever has to be walked back.
 * 2. Plain HTTP. The TIC AI site documents an https:// base URL, but the host
 *    does not answer on 443 — only port 80. Keep the scheme in env so this can
 *    flip without a redeploy of the code.
 *
 * And one thing to expect rather than fix: the gateway is not always up. Its
 * nginx front end answers 502 with an HTML body when the upstream drops; the
 * SDK retries twice and then fails the turn with "Failed after 3 attempts.
 * Last error: Bad Gateway" after ~6s (measured). The HTML stays in
 * `APICallError.responseBody` and never reaches the operator. Retries cover a
 * blip, not an outage.
 */

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import {
  convertToModelMessages,
  jsonSchema,
  stepCountIs,
  streamText,
  tool,
  type ToolSet,
  type UIMessage,
} from "ai";

import { listSkills } from "@back/skills";
import { logToolCall } from "@back/db/chats";
import {
  callTool,
  BridgeToolError,
  BridgeUnavailableError,
} from "@back/bridge/client";

const BASE_URL = process.env.AGENT_BASE_URL ?? "http://ia.ort.edu.ar/api/v1";
/** `tic-chat` is the gateway's tool-calling chat model. Its site advertises
 *  `tic-code` and `tic-embed` too, but on 2026-08-18 `GET /models` returned only
 *  `tic-chat` and `tic-test` — trust the endpoint, not the brochure. */
const MODEL = process.env.AGENT_MODEL ?? "tic-chat";
// `stepCountIs` compares with `===`, so a cap of 0 or NaN is never reached and
// silently means "no cap at all" — an unbounded tool loop on a machine that can
// walk into people. `?? "12"` does not catch it: an empty AGENT_MAX_STEPS= in a
// systemd EnvironmentFile parses to 0, and a trailing comment to NaN.
const parsedMaxSteps = Number(process.env.AGENT_MAX_STEPS);
const MAX_STEPS =
  Number.isInteger(parsedMaxSteps) && parsedMaxSteps > 0 ? parsedMaxSteps : 12;

// Read once, so the provider and the guard in `runAgentChat` can never
// disagree about whether there is a key.
const API_KEY = process.env.AGENT_API_KEY;

/**
 * Built once at module load — cheap and stateless. Deliberately NOT validated
 * here: a missing key should fail the /agent turn that needs it, not take the
 * whole backend down at boot alongside /health, auth and /state. The check
 * lives in `runAgentChat` instead, and it has to: `createOpenAICompatible`
 * accepts `apiKey: undefined` silently, so without it the operator would get a
 * bare gateway 401 rather than the name of the variable to set.
 */
const provider = createOpenAICompatible({
  name: "tic-ai",
  baseURL: BASE_URL,
  apiKey: API_KEY, // sent as `Authorization: Bearer <key>`
});

const SYSTEM_PREAMBLE = [
  "You are C3PO, the control intelligence of a Unitree G1 humanoid robot.",
  "You drive the robot by calling the skill tools provided. Reason about the",
  "goal, call get_state when you need the current pose / posture / battery /",
  "faults, and sequence skills to accomplish what the operator asks.",
  "",
  // Deliberately not hardcoded. This preamble used to assert "the robot is
  // currently the Isaac Sim emulation", which silently became false the moment
  // the bridge was deployed onto real hardware — and an agent that believes
  // gestures produce no motion will under-report real motion to its operator.
  // The environment is a runtime fact, so read it at runtime.
  "Environment: call get_state and read `env` to learn which target you are",
  "driving — 'stub', 'isaac' (simulator) or 'real' (physical robot). Do this",
  "before your first motion command in a session, and never assume. Each skill's",
  "catalogue entry below marks where it works: a skill that does not support the",
  "current env will be constructed and logged but produce no motion.",
  "",
  "Report what actually happened, not what you intended. If a skill did not run",
  "on this target, say so plainly rather than describing motion that didn't",
  "occur — and equally, do not claim nothing happened when the robot did move.",
  "",
  "Safety: stop_everything halts all motion immediately and works on every",
  "target. Respect each skill's preconditions, and confirm intent before",
  "high-danger skills. On the physical robot, treat every motion command as",
  "having real physical consequence: a person may be standing next to it.",
  "Keep operator-facing replies concise: say what you did and what happened.",
].join("\n");

export type AgentMode = "operator" | "voice";

export function agentToolExclusions(mode: AgentMode): ReadonlySet<string> {
  // The voice host owns both ends of the audio channel. Giving these tools to
  // the model permits duplicate speech and competing reads from one buffer.
  return mode === "voice" ? new Set(["listen", "say"]) : new Set();
}

const VOICE_PREAMBLE = [
  "Voice session: you are having a spoken conversation, not interpreting every",
  "utterance as a robot command. Respond naturally in concise conversational",
  "Spanish and preserve context from earlier turns.",
  "",
  "Use robot tools only when the speaker clearly asks you to inspect, move,",
  "gesture, or perform another physical task. Questions, greetings, comments,",
  "and follow-ups normally need no tool. Never infer a motion request merely",
  "because motion tools are available.",
  "",
  "For a clear low- or medium-danger request, call the matching tool in this same",
  "turn; do not merely say that you will do it. Copy numbers, directions, names,",
  "and distances exactly from the transcript into tool arguments. Do not invent",
  "missing arguments. Ask one short clarifying question when physical intent or a",
  "required argument is ambiguous, and ask the speaker to repeat speech that is",
  "garbled or incoherent instead of guessing. After a tool call, describe only",
  "the result the tool actually returned.",
  "",
  "Return the exact words you want spoken as your final text. Do not call `say`",
  "or `listen`; the voice-session host owns listening and speaks your final text",
  "exactly once. Do not describe tool-call mechanics to the speaker.",
].join("\n");

/** A compact catalogue appended to the system prompt so the model knows scope.
 * Exported for testing — pure string generation once the catalogue is in hand. */
export async function buildSystemPrompt(mode: AgentMode = "operator"): Promise<string> {
  const lines = (await listSkills()).map((s) => {
    const where =
      s.works.sim && s.works.real
        ? "sim+real"
        : s.works.real
          ? "real-only"
          : s.works.sim
            ? "sim-only"
            : "unavailable";
    return `- ${s.name} [${s.classification}/${s.status}/${where}/danger:${s.dangerLevel}] — ${s.description}`;
  });
  const modePrompt = mode === "voice" ? `\n\n${VOICE_PREAMBLE}` : "";
  return `${SYSTEM_PREAMBLE}${modePrompt}\n\nAvailable skills:\n${lines.join("\n")}`;
}

/**
 * Build AI SDK tools from the bridge's skill catalogue, each dispatching back
 * to the bridge and writing an audit row.
 *
 * Auditing happens here rather than by scraping tool parts off the finished
 * message for two reasons: this is the only place with a real duration, and it
 * still records the call if the stream is aborted mid-turn — which is exactly
 * when you most want to know what the robot was told to do.
 */
async function buildTools(
  chatId: string | null,
  excluded: ReadonlySet<string> = new Set(),
): Promise<ToolSet> {
  const tools: ToolSet = {};
  for (const skill of await listSkills()) {
    if (excluded.has(skill.name)) continue;
    tools[skill.name] = tool({
      description: skill.description,
      // Already plain JSON Schema off the bridge — nothing to convert.
      inputSchema: jsonSchema(
        skill.parameters as Parameters<typeof jsonSchema>[0],
      ),
      execute: async (args) => {
        const params = args as Record<string, unknown>;
        const startedAt = Date.now();
        try {
          const result = await callTool(skill.name, params);
          void logToolCall({
            chatId,
            skillName: skill.name,
            params,
            result,
            status: "ok",
            durationMs: Date.now() - startedAt,
          });
          return result;
        } catch (err) {
          // Return the failure to the model as a normal tool result so the
          // agent can recover or report it, rather than aborting the stream.
          const detail =
            err instanceof BridgeToolError
              ? `tool_error: ${err.detail}`
              : err instanceof BridgeUnavailableError
                ? "bridge_unavailable"
                : String(err);
          void logToolCall({
            chatId,
            skillName: skill.name,
            params,
            status: "error",
            error: detail,
            durationMs: Date.now() - startedAt,
          });
          return { error: detail };
        }
      },
    });
  }
  return tools;
}

/**
 * Run an operator chat turn as a streaming result. `messages` is the UIMessage
 * history from the chat client. The caller returns
 * `result.toUIMessageStreamResponse()` from the Elysia route; Elysia streams it
 * to the client and `@ai-sdk/svelte`'s Chat renders tokens + tool calls live.
 *
 * Note there is no thinking/reasoning knob here any more. It used to pass
 * `providerOptions.anthropic.thinking = "adaptive"`, which is an Anthropic API
 * feature with no equivalent on a generic OpenAI-compatible gateway; a server
 * that emits `reasoning_content` still reaches the console as reasoning parts
 * without being asked.
 */
export async function runAgentChat(
  messages: UIMessage[],
  opts: { chatId?: string | null; mode?: AgentMode } = {},
) {
  if (!API_KEY) {
    throw new Error(
      `AGENT_API_KEY is not set — ${BASE_URL} rejects unauthenticated requests.`,
    );
  }

  // Both hit the bridge for the catalogue, so fetch them together rather than
  // paying two sequential round-trips before the first token.
  const mode = opts.mode ?? "operator";
  const excludedTools = agentToolExclusions(mode);
  const [system, tools] = await Promise.all([
    buildSystemPrompt(mode),
    buildTools(opts.chatId ?? null, excludedTools),
  ]);

  return streamText({
    model: provider.chatModel(MODEL),
    system,
    // `ignoreIncompleteToolCalls` matters now that assistant turns really do
    // persist: an aborted turn leaves a tool part stuck in `input-available`,
    // and replaying that history would send the gateway an assistant tool call
    // with no matching tool result — which OpenAI-compatible servers reject
    // outright, permanently bricking the recovered conversation.
    messages: await convertToModelMessages(messages, {
      ignoreIncompleteToolCalls: true,
    }),
    tools,
    stopWhen: stepCountIs(MAX_STEPS),
  });
}
