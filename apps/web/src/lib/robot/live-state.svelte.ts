/**
 * Client-side live robot state.
 *
 * Polls the backend `GET /state` (which proxies the bridge's `get_state`) on an
 * interval and exposes it as runes so any console page can bind to a single,
 * always-fresh source of truth: pose, battery, posture, faults, latency, plus a
 * derived path (trail + cumulative distance) integrated from the pose stream.
 *
 * Seed it from a server `load` (`new RobotLive(data.state, data.online)`) for an
 * instant first paint, then call `start()` in `onMount` to go live.
 *
 * `online` is honest now: every request has a timeout (see `Poller`), so a
 * bridge that stalls instead of refusing turns into "Sin conexión" within a few
 * seconds rather than leaving the last reading on screen as current. `state` is
 * KEPT across failures on purpose — panels show it as the last reading — so
 * anything rendering it must check `online` before presenting it as live.
 */

import { createApi } from "$lib/api";
import { linkFromError, type LinkProblem } from "./link-copy";
import { Poller, type PollResult } from "./poller.svelte";
import type { RobotState } from "@back/routes/state";

export type PoseXY = { x: number; y: number };

/** Why the console has no current state. Null while online. */
export type { LinkProblem };

/** A pose must move at least this far to count as travel or a trail point. */
const JITTER_M = 0.05;
const TRAIL_MAX = 240;

export class RobotLive extends Poller {
  state = $state<RobotState | null>(null);
  online = $state(false);
  /** Why there is no current state, in the shared link vocabulary. */
  problem = $state<LinkProblem | null>(null);
  latencyMs = $state<number | null>(null);
  /** Cumulative path length in metres, integrated from polled pose deltas. */
  distanceM = $state(0);
  /** Recent world-frame positions, oldest → newest (capped). */
  trail = $state<PoseXY[]>([]);

  #last: PoseXY | null = null;

  constructor(initial?: RobotState | null, online = false) {
    // The stop button reads `online`; it must never wait out a hung bridge.
    super({ timeoutMs: 3500 });
    if (initial) {
      this.state = initial;
      this.online = online;
      this.#ingest(initial);
    }
    if (!online) this.problem = "offline";
  }

  /** True when the bridge says every value is fabricated (stub mode). */
  get simulated(): boolean {
    return this.state?.stub === true;
  }

  #ingest(s: RobotState) {
    const p = s.pose;
    if (!p) return;
    const pt = { x: p.x_meters_world, y: p.y_meters_world };
    if (this.#last) {
      const d = Math.hypot(pt.x - this.#last.x, pt.y - this.#last.y);
      // Ignore jitter so a stationary robot neither accrues distance nor
      // flushes its own trail: appending the same point every tick used to
      // collapse the "travelled path" to a dot after ~3 minutes standing still.
      if (d <= JITTER_M) return;
      this.distanceM += d;
    }
    this.#last = pt;
    this.trail = [...this.trail, pt].slice(-TRAIL_MAX);
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const started = Date.now();
    let res: Awaited<ReturnType<ReturnType<typeof createApi>["state"]["get"]>>;
    try {
      res = await createApi(fetch).state.get({ fetch: { signal } });
    } catch {
      this.online = false;
      this.problem = signal.aborted ? "offline" : "noApi";
      return "error";
    }
    this.latencyMs = Date.now() - started;
    const { data, error } = res;
    // Eden never throws for a failed fetch: it returns `{ status: 503, value:
    // <the thrown Error> }`. A real 503 from back carries a JSON body, so an
    // Error value is the browser failing to reach back at all — or the
    // timeout firing — and must not be reported as a bridge problem.
    if (error?.value instanceof Error) {
      this.online = false;
      this.problem = signal.aborted ? "offline" : "noApi";
      return "error";
    }
    if (error || !data) {
      const status = error ? (error.status as number) : null;
      this.online = false;
      this.problem = linkFromError(status, error?.value);
      return status === 401 ? "unauthorized" : "error";
    }
    this.online = true;
    this.problem = null;
    this.state = data as RobotState;
    this.#ingest(data as RobotState);
    return "ok";
  }

  /** Forget the travelled path (the odometer and trail), keeping the state. */
  resetPath(): void {
    this.distanceM = 0;
    this.trail = this.#last ? [this.#last] : [];
  }
}
