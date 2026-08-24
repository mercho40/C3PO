/**
 * The voice loop's switch, as reactive state for the console.
 *
 * DELIBERATELY THIN. Everything that decides what the operator is told lives in
 * `voice-loop.ts`, which is a plain module and therefore testable — `bun test`
 * cannot load a `.svelte.ts` at all, because runes are a compiler feature. This
 * holds reactive fields, a poll timer, and a guard against double-firing the
 * button that hands a robot its microphone.
 *
 * STARTING IT IS NOT A TOGGLE, IT IS A DECISION. While it runs, anything said
 * near the robot is transcribed and handed to an agent that can call every
 * bridge tool. `apps/back/src/voice/loop.ts` makes the same argument for why it
 * is explicitly started and never ambient.
 */

import {
  canHear,
  isRunning,
  voiceCommand,
  voiceStatus,
  type VoiceLoopState,
  type VoiceResult,
} from "./voice-loop";

export type { VoiceLoopState };

const POLL_MS = 2000;

export class VoiceLoopControl {
  state = $state<VoiceLoopState | null>(null);
  /** Why there is nothing, in words an operator can act on. Null when fine. */
  reason = $state<string | null>(null);
  /** True while a start/stop is in flight, so the button cannot be double-fired. */
  busy = $state(false);

  /** Where `back` lives. Injected: `$env/static/public` is a build-time import
   *  and pulling it in here is what would make this module unloadable in a test. */
  readonly #base: string;
  #timer: ReturnType<typeof setInterval> | null = null;
  #inFlight = false;

  constructor(base: string) {
    this.#base = base;
  }

  get running(): boolean {
    return isRunning(this.state);
  }

  get canHear(): boolean {
    return canHear(this.state);
  }

  start(): void {
    if (this.#timer) return;
    void this.#refresh();
    this.#timer = setInterval(() => void this.#refresh(), POLL_MS);
  }

  stop(): void {
    if (this.#timer) clearInterval(this.#timer);
    this.#timer = null;
  }

  async startLoop(): Promise<void> {
    await this.#command("start");
  }

  async stopLoop(): Promise<void> {
    await this.#command("stop");
  }

  #apply(result: VoiceResult): void {
    if (result.ok) {
      // The reply IS the new state — no optimistic flip.
      this.state = result.state;
      this.reason = null;
    } else {
      this.reason = result.reason;
    }
  }

  async #command(which: "start" | "stop"): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      this.#apply(await voiceCommand(this.#base, which));
    } finally {
      this.busy = false;
    }
  }

  async #refresh(): Promise<void> {
    if (this.#inFlight) return;
    this.#inFlight = true;
    try {
      this.#apply(await voiceStatus(this.#base));
    } finally {
      this.#inFlight = false;
    }
  }
}
