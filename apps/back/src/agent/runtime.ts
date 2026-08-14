/**
 * Internal agent runtime — the backend drives the robot via Claude.
 *
 * SPEC §12.1 "internal agent" path: instead of an external MCP client (Claude
 * Code over stdio) deciding which skills to call, `apps/back` hosts the
 * conversation. We expose the skill registry as tools to Claude; each tool call
 * is dispatched to the Python bridge over MCP/HTTP via `callTool`, the result is
 * fed back, and the loop continues until Claude stops calling tools.
 *
 * SDK: the Vercel AI SDK (`ai` + `@ai-sdk/anthropic`). Chosen over the bare
 * Anthropic SDK because Elysia streams an AI SDK result straight from a route
 * (`toUIMessageStreamResponse()`) and `@ai-sdk/svelte`'s `useChat` consumes that
 * same wire format on the SvelteKit console — one stack for agent-loop + token
 * streaming + UI. Our skills are TypeBox (JSON Schema at runtime), so they map
 * onto AI SDK tools via `jsonSchema()` with no conversion.
 */

import { anthropic } from "@ai-sdk/anthropic";
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

const MODEL = process.env.AGENT_MODEL ?? "claude-opus-5";
const MAX_STEPS = Number(process.env.AGENT_MAX_STEPS ?? "12");

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

/** A compact catalogue appended to the system prompt so Claude knows scope.
 * Exported for testing — pure string generation once the catalogue is in hand. */
export async function buildSystemPrompt(): Promise<string> {
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
  return `${SYSTEM_PREAMBLE}\n\nAvailable skills:\n${lines.join("\n")}`;
}

/**
 * Build AI SDK tools from the TypeBox skill registry, each dispatching to the
 * bridge and writing an audit row.
 *
 * Auditing happens here rather than by scraping tool parts off the finished
 * message for two reasons: this is the only place with a real duration, and it
 * still records the call if the stream is aborted mid-turn — which is exactly
 * when you most want to know what the robot was told to do.
 */
async function buildTools(chatId: string | null): Promise<ToolSet> {
  const tools: ToolSet = {};
  for (const skill of await listSkills()) {
    tools[skill.name] = tool({
      description: skill.description,
      // TypeBox `t.Object({...})` is a JSON-Schema object at runtime.
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

// Adaptive thinking is a 4.6+ feature (Opus 4.6/4.7/4.8/5, Sonnet 4.6/5) and is
// rejected by Haiku 4.5, so gate it on the model. Force off with AGENT_THINKING=off.
// On Opus 5 thinking is on by default, so this is belt-and-braces there rather
// than load-bearing — but keep it explicit so the setting survives a model swap.
const ADAPTIVE_THINKING =
  (process.env.AGENT_THINKING ?? "adaptive") === "adaptive" &&
  !MODEL.includes("haiku");

/**
 * Run an operator chat turn as a streaming result. `messages` is the UIMessage
 * history from the chat client. The caller returns
 * `result.toUIMessageStreamResponse()` from the Elysia route; Elysia streams it
 * to the client and `@ai-sdk/svelte`'s Chat renders tokens + tool calls live.
 */
export async function runAgentChat(
  messages: UIMessage[],
  opts: { chatId?: string | null } = {},
) {
  // Both hit the bridge for the catalogue, so fetch them together rather than
  // paying two sequential round-trips before the first token.
  const [system, tools] = await Promise.all([
    buildSystemPrompt(),
    buildTools(opts.chatId ?? null),
  ]);

  return streamText({
    model: anthropic(MODEL),
    system,
    messages: await convertToModelMessages(messages),
    tools,
    stopWhen: stepCountIs(MAX_STEPS),
    ...(ADAPTIVE_THINKING
      ? { providerOptions: { anthropic: { thinking: { type: "adaptive" } } } }
      : {}),
  });
}
