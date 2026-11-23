/**
 * Internal agent runtime — the backend drives the robot through an LLM.
 *
 * The "internal agent" driver (docs/ARCHITECTURE.md §3): instead of an external MCP client (Claude
 * Code over stdio) deciding which skills to call, `apps/back` hosts the
 * conversation. We expose the skill registry as tools to the model; each tool
 * call is dispatched to the Python bridge over MCP/HTTP (`dispatchSkill`), the
 * result is fed back, and the loop continues until the model stops calling
 * tools.
 *
 * SDK: the Vercel AI SDK (`ai` + `@ai-sdk/openai-compatible`). The SDK survived
 * the move off Anthropic unchanged, which is why it was chosen over a bare
 * vendor SDK: Elysia streams an AI SDK result straight from a route
 * (`toUIMessageStreamResponse()`) and `@ai-sdk/svelte`'s Chat consumes that same
 * wire format on the SvelteKit console — one stack for agent-loop + token
 * streaming + UI, and swapping the provider touches only agent/models.ts. The
 * skill catalogue arrives from the bridge over MCP as plain JSON Schema
 * (pydantic's output), so it maps onto AI SDK tools via `jsonSchema()` with no
 * conversion.
 *
 * The pieces, each its own module:
 *
 *   models.ts   the gateway, its fallback and the vision model (config)
 *   prompt.ts   the system prompt, the voice preamble, the world snapshot
 *   policy.ts   which skills each mode is offered
 *   tools.ts    the AI SDK tools: bridge skills and back's look / verify
 *   runtime.ts  this file: one turn, its budget, its abort and its log line
 */

import {
  convertToModelMessages,
  pruneMessages,
  stepCountIs,
  streamText,
  type ModelMessage,
  type UIMessage,
} from "ai";

import type { AgentConfig } from "#back/config.js";
import type { BridgeMcp } from "#back/bridge/client.js";
import type { LogToolCall } from "#back/db/chats.js";
import type { Catalogue, SkillCatalogueEntry } from "#back/skills/index.js";
import type { Dispatcher } from "#back/skills/dispatch.js";
import type { Confirmations } from "./confirmations";
import type { LookDeps } from "./look";
import type { AgentModels } from "./models";
import { createToolPolicy, type AgentMode } from "./policy";
import { buildSystemPrompt, worldSummary } from "./prompt";
import { buildTools, createBackTools, type ToolDeps } from "./tools";
import type { Turns } from "./turns";

export type { AgentMode };

/**
 * The model's view of a chat: the last 40 UI messages, with tool calls and
 * results kept only for the last 10 model messages (old perception is stale
 * anyway). The stored chat is untouched. A confirmed T2 call is no longer
 * retried by the model (the console runs it and sends the result), but ten
 * still keeps the refused call beside that result message.
 * shortcut: count-based window, not tokens; upgrade to a token budget if the
 * gateway starts rejecting long chats.
 */
export async function boundHistory(
  messages: UIMessage[],
): Promise<ModelMessage[]> {
  return pruneMessages({
    // `ignoreIncompleteToolCalls` matters now that assistant turns really do
    // persist: an aborted turn leaves a tool part stuck in `input-available`,
    // and replaying that history would send the gateway an assistant tool
    // call with no matching tool result — which OpenAI-compatible servers
    // reject outright, permanently bricking the recovered conversation.
    messages: await convertToModelMessages(messages.slice(-40), {
      ignoreIncompleteToolCalls: true,
    }),
    reasoning: "before-last-message",
    toolCalls: "before-last-10-messages",
    emptyMessages: "remove",
  });
}

/**
 * The one stdout line a finished turn writes: counts and tool names, never
 * prompt, camera or speech text.
 * shortcut: stdout only; registerTelemetry(new OpenTelemetry()) from
 * @ai-sdk/otel when a collector exists.
 */
export function turnSummary(
  event: {
    steps: { toolCalls: { toolName: string }[] }[];
    finishReason: string;
    usage?: { inputTokens?: number; outputTokens?: number };
  },
  turn: {
    mode: AgentMode;
    chatId: string | null;
    startedAt: number;
    model: string;
  },
) {
  return {
    mode: turn.mode,
    chatId: turn.chatId,
    model: turn.model,
    steps: event.steps.length,
    finishReason: event.finishReason,
    // The gateway may send no usage at all.
    inputTokens: event.usage?.inputTokens ?? null,
    outputTokens: event.usage?.outputTokens ?? null,
    ms: Date.now() - turn.startedAt,
    tools: event.steps.flatMap((s) => s.toolCalls.map((c) => c.toolName)),
  };
}

export type AgentRuntimeDeps = {
  config: Pick<AgentConfig, "maxSteps" | "turnBudgetMs" | "tools">;
  models: AgentModels;
  catalogue: Pick<Catalogue, "listSkills">;
  bridge: Pick<BridgeMcp, "callTool">;
  /** The robot camera, for `look` and `verify` (agent/look.ts `bridgeCamera`). */
  camera: Pick<LookDeps, "cameraStatus" | "cameraFrame">;
  dispatch: Pick<Dispatcher, "dispatchSkill">;
  logToolCall: LogToolCall;
  confirmations: Pick<Confirmations, "note">;
  turns: Pick<Turns, "beginTurn" | "holdVoiceLoop">;
};

/** The agent, over its dependencies. One per process (createApp). */
export function createAgentRuntime(deps: AgentRuntimeDeps) {
  const { config, models } = deps;
  const policy = createToolPolicy(config.tools);
  const backTools = createBackTools({ models, camera: deps.camera });
  const toolDeps: ToolDeps = {
    catalogue: deps.catalogue,
    policy,
    backTools,
    dispatch: deps.dispatch,
    logToolCall: deps.logToolCall,
    confirmations: deps.confirmations,
  };

  /**
   * The system prompt for `mode`, over the live catalogue or `given` (one
   * catalogue for the realtime session's prompt and its declared tools).
   */
  async function systemPrompt(
    mode: AgentMode = "operator",
    given?: readonly SkillCatalogueEntry[],
  ): Promise<string> {
    return buildSystemPrompt(
      mode,
      given ?? (await deps.catalogue.listSkills()),
      policy,
      backTools.map((t) => t.promptLine),
    );
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
  async function runAgentChat(
    messages: UIMessage[],
    opts: {
      chatId?: string | null;
      mode?: AgentMode;
      /** Who this turn runs for, for the audit trail. See `buildTools`. */
      userId?: string | null;
      /**
       * Ends the agent loop when the operator stops the reply (or leaves the
       * page). Without it, "Detener respuesta" only closed the stream to the
       * browser: the loop kept running server-side and could go on dispatching
       * robot skills nobody was watching. A skill already in flight still
       * finishes — this stops the NEXT step, not the robot.
       */
      abortSignal?: AbortSignal;
      /** An operator's turn (the /agent route): the voice loop answers no
       *  overheard speech until it and its robot calls settle. See turns.ts. */
      holdVoiceLoop?: boolean;
    } = {},
  ) {
    if (models.missingKey) throw new Error(models.missingKey);

    // Both hit the bridge for the catalogue, so fetch them together rather than
    // paying two sequential round-trips before the first token.
    const mode = opts.mode ?? "operator";
    // Registered before the prompt build, so a PARAR during it still ends the turn.
    const { signal: abortSignal, done } = deps.turns.beginTurn(
      opts.abortSignal,
    );
    const releaseVoice = opts.holdVoiceLoop
      ? deps.turns.holdVoiceLoop()
      : () => {};
    const inFlight = new Set<Promise<unknown>>();
    const turn = {
      mode,
      chatId: opts.chatId ?? null,
      startedAt: Date.now(),
      model: models.model,
    };
    // onEnd never sees these turns, so they get their own line.
    const failed = (err: unknown) => {
      done();
      console.log(
        "[agent] turn",
        JSON.stringify({
          mode,
          chatId: turn.chatId,
          ms: Date.now() - turn.startedAt,
          error: String(err),
        }),
      );
    };
    const deadline = turn.startedAt + config.turnBudgetMs;
    try {
      const [basePrompt, world, tools] = await Promise.all([
        systemPrompt(mode),
        worldSummary(() => deps.bridge.callTool("describe_surroundings", {})),
        buildTools(
          toolDeps,
          {
            chatId: opts.chatId ?? null,
            // Spread only when given: undefined must stay undefined so
            // `logToolCall` falls back to the chat's owner.
            ...(opts.userId !== undefined ? { userId: opts.userId } : {}),
            source: mode === "voice" ? "voice" : "agent",
          },
          mode,
          inFlight,
          deadline,
        ),
      ]);
      const system = `${basePrompt}\n\n${world}`;
      // `steps` rejects with a generic NoOutputGeneratedError; the gateway's
      // own error reaches only onError.
      let streamError: unknown;

      const result = streamText({
        model: models.chatModel(models.model),
        system,
        messages: await boundHistory(messages),
        tools,
        // Whichever comes first: a step cap (never unbounded) or the wall clock.
        stopWhen: [stepCountIs(config.maxSteps), () => Date.now() > deadline],
        abortSignal,
        onEnd: (e) => {
          done();
          console.log("[agent] turn", JSON.stringify(turnSummary(e, turn)));
        },
        onAbort: done,
        onError: ({ error }) => {
          streamError = error;
          console.error(error); // the SDK's default, kept
        },
      });
      // ai 7.0.140 skips onEnd when the first step fails, so a gateway error
      // leaked the turn; `steps` settles on success, abort and error alike.
      // Trade-off: reading it drains a tee'd branch, so a client that stops
      // reading no longer pauses the loop; request.signal and PARAR still end it.
      void result.steps.then(done, (err) => failed(streamError ?? err));
      // After the steps, not with them: an aborted turn's robot call settles later.
      void Promise.resolve(result.steps)
        .catch(() => {})
        .then(() => Promise.allSettled(inFlight))
        .finally(releaseVoice);
      // Backstop: a stream nobody ends must not leave the voice loop deaf. Its
      // robot calls are cut at the deadline anyway.
      setTimeout(releaseVoice, config.turnBudgetMs + 30_000).unref?.();
      // An abort ends the text stream at once while a walk_to the turn already
      // sent keeps driving; the voice turn waits on this before it ends.
      return Object.assign(result, {
        toolsSettled: () => Promise.allSettled(inFlight),
      });
    } catch (err) {
      failed(err);
      releaseVoice();
      throw err;
    }
  }

  return {
    runAgentChat,
    systemPrompt,
    policy,
    backTools,
    /** For /health: where the agent's next model call goes first. */
    activeProvider: models.activeProvider,
  };
}

export type AgentRuntime = ReturnType<typeof createAgentRuntime>;
