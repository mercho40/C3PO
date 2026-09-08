/**
 * The voice loop: what the robot hears becomes the next conversational turn.
 *
 * Every piece existed and none of them were connected. The bridge can hear
 * (`listen`) and speak (`say`), the agent can reason and already receives every
 * bridge tool dynamically — but nothing ever fed one into the other, so the
 * agent only ever ran when somebody typed into the web console. This is the
 * loop that closes it.
 *
 * IT LIVES IN `back`, NOT ON THE ROBOT, and that is D6.2's split rather than a
 * convenience: reasoning runs where the credentials are, and the robot holds no
 * cloud keys. It is also why the loop is allowed to be slow — nothing here is
 * on the path that stops the robot.
 *
 * EXPLICITLY STARTED, NEVER AMBIENT. A robot that reasons about every overheard
 * sentence is both a privacy problem and a bill, so this runs only while an
 * operator has switched it on. On the robot's own microphone the point is
 * moot — it is push-to-talk and hears nothing unless somebody holds L1+L2 — but
 * that stops being true the moment a USB mic is plugged in, and the guarantee
 * should not depend on which microphone is attached.
 */

/** One thing the robot heard, as `listen` reports it. */
export type Heard = { text: string; age_s: number };

/** The shape of `listen`'s result that this loop depends on. */
export type ListenResult = {
  status: string;
  heard?: Heard[];
  transcript?: string;
  stop_heard?: string | null;
  mic_ever_open?: boolean;
  always_listening?: boolean;
  note?: string | null;
};

export type VoiceInputEvent = {
  seq: number;
  kind: "speech" | "stop";
  text: string;
};

export type VoiceLoopDeps = {
  /** Calls a bridge tool. Injected so the loop is testable with no robot. */
  callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>;
  /** Runs the agent over an utterance. Injected so tests need no model. */
  runAgent: (utterance: string) => Promise<void>;
  /** Production uses the bridge's SSE stream. Omit to retain the polling fallback. */
  events?: (signal: AbortSignal) => AsyncIterable<VoiceInputEvent>;
  /** Non-consuming microphone diagnostics used alongside the event stream. */
  inputStatus?: (
    signal: AbortSignal,
  ) => Promise<{ mic_ever_open?: boolean; always_listening?: boolean }>;
  /** Overridable for deterministic tests. */
  sleep?: (ms: number) => Promise<void>;
  log?: (event: string, detail?: Record<string, unknown>) => void;
};

export type VoiceLoopOptions = {
  /** How often to check for speech. The listener buffers, so this is not a
   *  sampling rate — nothing is missed between polls. */
  pollMs?: number;
  /** Act on a heard stop phrase by calling `stop_everything` immediately.
   *
   *  ON by default *inside this loop*, which is a narrower claim than it looks:
   *  the loop is itself an explicit opt-in, and within it a spoken "emergencia"
   *  plainly means stop. It is still NOT the safety story — whoever is holding
   *  the remote to make the robot hear at all has a physical e-stop under their
   *  thumb, which is faster and cannot mis-hear. A mis-decode costs a halted
   *  robot, which is the cheap direction to be wrong in. */
  actOnStopPhrase?: boolean;
};

export type VoiceLoopState = {
  running: boolean;
  utterancesHeard: number;
  agentRuns: number;
  stopsTriggered: number;
  micEverOpen: boolean;
  alwaysListening: boolean;
  lastError: string | null;
  lastHeard: string | null;
};

const DEFAULT_POLL_MS = 1000;

export class VoiceLoop {
  private readonly deps: Required<
    Pick<VoiceLoopDeps, "callTool" | "runAgent">
  > &
    VoiceLoopDeps;
  private readonly pollMs: number;
  private readonly actOnStopPhrase: boolean;

  private stopping = false;
  private task: Promise<void> | null = null;
  private eventAbort: AbortController | null = null;
  private state: VoiceLoopState = {
    running: false,
    utterancesHeard: 0,
    agentRuns: 0,
    stopsTriggered: 0,
    micEverOpen: false,
    alwaysListening: false,
    lastError: null,
    lastHeard: null,
  };

  constructor(deps: VoiceLoopDeps, options: VoiceLoopOptions = {}) {
    this.deps = deps;
    this.pollMs = options.pollMs ?? DEFAULT_POLL_MS;
    this.actOnStopPhrase = options.actOnStopPhrase ?? true;
  }

  snapshot(): VoiceLoopState {
    return { ...this.state };
  }

  start(): void {
    if (this.state.running) return;
    this.stopping = false;
    this.state.running = true;
    this.eventAbort = new AbortController();
    this.task = this.deps.events ? this.runEvents() : this.runPolling();
  }

  /** Stops after the current iteration. Awaitable so tests are deterministic. */
  async stop(): Promise<void> {
    this.stopping = true;
    this.eventAbort?.abort();
    await this.task?.catch(() => {});
    this.eventAbort = null;
    this.state.running = false;
  }

  private sleep(ms: number): Promise<void> {
    return this.deps.sleep
      ? this.deps.sleep(ms)
      : new Promise((r) => setTimeout(r, ms));
  }

  private log(event: string, detail?: Record<string, unknown>): void {
    this.deps.log?.(event, detail);
  }

  private async runPolling(): Promise<void> {
    while (!this.stopping) {
      try {
        await this.tick();
      } catch (e) {
        // NEVER let one bad iteration end the loop. A dropped bridge connection
        // or a model error must not silently leave the robot deaf while the UI
        // still shows the loop as running — record it and keep polling.
        this.state.lastError = (e as Error).message;
        this.log("voice.tick_failed", { error: this.state.lastError });
      }
      if (!this.stopping) await this.sleep(this.pollMs);
    }
  }

  /** Event-driven production path: no fixed poll delay after end-of-turn. */
  private async runEvents(): Promise<void> {
    while (!this.stopping) {
      try {
        const signal = this.eventAbort?.signal;
        if (!signal || !this.deps.events) return;
        if (this.deps.inputStatus) {
          const status = await this.deps.inputStatus(signal);
          this.state.micEverOpen = Boolean(status.mic_ever_open);
          this.state.alwaysListening = Boolean(status.always_listening);
        }
        for await (const event of this.deps.events(signal)) {
          if (this.stopping) return;
          await this.handleEvent(event);
        }
        if (!this.stopping) throw new Error("voice event stream ended");
      } catch (e) {
        if (this.stopping || this.eventAbort?.signal.aborted) return;
        this.state.lastError = (e as Error).message;
        this.log("voice.stream_failed", { error: this.state.lastError });
        await this.sleep(250);
      }
    }
  }

  private async handleEvent(event: VoiceInputEvent): Promise<void> {
    if (event.kind === "stop") {
      if (!this.actOnStopPhrase) return;
      this.state.stopsTriggered += 1;
      this.log("voice.stop_phrase", { phrase: event.text, seq: event.seq });
      await this.deps.callTool("stop_everything", {});
      return;
    }
    await this.handleUtterance(event.text, event.seq);
  }

  private async handleUtterance(text: string, seq?: number): Promise<void> {
    const utterance = text.trim();
    if (!utterance) return;
    this.state.utterancesHeard += 1;
    this.state.lastHeard = utterance;
    this.state.agentRuns += 1;
    this.state.lastError = null;
    this.log("voice.utterance", { text: utterance, seq });
    await this.deps.runAgent(utterance);
  }

  /** One poll. Exposed for tests so they never need timers. */
  async tick(): Promise<void> {
    const result = (await this.deps.callTool("listen", {})) as ListenResult;

    this.state.micEverOpen = Boolean(result?.mic_ever_open);
    this.state.alwaysListening = Boolean(result?.always_listening);

    // The stop phrase is handled BEFORE the transcript, and without waiting for
    // the agent. Routing it through a model would put an LLM round-trip between
    // a person saying "stop" and the robot stopping.
    if (result?.stop_heard && this.actOnStopPhrase) {
      this.state.stopsTriggered += 1;
      this.log("voice.stop_phrase", { phrase: result.stop_heard });
      await this.deps.callTool("stop_everything", {});
    }

    const utterances = (result?.heard ?? []).map((h) => h.text).filter(Boolean);
    if (utterances.length === 0) return;

    this.state.utterancesHeard += utterances.length;

    // Joined into ONE turn rather than one agent run per utterance. Vosk splits
    // on pauses, so a single sentence said with a breath in the middle arrives
    // as two — running the agent twice would answer half a question, then
    // answer the other half without the first half's context.
    const utterance = utterances.join(" ").trim();
    this.state.lastHeard = utterance;
    this.state.agentRuns += 1;
    this.log("voice.utterance", { text: utterance });

    // Awaited, so the next poll cannot start a second agent run while this one
    // is still deciding. `listen` buffers meanwhile, so nothing said during the
    // agent's turn is lost — it is simply picked up on the next tick.
    await this.deps.runAgent(utterance);
  }
}
