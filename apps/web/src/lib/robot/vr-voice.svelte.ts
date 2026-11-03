/**
 * "Hablar con el robot" from inside the headset.
 *
 * Push-to-talk on the Quest's own microphone (B/Y held, see vr-control), the
 * recording encoded to WAV here, `POST /voice/transcribe` for the text, then an
 * ordinary agent turn over `POST /agent` — the same Chat + transport the chat
 * page uses, so every bridge gate (PARAR latch, T2 confirmation, person rule)
 * applies unchanged. The words the panel shows are decided in voice-panel.ts.
 *
 * The microphone is opened from a 2D button BEFORE entering VR: Quest Browser
 * shows its permission prompt in 2D, and the AudioContext needs a user gesture.
 */
import { Chat } from "@ai-sdk/svelte";
import { DefaultChatTransport, type UIMessage } from "ai";
import { PUBLIC_API_URL } from "$app/env/public";
import { newId } from "#lib/chat/ids.js";
import type { MenuItem, VoiceLine } from "#lib/webxr/menu-layer.js";
import { redirectToLogin } from "./poller.svelte";
import type { StopLatch } from "./stop-latch.svelte";
import {
  VOICE_CLOSE,
  VOICE_CONFIRM,
  VOICE_OPEN,
  VOICE_RESUME,
  pendingConfirmation,
  voiceLines,
  voiceMenu,
  type VoicePhase,
} from "./voice-panel";
import { concat, downsample, encodeWav, TARGET_RATE } from "./wav";

const MAX_TALK_S = 30; // the backend takes ~60 s; nobody holds B that long on purpose
const MIN_TALK_S = 0.3; // a tap is not an utterance

export class VrVoice {
  /** Voice mode: the headset menu shows the conversation instead of gestures. */
  open = $state(false);
  micReady = $state(false);
  micError = $state<string | null>(null);
  phase = $state<VoicePhase>("idle");
  error = $state<string | null>(null);
  #confirming = $state(false);

  #latch: StopLatch;
  #chat: Chat<UIMessage>;
  #ctx: AudioContext | null = null;
  #stream: MediaStream | null = null;
  #proc: ScriptProcessorNode | null = null;
  #chunks: Float32Array[] = [];
  #recording = false;
  #startedAt = 0;

  constructor(latch: StopLatch) {
    this.#latch = latch;
    this.#chat = new Chat({
      id: newId(),
      transport: new DefaultChatTransport({
        api: `${PUBLIC_API_URL}/agent`,
        credentials: "include",
        // Spoken, so voice mode: the voice exclusions (damp, high danger) apply.
        body: { mode: "voice" },
        fetch: async (input, init) => {
          const res = await fetch(input, init);
          if (res.status === 401) redirectToLogin("expired");
          return res;
        },
      }),
    });
  }

  get #busy(): boolean {
    return (
      this.#chat.status === "submitted" || this.#chat.status === "streaming"
    );
  }

  get lines(): VoiceLine[] {
    const phase = this.phase === "idle" && this.#busy ? "thinking" : this.phase;
    return voiceLines(this.#chat.messages, {
      phase,
      stopStanding: this.#latch.standing,
      error: this.error ?? this.#latch.error,
    });
  }

  get menuItems(): MenuItem[] {
    const pending =
      this.#confirming || this.#busy
        ? null
        : pendingConfirmation(this.#chat.messages);
    return voiceMenu(pending, this.#latch.standing);
  }

  /** From the 2D page, before entering VR. */
  async enableMic(): Promise<void> {
    if (this.micReady) return;
    this.micError = null;
    try {
      this.#stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
      });
      const ctx = new AudioContext();
      const source = ctx.createMediaStreamSource(this.#stream);
      // shortcut: ScriptProcessor is deprecated but needs no worklet module
      // file; move to an AudioWorklet if Quest Browser ever drops it.
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (e) => {
        if (!this.#recording) return;
        this.#chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        if (performance.now() - this.#startedAt > MAX_TALK_S * 1000)
          void this.#finish();
      };
      source.connect(proc);
      proc.connect(ctx.destination); // Chromium only runs it when connected; the output stays silent
      this.#ctx = ctx;
      this.#proc = proc;
      this.micReady = true;
    } catch (e) {
      this.micError =
        e instanceof DOMException && e.name === "NotAllowedError"
          ? "Permiso de micrófono denegado."
          : "No se pudo abrir el micrófono.";
    }
  }

  /** B/Y pressed (true) or released (false). */
  onTalk(down: boolean): void {
    if (down) this.#begin();
    else void this.#finish();
  }

  /** Drop a recording without sending it (headset off, page blurred). */
  cancelTalk(): void {
    this.#recording = false;
    this.#chunks = [];
    if (this.phase === "listening") this.phase = "idle";
  }

  #begin(): void {
    this.open = true;
    this.error = null;
    if (!this.micReady || !this.#ctx) {
      this.error =
        "Micrófono apagado: activalo en la página antes de entrar en VR.";
      return;
    }
    if (this.#busy || this.phase !== "idle") {
      this.error = "Esperá a que el robot termine de responder.";
      return;
    }
    void this.#ctx.resume();
    this.#chunks = [];
    this.#startedAt = performance.now();
    this.#recording = true;
    this.phase = "listening";
  }

  async #finish(): Promise<void> {
    if (!this.#recording || !this.#ctx) return;
    this.#recording = false;
    const samples = concat(this.#chunks);
    this.#chunks = [];
    if (samples.length < MIN_TALK_S * this.#ctx.sampleRate) {
      this.phase = "idle";
      this.error = "Muy corto: mantené B/Y mientras hablás.";
      return;
    }
    this.phase = "transcribing";
    try {
      const wav = encodeWav(
        downsample(samples, this.#ctx.sampleRate, TARGET_RATE),
      );
      const res = await fetch(`${PUBLIC_API_URL}/voice/transcribe`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "audio/wav" },
        body: new Blob([wav.buffer as ArrayBuffer], { type: "audio/wav" }),
      });
      if (res.status === 401) redirectToLogin("expired");
      const body = (await res.json().catch(() => ({}))) as {
        text?: string;
        error?: string;
      };
      if (!res.ok) throw new Error(body.error ?? String(res.status));
      const text = (body.text ?? "").trim();
      this.phase = "idle";
      if (!text) {
        this.error = "No se entendió. Probá de nuevo, más cerca.";
        return;
      }
      void this.#chat.sendMessage({ text });
    } catch (e) {
      this.phase = "idle";
      this.error = `No se pudo transcribir (${e instanceof Error ? e.message : "error"}).`;
    }
  }

  /** A `voice:*` menu selection. True when handled. */
  onItem(name: string): boolean {
    this.error = null;
    if (name === VOICE_OPEN) this.open = true;
    else if (name === VOICE_CLOSE) this.open = false;
    else if (name === VOICE_RESUME) void this.#latch.resume();
    else if (name.startsWith(VOICE_CONFIRM))
      void this.#confirm(name.slice(VOICE_CONFIRM.length));
    else return false;
    return true;
  }

  async #confirm(tool: string): Promise<void> {
    this.#confirming = true;
    try {
      const res = await fetch(
        `${PUBLIC_API_URL}/skills/${encodeURIComponent(tool)}/confirm`,
        {
          method: "POST",
          credentials: "include",
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string };
        this.error = `No se pudo confirmar ${tool} (${res.status}${body.error ? `: ${body.error}` : ""}).`;
        return;
      }
      void this.#chat.sendMessage({
        text: `Confirmado: podés ejecutar ${tool} ahora.`,
      });
    } catch {
      this.error = `No se pudo confirmar ${tool}: sin respuesta del servidor.`;
    } finally {
      this.#confirming = false;
    }
  }

  destroy(): void {
    this.cancelTalk();
    void this.#chat.stop();
    this.#proc?.disconnect();
    this.#stream?.getTracks().forEach((t) => t.stop());
    void this.#ctx?.close();
    this.#ctx = null;
    this.micReady = false;
  }
}
