import WebSocket, { type RawData } from "ws";
import { createIdGenerator } from "ai";

import type { Confirmations } from "#back/agent/confirmations.js";
import type { AgentMode, OfferedSkill } from "#back/agent/policy.js";
import type { AgentRuntime } from "#back/agent/runtime.js";
import type { BackTool } from "#back/agent/tools.js";
import { rethrowCause, type BridgeHttp } from "#back/bridge/http.js";
import type { Config } from "#back/config.js";
import type { ChatStore, LogToolCall } from "#back/db/chats.js";
import type { Dispatcher } from "#back/skills/dispatch.js";
import type { Catalogue, SkillCatalogueEntry } from "#back/skills/index.js";
import { validateArgs } from "#back/skills/validate.js";
import { concatBytes, Pcm16Resampler } from "./pcm";

const messageId = createIdGenerator({ prefix: "msg", size: 16 });
const OUTPUT_BATCH_BYTES = 6_400; // 200 ms at the robot's 16 kHz PCM16 format.

export type RealtimeVoiceState = {
  running: boolean;
  phase: "idle" | "connecting" | "listening" | "speaking" | "error";
  chatId: string | null;
  ownerId: string | null;
  lastHeard: string | null;
  lastReply: string | null;
  lastError: string | null;
  micEverOpen: boolean;
  utterancesHeard: number;
  agentRuns: number;
  toolCalls: number;
};

type StartOptions = { chatId: string; ownerId: string };

type RealtimeEvent = {
  type?: string;
  transcript?: string;
  delta?: string;
  name?: string;
  arguments?: string;
  call_id?: string;
  error?: RealtimeError;
  response?: { status?: string; status_details?: unknown };
};

type RealtimeError = { type?: string; code?: string; message?: string };

/**
 * Error codes that reject one client event and leave the session usable.
 * `response.create` goes out after every tool result and every operator
 * decision while semantic VAD creates responses of its own, so a collision
 * with an active response is routine; a cancel can arrive after its response
 * ended; a rate limit fails a response, not the socket. Anything not listed
 * stays fatal: a session we think is alive but is not would leave the operator
 * talking to nothing. And a misjudged entry still ends the session when the
 * server closes the socket (the `close` handler).
 */
const RECOVERABLE_ERROR_CODES: ReadonlySet<string> = new Set([
  "conversation_already_has_active_response",
  "response_cancel_not_active",
  "input_audio_buffer_commit_empty",
  "rate_limit_exceeded",
]);

export function isRecoverableRealtimeError(
  error: RealtimeError | undefined,
): boolean {
  return error?.code !== undefined && RECOVERABLE_ERROR_CODES.has(error.code);
}

type RealtimeSkill = SkillCatalogueEntry;

/** What the session declares: the skills voice is offered, and back's own tools. */
export function realtimeTools(
  skills: readonly RealtimeSkill[],
  offered: (skill: OfferedSkill, mode: AgentMode) => boolean,
  backTools: readonly Pick<BackTool, "def">[],
) {
  const bridgeTools = skills
    .filter((skill) => offered(skill, "voice"))
    .map((skill) => ({
      type: "function" as const,
      name: skill.name,
      description: skill.description,
      parameters: skill.parameters as Record<string, unknown>,
    }));
  // look and verify run here in back, not on the bridge — see agent/look.ts.
  // The frame goes to the agent's vision model, never into the Realtime session.
  return [
    ...bridgeTools,
    ...backTools.map(({ def }) => ({
      type: "function" as const,
      name: def.name,
      description: def.description,
      parameters: def.parameters as Record<string, unknown>,
    })),
  ];
}

/**
 * Why a call's arguments do not fit the schema this session declared, or null.
 * Realtime does not enforce it: on 2026-11-16 walk_to got `target_x_meters_world`
 * (copied from get_state) for `target_x_meters_world_frame`. Unknown names are
 * checked by hand because pydantic's schemas do not set `additionalProperties`.
 */
export function argumentProblem(
  name: string,
  schema: unknown,
  args: Record<string, unknown>,
): string | null {
  const s = (schema ?? {}) as {
    properties?: Record<string, unknown>;
    required?: string[];
  };
  const accepted = Object.keys(s.properties ?? {});
  const unknown = Object.keys(args).filter((k) => !accepted.includes(k));
  const checked = validateArgs(schema ?? {}, args);
  if (!unknown.length && checked.ok) return null;
  const missing = (s.required ?? []).filter((k) => !(k in args));
  return [
    `invalid_arguments: nothing was sent to the robot.`,
    unknown.length && `Unknown parameters: ${unknown.join(", ")}.`,
    missing.length && `Missing required: ${missing.join(", ")}.`,
    !checked.ok &&
      `Problems: ${checked.issues.map((i) => `${i.path} ${i.message}`).join("; ")}.`,
    `${name} accepts exactly: ${accepted.join(", ") || "no parameters"}.`,
    `Call ${name} again now with these exact names.`,
  ]
    .filter(Boolean)
    .join(" ");
}

export function realtimeSessionUpdate(
  instructions: string,
  tools: ReturnType<typeof realtimeTools>,
  options: { model: string; voice: string },
) {
  return {
    type: "session.update" as const,
    session: {
      type: "realtime" as const,
      model: options.model,
      output_modalities: ["audio"],
      instructions: `${instructions}\n\nRealtime voice safety: high-danger tools are intentionally unavailable. Ask the operator to use the authenticated console for those actions.`,
      audio: {
        input: {
          format: { type: "audio/pcm", rate: 24_000 },
          transcription: { model: "gpt-4o-mini-transcribe", language: "es" },
          turn_detection: {
            type: "semantic_vad",
            eagerness: "auto",
            create_response: true,
            interrupt_response: true,
          },
        },
        output: {
          format: { type: "audio/pcm", rate: 24_000 },
          voice: options.voice,
        },
      },
      tools,
      tool_choice: "auto",
    },
  };
}

class RobotAudioSink {
  constructor(private readonly http: Pick<BridgeHttp, "request">) {}

  private resampler = new Pcm16Resampler(24_000, 16_000);
  private pending: Uint8Array<ArrayBufferLike> = new Uint8Array();
  private tail: Promise<void> = Promise.resolve();
  private epoch = 0;
  private streamId = crypto.randomUUID();

  enqueue(base64: string): void {
    const bytes = Uint8Array.from(Buffer.from(base64, "base64"));
    this.pending = concatBytes(this.pending, this.resampler.push(bytes));
    while (this.pending.byteLength >= OUTPUT_BATCH_BYTES) {
      const chunk = this.pending.slice(0, OUTPUT_BATCH_BYTES);
      this.pending = this.pending.slice(OUTPUT_BATCH_BYTES);
      this.queue(chunk, this.epoch);
    }
  }

  finish(): Promise<void> {
    if (this.pending.byteLength > 0) {
      const chunk = this.pending;
      this.pending = new Uint8Array();
      this.queue(chunk, this.epoch);
    }
    return this.tail;
  }

  /** `robot: false` drops only our queued audio: the robot's stop would also cut a
   *  tool's motion warning, and the bridge then refuses the motion. */
  interrupt(robot = true): Promise<void> {
    this.epoch += 1;
    this.pending = new Uint8Array();
    this.resampler = new Pcm16Resampler(24_000, 16_000);
    this.streamId = crypto.randomUUID();
    if (!robot) return this.tail;
    this.tail = this.tail
      .catch(() => {})
      .then(async () => {
        await this.http
          .request("/telemetry/voice/audio/output", { method: "DELETE" })
          .catch(() => {});
      });
    return this.tail;
  }

  private queue(chunk: Uint8Array, epoch: number): void {
    const streamId = this.streamId;
    this.tail = this.tail.then(async () => {
      if (epoch !== this.epoch) return;
      const response = await this.http
        .request("/telemetry/voice/audio/output", {
          method: "POST",
          query: { stream_id: streamId },
          headers: { "Content-Type": "application/octet-stream" },
          body: Buffer.from(chunk),
        })
        .catch(rethrowCause);
      if (!response.ok)
        throw new Error(`robot audio output failed (${response.status})`);
      const result = (await response.json()) as {
        status?: string;
        rpc_code?: number;
      };
      if (result.status !== "ok") {
        throw new Error(
          `robot rejected audio output (rpc ${result.rpc_code ?? "unknown"})`,
        );
      }
    });
    // Marks this link handled so a failed POST cannot crash the process (and
    // console PARAR with it); finish() still rejects for whoever awaits it.
    this.tail.catch(() => {});
  }
}

export type RealtimeDeps = {
  config: Config["realtime"];
  catalogue: Pick<Catalogue, "listSkills">;
  agent: Pick<AgentRuntime, "systemPrompt" | "policy" | "backTools">;
  dispatch: Pick<Dispatcher, "dispatchSkill">;
  chats: Pick<ChatStore, "appendMessage">;
  logToolCall: LogToolCall;
  confirmations: Pick<Confirmations, "note">;
  http: Pick<BridgeHttp, "request">;
};

/** One operator-owned speech-to-speech session. Typed `/agent` is untouched. */
export class RealtimeVoiceSession {
  private socket: WebSocket | null = null;
  private inputAbort: AbortController | null = null;
  private audio: RobotAudioSink;
  private toolsRunning = 0;
  /**
   * The tool names this session DECLARED to the model. A function call naming
   * anything else is refused here: what was declared to OpenAI is a request,
   * not an enforcement, and `executeTool` used to dispatch whatever name came
   * back — including a high-danger skill the declaration had left out.
   */
  private declaredTools: ReadonlyMap<string, unknown> = new Map();
  private eventTail: Promise<void> = Promise.resolve();
  private state: RealtimeVoiceState = {
    running: false,
    phase: "idle",
    chatId: null,
    ownerId: null,
    lastHeard: null,
    lastReply: null,
    lastError: null,
    micEverOpen: false,
    utterancesHeard: 0,
    agentRuns: 0,
    toolCalls: 0,
  };

  constructor(private readonly deps: RealtimeDeps) {
    this.audio = new RobotAudioSink(deps.http);
  }

  snapshot(): RealtimeVoiceState {
    return { ...this.state };
  }

  async start(options: StartOptions): Promise<void> {
    if (this.state.running) {
      if (this.state.ownerId !== options.ownerId)
        throw new Error("voice session is owned by another operator");
      return;
    }
    const apiKey = this.deps.config.apiKey;
    if (!apiKey) throw new Error("OPENAI_API_KEY is not configured");

    this.state = {
      running: true,
      phase: "connecting",
      chatId: options.chatId,
      ownerId: options.ownerId,
      lastHeard: null,
      lastReply: null,
      lastError: null,
      micEverOpen: false,
      utterancesHeard: 0,
      agentRuns: 0,
      toolCalls: 0,
    };
    this.eventTail = Promise.resolve();
    this.audio = new RobotAudioSink(this.deps.http);

    try {
      // One catalogue for both, so the prompt describes the tools declared.
      const skills = await this.deps.catalogue.listSkills();
      // Empty means the bridge was unreachable with nothing cached: the session
      // would hear everything and could move nothing, not even stop.
      if (!skills.length)
        throw new Error(
          "robot tool catalogue unavailable (bridge unreachable): not starting voice",
        );
      const instructions = await this.deps.agent.systemPrompt("voice", skills);
      const url = new URL("wss://api.openai.com/v1/realtime");
      url.searchParams.set("model", this.deps.config.model);
      const socket = new WebSocket(url, {
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "OpenAI-Safety-Identifier": await safetyIdentifier(options.ownerId),
        },
      });
      this.socket = socket;
      await waitForOpen(socket);
      socket.on("message", (raw) => this.receive(raw));
      socket.on("error", (error) => this.fail(error));
      socket.on("close", () => {
        if (this.state.running)
          this.fail(new Error("OpenAI Realtime connection closed"));
      });

      const tools = realtimeTools(
        skills,
        this.deps.agent.policy.offered,
        this.deps.agent.backTools,
      );
      this.declaredTools = new Map(tools.map((t) => [t.name, t.parameters]));
      this.send(
        realtimeSessionUpdate(instructions, tools, {
          model: this.deps.config.model,
          voice: this.deps.config.voice,
        }),
      );
      console.log(
        `[voice/realtime] declared tools: ${[...this.declaredTools.keys()].join(", ")}`,
      );

      this.state.phase = "listening";
      this.inputAbort = new AbortController();
      void this.pumpRobotAudio(this.inputAbort.signal);
    } catch (error) {
      await this.stop();
      this.state.chatId = null;
      this.state.ownerId = null;
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.state.running = false;
    this.inputAbort?.abort();
    this.inputAbort = null;
    await this.audio.interrupt();
    this.socket?.close();
    this.socket = null;
    this.state.phase = "idle";
  }

  /**
   * An admin route already confirmed (or cancelled) a call this session asked
   * for: the model is told what happened. It decides nothing here.
   */
  informOutcome(chatId: string, message: string): void {
    if (!this.state.running || this.state.chatId !== chatId) return;
    this.send({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "system",
        content: [{ type: "input_text", text: message }],
      },
    });
    this.send({ type: "response.create" });
    // In the transcript too, so the voice chat shows what the operator decided.
    // Through eventTail, so it lands in order with the turn it answers.
    this.eventTail = this.eventTail.then(() =>
      this.deps.chats
        .appendMessage({
          id: messageId(),
          chatId,
          role: "system",
          parts: [{ type: "text", text: message }],
        })
        .then(() => undefined)
        .catch((error) =>
          console.error("[voice/realtime] outcome persistence failed", error),
        ),
    );
  }

  private send(event: unknown): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(event));
  }

  private async pumpRobotAudio(signal: AbortSignal): Promise<void> {
    try {
      const response = await this.deps.http
        .request("/telemetry/voice/audio/input", {
          headers: { Accept: "audio/pcm" },
          cache: "no-store",
          signal,
        })
        .catch(rethrowCause);
      if (!response.ok || !response.body)
        throw new Error(`robot audio input failed (${response.status})`);
      const reader = response.body.getReader();
      const resampler = new Pcm16Resampler(16_000, 24_000);
      try {
        while (!signal.aborted) {
          const { done, value } = await reader.read();
          if (done) throw new Error("robot audio input ended");
          if (value.byteLength > 0) this.state.micEverOpen = true;
          const audio = resampler.push(value);
          if (audio.byteLength > 0) {
            this.send({
              type: "input_audio_buffer.append",
              audio: Buffer.from(audio).toString("base64"),
            });
          }
        }
      } finally {
        reader.releaseLock();
      }
    } catch (error) {
      if (!signal.aborted) this.fail(error);
    }
  }

  private receive(raw: RawData): void {
    let event: RealtimeEvent;
    try {
      event = JSON.parse(raw.toString()) as RealtimeEvent;
    } catch {
      return;
    }

    // Audio control is latency-critical and must not queue behind a long robot
    // function call or database write. In particular, barge-in stops playback
    // immediately even while an earlier tool is still completing.
    if (event.type === "input_audio_buffer.speech_started") {
      this.state.phase = "listening";
      // Any voice in a crowd triggers this; mid-tool it would cancel the wave (2026-11-16).
      void this.audio.interrupt(this.toolsRunning === 0);
      return;
    }
    if (event.type === "response.output_audio.delta") {
      if (event.delta) {
        this.state.phase = "speaking";
        this.audio.enqueue(event.delta);
      }
      return;
    }
    if (event.type === "response.output_audio.done") {
      void this.audio
        .finish()
        .then(() => {
          if (this.state.running) this.state.phase = "listening";
        })
        .catch((error) => this.fail(error));
      return;
    }
    if (event.type === "error") {
      if (isRecoverableRealtimeError(event.error)) {
        console.warn("[voice/realtime] recoverable error", event.error);
        return;
      }
      this.fail(new Error(event.error?.message ?? "OpenAI Realtime error"));
      return;
    }

    // Transcript and tool effects do need ordering: appendMessage assigns its
    // sequence at write time, and robot functions should not overlap by accident.
    this.eventTail = this.eventTail
      .then(() => this.handleOrdered(event))
      .catch((error) => this.fail(error));
  }

  private async handleOrdered(event: RealtimeEvent): Promise<void> {
    switch (event.type) {
      case "conversation.item.input_audio_transcription.completed":
        await this.persistTranscript("user", event.transcript);
        break;
      case "response.output_audio_transcript.done":
        await this.persistTranscript("assistant", event.transcript);
        break;
      case "response.function_call_arguments.done":
        await this.executeTool(event);
        break;
      // Heard but did nothing (2026-11-16): a response cut by noise (barge-in,
      // `turn_detected`) ends here, before any function call.
      case "response.done":
        if (event.response?.status !== "completed")
          console.warn(
            `[voice/realtime] response ${event.response?.status}`,
            JSON.stringify(event.response?.status_details),
          );
        break;
    }
  }

  private async persistTranscript(
    role: "user" | "assistant",
    transcript?: string,
  ): Promise<void> {
    const text = transcript?.trim();
    const chatId = this.state.chatId;
    if (!text || !chatId) return;
    if (role === "user") {
      this.state.lastHeard = text;
      this.state.utterancesHeard += 1;
    } else {
      this.state.lastReply = text;
      this.state.agentRuns += 1;
    }
    await this.deps.chats
      .appendMessage({
        id: messageId(),
        chatId,
        role,
        parts: [{ type: "text", text }],
      })
      .catch((error) =>
        console.error("[voice/realtime] transcript persistence failed", error),
      );
  }

  private async executeTool(event: RealtimeEvent): Promise<void> {
    const name = event.name;
    const callId = event.call_id;
    const chatId = this.state.chatId;
    if (!name || !callId || !chatId) return;
    let args: Record<string, unknown> = {};
    try {
      // `?? {}`: a "null" argument string must not throw in the schema check.
      args = (JSON.parse(event.arguments || "{}") ?? {}) as Record<
        string,
        unknown
      >;
    } catch {
      args = {};
    }

    const started = performance.now();
    let result: unknown;
    let status: "ok" | "error" = "ok";
    let error: string | null = null;
    let dispatched = false;
    // Either refusal sends the robot nothing, so neither counts as running.
    const refused = this.declaredTools.has(name)
      ? argumentProblem(name, this.declaredTools.get(name), args)
      : `tool_not_offered: ${name}`;
    if (refused) {
      status = "error";
      error = refused;
      result = { error };
    } else {
      // Only a dispatched call counts; `finally`, so a throw in the catch
      // below cannot leave barge-in sparing the robot's speech for good.
      this.toolsRunning += 1;
      try {
        const back = this.deps.agent.backTools.find((t) => t.def.name === name);
        if (back) {
          result = await back.run(args);
          if ((result as { status?: string }).status === "error") {
            status = "error";
            error = (result as { error?: string }).error ?? `${name} failed`;
          }
        } else {
          dispatched = true;
          const ownerId = this.state.ownerId;
          const r = await this.deps.dispatch.dispatchSkill(name, args, {
            // Explicit rather than resolved from the chat: the session owner
            // is who started the microphone, and is known right here.
            who: { chatId, userId: ownerId, source: "voice" },
            // Noted for the transcript's Confirmar; the realtime model cannot confirm.
            onRefused: () => {
              if (ownerId)
                this.deps.confirmations.note({
                  id: callId,
                  tool: name,
                  args,
                  ownerId,
                  chatId,
                  loop: false,
                });
            },
          });
          if (r.ok) result = r.result;
          else {
            status = "error";
            // The robot's own reason, not just "tool_error": it is what the
            // model needs to say what went wrong.
            const f = r.failure;
            error =
              f.error === "tool_error"
                ? `tool_error: ${name}: ${f.detail}`
                : f.error;
            result = { error };
          }
        }
      } catch (cause) {
        status = "error";
        error = cause instanceof Error ? cause.message : String(cause);
        result = { error };
      } finally {
        this.toolsRunning -= 1;
      }
    }
    this.state.toolCalls += 1;
    // A bridge skill's row is `dispatchSkill`'s. Back's own tools and the
    // refusals above are logged here, fire-and-forget like the agent's:
    // `logToolCall` never throws, and awaiting it made the model wait on a
    // database write before it could hear the tool's result and answer the
    // person standing at the robot.
    if (!dispatched)
      void this.deps.logToolCall({
        chatId,
        userId: this.state.ownerId,
        source: "voice",
        skillName: name,
        params: args,
        result,
        status,
        error,
        durationMs: Math.round(performance.now() - started),
      });

    await this.deps.chats
      .appendMessage({
        id: messageId(),
        chatId,
        role: "assistant",
        parts: [
          {
            type: "dynamic-tool",
            toolName: name,
            toolCallId: callId,
            state: "output-available",
            input: args,
            output: result,
          },
        ],
      })
      .catch((cause) =>
        console.error("[voice/realtime] tool persistence failed", cause),
      );
    this.send({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(result ?? null),
      },
    });
    this.send({ type: "response.create" });
  }

  private fail(cause: unknown): void {
    const error = cause instanceof Error ? cause : new Error(String(cause));
    this.state.running = false;
    this.state.lastError = error.message;
    this.state.phase = "error";
    this.inputAbort?.abort();
    this.inputAbort = null;
    void this.audio.interrupt();
    this.socket?.close();
    this.socket = null;
    console.error("[voice/realtime]", error);
  }
}

async function safetyIdentifier(userId: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(userId),
  );
  return Buffer.from(digest).toString("hex");
}

function waitForOpen(socket: WebSocket): Promise<void> {
  return new Promise((resolve, reject) => {
    const opened = () => {
      cleanup();
      resolve();
    };
    const failed = (error: Error) => {
      cleanup();
      reject(error);
    };
    const cleanup = () => {
      socket.off("open", opened);
      socket.off("error", failed);
    };
    socket.on("open", opened);
    socket.on("error", failed);
  });
}
