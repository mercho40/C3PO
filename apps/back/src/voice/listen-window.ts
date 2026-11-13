/**
 * «Escuchar después de saludar»: when the robot greets an ENROLLED person by
 * name («Hola Oliver, ¿qué querés que haga?»), open the legacy robot-mic voice
 * loop for a while so they can just answer.
 *
 * The bridge numbers each enrolled greeting (`/face/seen` → `greet.seq`, never
 * a stranger's) and carries the «Caras» switch (`greet.listen_after_greet`).
 * `tick()` runs once a second. A new seq opens a window: the loop starts, its
 * turns and cards belong to the admin who turned the switch on (`owner`).
 * Each utterance or running turn extends it; `windowMs` of quiet closes it.
 * Only a loop this window started is ever stopped: an admin's own /voice
 * session is left alone. A PARAR (bridge latch moved, or the loop's own stop
 * phrase) closes it at once. Unknown owner (back restarted since the switch
 * was turned on): no window, said once per greeting.
 */

export type Greet = { seq: number; name: string | null; listen: boolean };

export type ListenWindowDeps = {
  /** The bridge's last enrolled greeting, or null when unreadable. */
  readGreet: () => Promise<Greet | null>;
  /** The bridge's stop latch stamp, or null when unreadable (stop-watch.ts). */
  readStoppedAt: () => Promise<number | null>;
  loopRunning: () => boolean;
  startLoop: (owner: string) => void;
  stopLoop: () => Promise<void>;
  /** Grows with each utterance heard or turn run by the loop. */
  activity: () => number;
  /** The loop's spoken stops so far. */
  stops: () => number;
  /** A voice turn is in flight (thinking or speaking). */
  busy: () => boolean;
  now?: () => number;
  log?: (event: string, detail?: Record<string, unknown>) => void;
};

export class ListenWindow {
  private owner: string | null = null;
  private lastSeq: number | null = null;
  private open = false;
  private deadline = 0;
  private lastActivity = 0;
  private lastStops = 0;
  private stoppedAt: number | null = null;
  private name: string | null = null;

  constructor(
    private readonly deps: ListenWindowDeps,
    private readonly windowMs: number,
  ) {}

  /** The admin who turned the switch on (null when it went off). */
  setOwner(owner: string | null): void {
    this.owner = owner;
  }

  ownerId(): string | null {
    return this.owner;
  }

  isOpen(): boolean {
    return this.open;
  }

  private now(): number {
    return this.deps.now?.() ?? Date.now();
  }

  private log(event: string, detail: Record<string, unknown>): void {
    this.deps.log?.(event, detail);
  }

  async close(reason: string): Promise<void> {
    if (!this.open) return;
    this.open = false;
    this.log("voice.listen_window.close", { name: this.name, reason });
    if (this.deps.loopRunning()) await this.deps.stopLoop();
  }

  async tick(): Promise<void> {
    if (this.open) {
      if (!this.deps.loopRunning()) {
        // Someone ended it (/voice/stop): it is no longer ours to stop.
        this.open = false;
        this.log("voice.listen_window.close", {
          name: this.name,
          reason: "loop_stopped",
        });
      } else {
        const stoppedAt = await this.deps.readStoppedAt();
        const stopped =
          this.deps.stops() > this.lastStops ||
          (stoppedAt !== null &&
            this.stoppedAt !== null &&
            stoppedAt > this.stoppedAt);
        if (stoppedAt !== null) this.stoppedAt = stoppedAt;
        if (stopped) return this.close("stop");
        if (this.owner === null) return this.close("switch_off");
      }
    }

    const greet = await this.deps.readGreet();
    if (greet === null) return this.idle();
    if (this.open && !greet.listen) return this.close("switch_off");
    const fresh = this.lastSeq !== null && greet.seq > this.lastSeq;
    this.lastSeq = greet.seq; // the first read is only a baseline: no window for an old greeting
    if (fresh && greet.listen) {
      if (this.open) {
        this.name = greet.name;
        this.deadline = this.now() + this.windowMs;
        this.log("voice.listen_window.extend", {
          name: greet.name,
          reason: "greeted",
        });
      } else if (this.owner === null) {
        this.log("voice.listen_window.skipped", {
          name: greet.name,
          reason:
            "owner_unknown: back restarted since «Escuchar después de saludar» was turned on; turn it off and on again",
        });
      } else if (this.deps.loopRunning()) {
        this.log("voice.listen_window.skipped", {
          name: greet.name,
          reason: "voice_session_already_running",
        });
      } else {
        this.deps.startLoop(this.owner);
        this.open = true;
        this.name = greet.name;
        this.deadline = this.now() + this.windowMs;
        this.lastActivity = this.deps.activity();
        this.lastStops = this.deps.stops();
        this.stoppedAt = await this.deps.readStoppedAt();
        this.log("voice.listen_window.open", {
          name: greet.name,
          windowMs: this.windowMs,
        });
        return;
      }
    }
    return this.idle();
  }

  private async idle(): Promise<void> {
    if (!this.open) return;
    const activity = this.deps.activity();
    if (activity !== this.lastActivity || this.deps.busy()) {
      if (activity !== this.lastActivity)
        this.log("voice.listen_window.extend", {
          name: this.name,
          reason: "heard",
        });
      this.lastActivity = activity;
      this.deadline = this.now() + this.windowMs;
      return;
    }
    if (this.now() >= this.deadline) await this.close("idle");
  }
}
