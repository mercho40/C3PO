/**
 * The tools a turn's model can call: the bridge's skills (dispatched through
 * `dispatchSkill`, skills/dispatch.ts) and back's own `look` / `verify`.
 */

import { generateText, jsonSchema, tool, type ToolSet } from "ai";

import type { LogToolCall } from "#back/db/chats.js";
import type { Catalogue } from "#back/skills/index.js";
import {
  auditError,
  NEEDS_CONFIRMATION,
  type AuditWho,
  type Dispatcher,
} from "#back/skills/dispatch.js";
import type { Confirmations } from "./confirmations";
import {
  LOOK_PROMPT_LINE,
  LOOK_TOOL,
  VERIFY_PROMPT_LINE,
  VERIFY_TOOL,
  VISION_SYSTEM,
  look,
  verify,
  type LookDeps,
  type LookResult,
  type VerifyResult,
} from "./look";
import type { AgentModels } from "./models";
import { agentToolExclusions, type AgentMode, type ToolPolicy } from "./policy";

export { NEEDS_CONFIRMATION };

export type BackTool = {
  def: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
  run: (args: { question?: unknown }) => Promise<LookResult | VerifyResult>;
  promptLine: string;
};

/**
 * Tools that run here in back, not on the bridge: look through the robot's
 * camera and answer `question`, or check a yes/no fact. Shared by the typed
 * agent, the legacy voice loop (both via `buildTools`) and the Realtime
 * session, so every engine sees through the same path.
 */
export function createBackTools(deps: {
  models: Pick<AgentModels, "chatModel" | "visionModel" | "missingKey">;
  camera: Pick<LookDeps, "cameraStatus" | "cameraFrame">;
}): readonly BackTool[] {
  const { models } = deps;
  /** The robot's camera and the vision model, as `look` and `verify` use them. */
  const robotVision: LookDeps = {
    ...deps.camera,
    model: models.visionModel,
    describe: async (question, jpeg) => {
      const { text } = await generateText({
        model: models.chatModel(models.visionModel),
        system: VISION_SYSTEM,
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: question },
              { type: "file", data: jpeg, mediaType: "image/jpeg" },
            ],
          },
        ],
        // One retry covers a gateway blip; the agent loop is waiting on this.
        maxRetries: 1,
        abortSignal: AbortSignal.timeout(20_000),
      });
      return text;
    },
  };
  const noKey = <T>(): Promise<T> =>
    Promise.resolve({ status: "error", error: models.missingKey } as T);
  return [
    {
      def: LOOK_TOOL,
      run: (args) =>
        models.missingKey ? noKey<LookResult>() : look(args, robotVision),
      promptLine: LOOK_PROMPT_LINE,
    },
    {
      // A yes/no check on the camera view: `look`'s path, read as a verdict.
      def: VERIFY_TOOL,
      run: (args) =>
        models.missingKey ? noKey<VerifyResult>() : verify(args, robotVision),
      promptLine: VERIFY_PROMPT_LINE,
    },
  ];
}

/**
 * A walk that never started reports `arrived: true`: 2026-11-13 the model read
 * a 0.20 m walk_to (stop_distance 0.3) as moving closer and picked «mejor
 * posicionado». Say it in the result the model reads; the audit keeps the raw.
 */
export function toldNoEffect(result: unknown): unknown {
  const phase = (result as { phase?: unknown } | null)?.phase;
  return phase === "already_within_stop_distance"
    ? {
        ...(result as object),
        moved: false,
        note: "No motion: the target was already within stop_distance_m, so the robot did not move and is no closer than before.",
      }
    : result;
}

/** What the model is told instead of the bridge's "retry once" instruction. */
// shortcut: always offers the face, even with C3PO_FACE_CONFIRM=0 on the bridge (then the card shows no face line).
export const confirmRefusal = (tool: string) =>
  `refused: ${tool} ${NEEDS_CONFIRMATION} first. You cannot give it. The operator's «Confirmar», or an enrolled operator's face shown to the robot's head camera, runs this exact call and you will then get its result as a message: do not call ${tool} again, tell the operator in one sentence that you are waiting for their «Confirmar» on the screen or an operator's face at the camera and what will happen then (do not say you are doing it), say «Confirmá la acción o mostrale la cara de un operador a la cámara para confirmar», and wait.`;

export type ToolDeps = {
  catalogue: Pick<Catalogue, "listSkills">;
  policy: Pick<ToolPolicy, "offered">;
  backTools: readonly BackTool[];
  dispatch: Pick<Dispatcher, "dispatchSkill">;
  logToolCall: LogToolCall;
  confirmations: Pick<Confirmations, "note">;
};

/**
 * Build AI SDK tools from the bridge's skill catalogue, each dispatching back
 * to the bridge and writing an audit row.
 *
 * Auditing happens at dispatch rather than by scraping tool parts off the
 * finished message for two reasons: this is the only place with a real
 * duration, and it still records the call if the stream is aborted mid-turn —
 * which is exactly when you most want to know what the robot was told to do.
 *
 * `audit` says who and which path. `userId` undefined lets `logToolCall`
 * resolve the chat's owner; a chat-less voice turn has no chat to resolve
 * from, so its caller must pass the session owner explicitly.
 */
export async function buildTools(
  deps: ToolDeps,
  audit: AuditWho & { chatId: string | null },
  mode: AgentMode,
  inFlight: Set<Promise<unknown>>,
  deadline: number,
): Promise<ToolSet> {
  const tools: ToolSet = {};
  for (const skill of await deps.catalogue.listSkills()) {
    if (!deps.policy.offered(skill, mode)) continue;
    tools[skill.name] = tool({
      description: skill.description,
      // Already plain JSON Schema off the bridge — nothing to convert.
      inputSchema: jsonSchema(
        skill.parameters as Parameters<typeof jsonSchema>[0],
      ),
      execute: async (args, { abortSignal, toolCallId }) => {
        // ai 7.0.140 can still start a tool after the turn is aborted, once
        // `toolsSettled` has already been read: send the robot nothing then.
        if (abortSignal?.aborted) return { error: "aborted" };
        const params = args as Record<string, unknown>;
        // A turn that ends (PARAR, the budget, the client gone) cancels its
        // call: a 300 s walk_to outlived the 240 s budget. A stop runs out.
        const signal =
          skill.classification === "safety"
            ? undefined
            : AbortSignal.any([
                ...(abortSignal ? [abortSignal] : []),
                AbortSignal.timeout(Math.max(0, deadline - Date.now())),
              ]);
        let gated = false;
        const r = await deps.dispatch.dispatchSkill(skill.name, params, {
          who: audit,
          signal,
          track: (settled) => void inFlight.add(settled),
          // Noted, never granted: only an admin's Confirmar (a route) runs it.
          onRefused: () => {
            gated = true;
            if (audit.userId)
              deps.confirmations.note({
                id: toolCallId,
                tool: skill.name,
                args: params,
                ownerId: audit.userId,
                chatId: audit.chatId,
                loop: mode === "voice" && audit.chatId === null,
              });
          },
        });
        if (r.ok) return toldNoEffect(r.result);
        // The failure goes back to the model as a normal tool result so the
        // agent can recover or report it, rather than aborting the stream.
        // The bridge's text says "retry once"; here the console runs it.
        return gated
          ? { error: confirmRefusal(skill.name) }
          : { error: auditError(r.failure) };
      },
    });
  }
  for (const back of deps.backTools) {
    if (agentToolExclusions(mode).has(back.def.name)) continue;
    tools[back.def.name] = tool({
      description: back.def.description,
      inputSchema: jsonSchema(back.def.parameters),
      execute: async (args) => {
        const params = args as Record<string, unknown>;
        const startedAt = Date.now();
        const result = await back.run(params);
        void deps.logToolCall({
          ...audit,
          skillName: back.def.name,
          params,
          result,
          status: result.status === "error" ? "error" : "ok",
          error: result.status === "error" ? result.error : null,
          durationMs: Date.now() - startedAt,
          startedAt,
        });
        return result;
      },
    });
  }

  return tools;
}
