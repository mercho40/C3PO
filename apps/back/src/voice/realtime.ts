import WebSocket, { type RawData } from "ws";
import { createIdGenerator } from "ai";

import { callTool } from "@back/bridge/client";
import { bridgeSiblingUrl } from "@back/bridge/url";
import { appendMessage, logToolCall } from "@back/db/chats";
import { buildSystemPrompt } from "@back/agent/runtime";
import { listSkills } from "@back/skills";
import { concatBytes, Pcm16Resampler } from "./pcm";

const messageId = createIdGenerator({ prefix: "msg", size: 16 });
const MODEL = process.env.OPENAI_REALTIME_MODEL ?? "gpt-realtime-2.1";
const VOICE = process.env.OPENAI_REALTIME_VOICE ?? "marin";
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
  error?: { message?: string };
};

type RealtimeSkill = Awaited<ReturnType<typeof listSkills>>[number];

export function realtimeTools(skills: RealtimeSkill[]) {
  return skills
    .filter(
      (skill) =>
        skill.name !== "listen" &&
        skill.name !== "say" &&
        skill.dangerLevel !== "high",
    )
    .map((skill) => ({
      type: "function" as const,
      name: skill.name,
      description: skill.description,
      parameters: skill.parameters,
    }));
}

export function realtimeSessionUpdate(instructions: string, skills: RealtimeSkill[]) {
  return {
    type: "session.update" as const,
    session: {
      type: "realtime" as const,
      model: MODEL,
      output_modalities: ["audio"],
      instructions,
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
        output: { format: { type: "audio/pcm" }, voice: VOICE },
      },
      tools: realtimeTools(skills),
      tool_choice: "auto",
    },
  };
}

class RobotAudioSink {
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

  interrupt(): Promise<void> {
    this.epoch += 1;
    this.pending = new Uint8Array();
    this.resampler = new Pcm16Resampler(24_000, 16_000);
    this.streamId = crypto.randomUUID();
    this.tail = this.tail
      .catch(() => {})
      .then(async () => {
        await fetch(bridgeSiblingUrl("/telemetry/voice/audio/output"), {
          method: "DELETE",
        }).catch(() => {});
      });
    return this.tail;
  }

  private queue(chunk: Uint8Array, epoch: number): void {
    const streamId = this.streamId;
    this.tail = this.tail.then(async () => {
      if (epoch !== this.epoch) return;
      const url = new URL(bridgeSiblingUrl("/telemetry/voice/audio/output"));
      url.searchParams.set("stream_id", streamId);
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: Buffer.from(chunk),
      });
      if (!response.ok) throw new Error(`robot audio output failed (${response.status})`);
      const result = (await response.json()) as { status?: string; rpc_code?: number };
      if (result.status !== "ok") {
        throw new Error(`robot rejected audio output (rpc ${result.rpc_code ?? "unknown"})`);
      }
    });
  }
}

/** One operator-owned speech-to-speech session. Typed `/agent` is untouched. */
export class RealtimeVoiceSession {
  private socket: WebSocket | null = null;
  private inputAbort: AbortController | null = null;
  private audio = new RobotAudioSink();
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

  snapshot(): RealtimeVoiceState {
    return { ...this.state };
  }

  async start(options: StartOptions): Promise<void> {
    if (this.state.running) {
      if (this.state.ownerId !== options.ownerId) throw new Error("voice session is owned by another operator");
      return;
    }
    const apiKey = process.env.OPENAI_API_KEY;
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
    this.audio = new RobotAudioSink();

    try {
      const [baseInstructions, skills] = await Promise.all([buildSystemPrompt("voice"), listSkills()]);
      const instructions = `${baseInstructions}\n\nRealtime voice safety: high-danger tools are intentionally unavailable. Ask the operator to use the authenticated console for those actions.`;
      const url = new URL("wss://api.openai.com/v1/realtime");
      url.searchParams.set("model", MODEL);
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
        if (this.state.running) this.fail(new Error("OpenAI Realtime connection closed"));
      });

      this.send(realtimeSessionUpdate(instructions, skills));

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

  private send(event: unknown): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(event));
  }

  private async pumpRobotAudio(signal: AbortSignal): Promise<void> {
    try {
      const response = await fetch(bridgeSiblingUrl("/telemetry/voice/audio/input"), {
        headers: { Accept: "audio/pcm" },
        cache: "no-store",
        signal,
      });
      if (!response.ok || !response.body) throw new Error(`robot audio input failed (${response.status})`);
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
      void this.audio.interrupt();
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
    }
  }

  private async persistTranscript(role: "user" | "assistant", transcript?: string): Promise<void> {
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
    await appendMessage({ id: messageId(), chatId, role, parts: [{ type: "text", text }] }).catch(
      (error) => console.error("[voice/realtime] transcript persistence failed", error),
    );
  }

  private async executeTool(event: RealtimeEvent): Promise<void> {
    const name = event.name;
    const callId = event.call_id;
    const chatId = this.state.chatId;
    if (!name || !callId || !chatId) return;
    let args: Record<string, unknown> = {};
    try {
      args = JSON.parse(event.arguments || "{}") as Record<string, unknown>;
    } catch {
      args = {};
    }

    const started = performance.now();
    let result: unknown;
    let status: "ok" | "error" = "ok";
    let error: string | null = null;
    try {
      result = await callTool(name, args);
    } catch (cause) {
      status = "error";
      error = cause instanceof Error ? cause.message : String(cause);
      result = { error };
    }
    this.state.toolCalls += 1;
    await logToolCall({
      chatId,
      skillName: name,
      params: args,
      result,
      status,
      error,
      durationMs: Math.round(performance.now() - started),
    });
    await appendMessage({
      id: messageId(),
      chatId,
      role: "assistant",
      parts: [{
        type: "dynamic-tool",
        toolName: name,
        toolCallId: callId,
        state: "output-available",
        input: args,
        output: result,
      }],
    }).catch((cause) => console.error("[voice/realtime] tool persistence failed", cause));
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
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(userId));
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
