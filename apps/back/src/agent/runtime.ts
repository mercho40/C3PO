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
import { jsonSchema, stepCountIs, streamText, tool, type ToolSet } from "ai";

import { listSkills } from "@back/skills";
import {
  callTool,
  BridgeToolError,
  BridgeUnavailableError,
} from "@back/bridge/client";

const MODEL = process.env.AGENT_MODEL ?? "claude-opus-4-8";
const MAX_STEPS = Number(process.env.AGENT_MAX_STEPS ?? "12");

const SYSTEM_PREAMBLE = [
  "You are C3PO, the control intelligence of a Unitree G1 humanoid robot.",
  "You drive the robot by calling the skill tools provided. Reason about the",
  "goal, call get_state when you need the current pose / posture / battery /",
  "faults, and sequence skills to accomplish what the operator asks.",
  "",
  "Environment: the robot is currently the Isaac Sim emulation. Locomotion",
  "(walk_to, turn) and get_state are real; the high-level posture/gesture skills",
  "marked 'real-only' are constructed but produce NO motion in sim (logged only).",
  "Do not claim the robot moved when a real-only skill was called in sim.",
  "",
  "Safety: stop_everything halts all motion immediately. Respect each skill's",
  "preconditions, and prefer to confirm intent before high-danger skills.",
  "Keep operator-facing replies concise: say what you did and what happened.",
].join("\n");

/** A compact catalogue appended to the system prompt so Claude knows scope. */
function buildSystemPrompt(): string {
  const lines = listSkills().map((s) => {
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

/** Build AI SDK tools from the TypeBox skill registry, each dispatching to the bridge. */
function buildTools(): ToolSet {
  const tools: ToolSet = {};
  for (const skill of listSkills()) {
    tools[skill.name] = tool({
      description: skill.description,
      // TypeBox `t.Object({...})` is a JSON-Schema object at runtime.
      inputSchema: jsonSchema(
        skill.parameters as Parameters<typeof jsonSchema>[0],
      ),
      execute: async (args) => {
        try {
          return await callTool(skill.name, args as Record<string, unknown>);
        } catch (err) {
          // Return the failure to the model as a normal tool result so the
          // agent can recover or report it, rather than aborting the stream.
          const detail =
            err instanceof BridgeToolError
              ? `tool_error: ${err.detail}`
              : err instanceof BridgeUnavailableError
                ? "bridge_unavailable"
                : String(err);
          return { error: detail };
        }
      },
    });
  }
  return tools;
}

// Adaptive thinking is a 4.6+ feature (Opus 4.6/4.7/4.8, Sonnet 4.6) and is
// rejected by Haiku 4.5, so gate it on the model. Force off with AGENT_THINKING=off.
const ADAPTIVE_THINKING =
  (process.env.AGENT_THINKING ?? "adaptive") === "adaptive" &&
  !MODEL.includes("haiku");

/**
 * Run one operator turn as a streaming result. The caller returns
 * `result.toUIMessageStreamResponse()` from the Elysia route; Elysia streams it
 * to the client and `useChat` renders the tokens + tool calls live.
 */
export function runAgentStream(message: string) {
  return streamText({
    model: anthropic(MODEL),
    system: buildSystemPrompt(),
    prompt: message,
    tools: buildTools(),
    stopWhen: stepCountIs(MAX_STEPS),
    ...(ADAPTIVE_THINKING
      ? { providerOptions: { anthropic: { thinking: { type: "adaptive" } } } }
      : {}),
  });
}
