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
 * Capture runs on an AudioWorklet (mic-capture.worklet.js).
 */
import { Chat } from "@ai-sdk/svelte";
import { DefaultChatTransport, type UIMessage } from "ai";
import { untrack } from "svelte";
import { PUBLIC_API_URL } from "$app/env/public";
import { ADMIN_REQUIRED_HINT } from "#lib/chat/chat-error.js";
import { decideCall } from "#lib/chat/confirm.js";
import { newId } from "#lib/chat/ids.js";
import type { MenuItem, VoiceLine } from "#lib/webxr/menu-layer.js";
import { estop } from "./estop.svelte";
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
import { concat, downsample, encodeWav, TARGET_RATE, trimSilence } from "./wav";
// no-inline: a same-origin file, not a data: URL, is what every worklet loader accepts.
import workletUrl from "./mic-capture.worklet.js?url&no-inline";

/** apps/back /voice/transcribe's answer to a spoken «confirmo»/«cancelar». */
type VoiceConfirmation = {
  action: "confirmed" | "cancelled" | "ambiguous";
  tool?: string;
  count?: number;
  message?: string;
  error?: string;
  detail?: string;
};

const MAX_TALK_S = 30; // the backend takes ~60 s; nobody holds B that long on purpose
const MIN_TALK_S = 0.3; // a tap is not an utterance
// The wait on /voice/transcribe is deliberate (a «confirmo» may be running the
// call), but a request whose answer never comes must not hold the panel forever.
const REPLY_TIMEOUT_MS = 60_000;

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
  #node: AudioWorkletNode | null = null;
  #chunks: Float32Array[] = [];
  #recording = false;
  #startedAt = 0;
  #stateLogged = false;
  #opening = false; // a second tap during the permission prompt must not open a second mic
  /** Bumped per upload, by PARAR and by the give-up: a reply to an older one is dropped. */
  #request = 0;
  #unwatchStop: () => void;

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
    // PARAR drops the utterance in flight: nothing said before the stop reaches
    // the agent after it, and the panel is free again at once.
    this.#unwatchStop = $effect.root(() => {
      $effect(() => {
        if (estop.phase === "sending") untrack(() => this.#abandon(null));
      });
    });
  }

  /** An agent turn is running (submitted or streaming, tool calls included), for any utterance, questions too. */
  get busy(): boolean {
    return (
      this.#chat.status === "submitted" || this.#chat.status === "streaming"
    );
  }

  get lines(): VoiceLine[] {
    const phase = this.phase === "idle" && this.busy ? "thinking" : this.phase;
    return voiceLines(this.#chat.messages, {
      phase,
      stopStanding: this.#latch.standing,
      error: this.error ?? this.#latch.error,
    });
  }

  /** The refused call on screen right now, if any: Confirmar and «confirmo» act on it only. */
  get #shown() {
    return this.#confirming || this.busy
      ? null
      : pendingConfirmation(this.#chat.messages);
  }

  get menuItems(): MenuItem[] {
    const pending = this.#shown;
    return voiceMenu(
      pending?.tool ?? null,
      this.#latch.standing,
      pending ?? undefined,
    );
  }

  /** From the 2D page, before entering VR. */
  async enableMic(): Promise<void> {
    if (this.micReady || this.#opening) return;
    this.#opening = true;
    this.micError = null;
    let ctx: AudioContext | null = null;
    try {
      // Before any await, so it is created inside the tap's user activation.
      ctx = new AudioContext();
      this.#stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
      });
      await ctx.audioWorklet.addModule(workletUrl);
      const node = new AudioWorkletNode(ctx, "mic-capture");
      node.port.onmessage = (e: MessageEvent<Float32Array>) => {
        if (!this.#recording) return; // a block in flight when #finish posted false
        this.#chunks.push(e.data);
        if (performance.now() - this.#startedAt > MAX_TALK_S * 1000)
          void this.#finish();
      };
      // A throw in the worklet kills it for good; say so instead of recording silence.
      node.onprocessorerror = () => {
        this.#releaseMic();
        this.micError = "El micrófono dejó de funcionar: activalo de nuevo.";
      };
      ctx.createMediaStreamSource(this.#stream).connect(node);
      node.connect(ctx.destination); // Chromium only pulls a connected node; the output stays silent
      const track = this.#stream.getAudioTracks()[0];
      // A dropped mic (unplugged, revoked) brings the 2D button back instead of recording silence.
      if (track) track.onended = () => this.#releaseMic();
      this.#ctx = ctx;
      this.#node = node;
      this.micReady = true;
    } catch (e) {
      // Never leak a stream or a context, whichever step failed.
      this.#stream?.getTracks().forEach((t) => t.stop());
      this.#stream = null;
      void ctx?.close();
      this.micError =
        e instanceof DOMException && e.name === "NotAllowedError"
          ? "Permiso de micrófono denegado."
          : "No se pudo abrir el micrófono.";
    } finally {
      this.#opening = false;
    }
  }

  /** On the «Entrar en VR» tap: resume inside the same user activation requestSession uses. */
  prime(): void {
    this.#stateLogged = false;
    void this.#ctx?.resume();
  }

  /** B/Y pressed (true) or released (false). */
  onTalk(down: boolean): void {
    if (down) this.#begin();
    else void this.#finish();
  }

  /** Drop a recording without sending it (headset off, page blurred). */
  cancelTalk(): void {
    this.#recording = false;
    this.#node?.port.postMessage(false);
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
    if (this.busy || this.phase !== "idle") {
      this.error = "Esperá a que el robot termine de responder.";
      return;
    }
    if (!this.#stateLogged) {
      // Evidence for 'Muy corto' reports: a suspended context records nothing.
      console.info(`[vr-voice] AudioContext ${this.#ctx.state} on first talk`);
      this.#stateLogged = true;
    }
    void this.#ctx.resume();
    this.#chunks = [];
    this.#startedAt = performance.now();
    this.#recording = true;
    this.#node?.port.postMessage(true);
    this.phase = "listening";
  }

  async #finish(): Promise<void> {
    if (!this.#recording || !this.#ctx) return;
    const recordMs = Math.round(performance.now() - this.#startedAt);
    this.#recording = false;
    this.#node?.port.postMessage(false);
    const samples = concat(this.#chunks);
    this.#chunks = [];
    if (samples.length < MIN_TALK_S * this.#ctx.sampleRate) {
      this.phase = "idle";
      // Not one block in the whole hold: the capture is dead, not the operator too quick.
      this.error =
        samples.length === 0
          ? "El micrófono no entrega audio: activalo de nuevo."
          : "Muy corto: mantené B/Y mientras hablás.";
      return;
    }
    // Back decides a spoken «confirmo»/«cancelar» from this text, for the
    // one call on screen only, before any model sees it (voice.ts): with a call
    // on screen this request may also run it, so say so while it waits.
    const shown = this.#shown;
    const req = ++this.#request;
    const giveUp = setTimeout(() => {
      if (req === this.#request)
        this.#abandon(
          shown
            ? "Sin respuesta en 60 s: si dijiste «confirmo», puede haberse ejecutado."
            : "Sin respuesta en 60 s: probá de nuevo.",
        );
    }, REPLY_TIMEOUT_MS);
    try {
      const speech = trimSilence(
        downsample(samples, this.#ctx.sampleRate, TARGET_RATE),
      );
      if (speech.length === 0) {
        this.phase = "idle";
        this.error = "No se oyó nada: hablá más cerca del visor.";
        return;
      }
      this.phase = shown ? "deciding" : "transcribing";
      const wav = encodeWav(speech);
      const q = new URLSearchParams({ chatId: this.#chat.id });
      if (shown) q.set("pending", shown.id);
      const sentAt = performance.now();
      const res = await fetch(`${PUBLIC_API_URL}/voice/transcribe?${q}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "audio/wav" },
        body: new Blob([wav.buffer as ArrayBuffer], { type: "audio/wav" }),
      });
      if (req !== this.#request) return;
      if (res.status === 401) redirectToLogin("expired");
      if (res.status === 403) {
        this.phase = "idle";
        this.error = ADMIN_REQUIRED_HINT;
        return;
      }
      const body = (await res.json().catch(() => ({}))) as {
        text?: string;
        error?: string;
        confirmation?: VoiceConfirmation;
      };
      if (req !== this.#request) return;
      // Back logs its own split of the second figure (upload, transcribe).
      console.info(
        `[vr-voice] record ${recordMs} ms (${(speech.length / TARGET_RATE).toFixed(1)} s sent), upload+transcribe ${Math.round(performance.now() - sentAt)} ms`,
      );
      if (!res.ok) throw new Error(body.error ?? String(res.status));
      const text = (body.text ?? "").trim();
      this.phase = "idle";
      if (!text) {
        this.error = "No se entendió. Probá de nuevo, más cerca.";
        return;
      }
      if (body.confirmation) {
        this.#spoken(body.confirmation);
        return;
      }
      const askedAt = performance.now();
      void this.#chat
        .sendMessage({ text })
        .then(() =>
          console.info(
            `[vr-voice] agent ${Math.round(performance.now() - askedAt)} ms`,
          ),
        );
    } catch (e) {
      if (req !== this.#request) return;
      this.phase = "idle";
      this.error = `No se pudo transcribir (${e instanceof Error ? e.message : "error"}).`;
    } finally {
      clearTimeout(giveUp);
    }
  }

  /** Stop waiting on the utterance in flight; its reply, when it comes, is dropped. */
  #abandon(error: string | null): void {
    this.cancelTalk();
    this.#request++;
    this.phase = "idle";
    this.error = error;
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

  /** What back did with a spoken «confirmo»/«cancelar». */
  #spoken(c: VoiceConfirmation): void {
    if (c.action === "ambiguous") {
      this.error = `Hay ${c.count ?? "varias"} confirmaciones pendientes: elegí con el menú.`;
      return;
    }
    // The agent reads what happened and continues; it never retries.
    if (c.message) void this.#chat.sendMessage({ text: c.message });
    else
      this.error = `No se pudo ${c.action === "cancelled" ? "cancelar" : "confirmar"} ${c.tool ?? ""} (${c.detail ?? c.error ?? "sin respuesta"}).`;
  }

  async #confirm(key: string): Promise<void> {
    // Re-read at click time: the menu may be older than the transcript, and
    // the call run must be the one the operator was shown.
    const pending = pendingConfirmation(this.#chat.messages);
    if (!pending || pending.id !== key) {
      this.error = "Esa confirmación ya no corresponde: no se envió nada.";
      return;
    }
    const { tool } = pending;
    this.#confirming = true;
    try {
      // One action: back grants AND runs exactly this call.
      const r = await decideCall(
        PUBLIC_API_URL,
        tool,
        pending.id,
        pending.input,
        "confirm",
      );
      if (r.message) void this.#chat.sendMessage({ text: r.message });
      else this.error = `${tool}: ${r.outcome.text}`;
    } finally {
      this.#confirming = false;
    }
  }

  /** The mic alone: a dropped track must not abort a running agent turn. */
  #releaseMic(): void {
    this.cancelTalk();
    this.#node?.disconnect();
    this.#node = null;
    this.#stream?.getTracks().forEach((t) => t.stop());
    this.#stream = null;
    void this.#ctx?.close();
    this.#ctx = null;
    this.micReady = false;
  }

  destroy(): void {
    this.#unwatchStop();
    this.#releaseMic();
    void this.#chat.stop();
  }
}
