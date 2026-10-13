/**
 * The voice loop's switch, as reactive state for the console.
 *
 * DELIBERATELY THIN. Everything that decides what the operator is told lives in
 * `voice-loop.ts`, which is a plain module and therefore testable — `bun test`
 * cannot load a `.svelte.ts` at all, because runes are a compiler feature. This
 * holds reactive fields, the poll (via `Poller`), and a guard against
 * double-firing the button that hands a robot its microphone.
 *
 * ONE INSTANCE PER CONSOLE. The protected layout owns it (see `context.ts`), so
 * the topbar can show that the robot is listening on every page — a running
 * loop is a privacy state, and it used to be visible only at the bottom of the
 * dashboard.
 *
 * STARTING IT IS NOT A TOGGLE, IT IS A DECISION. While it runs, speech near the
 * robot becomes a conversational turn; clear physical requests may call robot
 * tools. `apps/back/src/voice/loop.ts` makes the same argument for why it is
 * explicitly started and never ambient.
 */

import {
  canControl,
  canHear,
  isRunning,
  voiceCommand,
  voiceStatus,
  type VoiceLoopState,
  type VoiceResult,
} from "./voice-loop";
import { Poller, type PollResult } from "./poller.svelte";

export type { VoiceLoopState };

const POLL_MS = 2000;

export class VoiceLoopControl extends Poller {
  state = $state<VoiceLoopState | null>(null);
  /** Why the latest STATUS read failed, in words an operator can act on. */
  reason = $state<string | null>(null);
  /**
   * Why the latest start/stop failed. Kept apart from `reason` because a status
   * poll succeeding 2 s later used to wipe the error before anyone read it.
   * Cleared by the next successful command, or by the operator.
   */
  commandError = $state<string | null>(null);
  /** True while a start/stop is in flight, so the button cannot be double-fired. */
  busy = $state(false);
  /** Which command is in flight, for "Iniciando…" / "Terminando…". */
  pending = $state<"start" | "stop" | null>(null);

  /** Where `back` lives. Injected: `$env/static/public` is a build-time import
   *  and pulling it in here is what would make this module unloadable in a test. */
  readonly #base: string;
  /**
   * Bumped whenever a start/stop answers. A status poll that was already in
   * flight when the command was sent describes the loop BEFORE it, and landing
   * after the command's reply it used to flip the chip back to "Escuchando"
   * for a poll cycle after Stop — on a privacy state.
   */
  #commandSeq = 0;

  constructor(base: string) {
    super({ timeoutMs: 4000 });
    this.#base = base;
  }

  get running(): boolean {
    return isRunning(this.state);
  }

  get canHear(): boolean {
    return canHear(this.state);
  }

  /** False when another operator owns the running realtime session. */
  get canControl(): boolean {
    return canControl(this.state);
  }

  start(intervalMs = POLL_MS): void {
    super.start(intervalMs);
  }

  async startLoop(): Promise<void> {
    await this.#command("start");
  }

  async stopLoop(): Promise<void> {
    await this.#command("stop");
  }

  dismissError(): void {
    this.commandError = null;
  }

  async #command(which: "start" | "stop"): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.pending = which;
    try {
      const result = await voiceCommand(this.#base, which);
      this.#commandSeq += 1;
      if (result.ok) {
        // The reply IS the new state — no optimistic flip.
        this.state = result.state;
        this.commandError = null;
      } else {
        if (result.state) this.state = result.state;
        this.commandError = result.reason;
        if (result.status === 401) this.onUnauthorized();
      }
    } finally {
      this.busy = false;
      this.pending = null;
    }
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const seq = this.#commandSeq;
    const result: VoiceResult = await voiceStatus(this.#base, signal);
    // Overtaken by a start/stop (or one is in flight): the command's reply is
    // newer than this reading. Not a failure either.
    if (seq !== this.#commandSeq || this.busy) return "ok";
    if (result.ok) {
      this.state = result.state;
      this.reason = null;
      return "ok";
    }
    this.reason = result.reason;
    return result.status === 401 ? "unauthorized" : "error";
  }
}
