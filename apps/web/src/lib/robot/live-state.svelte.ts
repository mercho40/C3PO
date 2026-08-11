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
 */

import { browser } from "$app/environment";
import { createApi } from "$lib/api";
import type { RobotState } from "@back/routes/state";

export type PoseXY = { x: number; y: number };

/**
 * Map a world-frame pose (metres) to a percentage position on a centred canvas.
 * Origin sits at the middle; +x → right, +y → up. `scale` is percent of the
 * canvas per metre. Clamped so the marker never leaves the frame.
 */
export function projectPose(
  x: number,
  y: number,
  scale = 6,
): { left: number; top: number } {
  return {
    left: Math.min(96, Math.max(4, 50 + x * scale)),
    top: Math.min(96, Math.max(4, 50 - y * scale)),
  };
}

export class RobotLive {
  state = $state<RobotState | null>(null);
  online = $state(false);
  latencyMs = $state<number | null>(null);
  /** Cumulative path length in metres, integrated from polled pose deltas. */
  distanceM = $state(0);
  /** Recent world-frame positions, oldest → newest (capped). */
  trail = $state<PoseXY[]>([]);

  #timer: ReturnType<typeof setInterval> | null = null;
  #last: PoseXY | null = null;

  constructor(initial?: RobotState | null, online = false) {
    if (initial) {
      this.state = initial;
      this.online = online;
      this.#ingest(initial);
    }
  }

  #ingest(s: RobotState) {
    const p = s.pose;
    if (!p) return;
    const pt = { x: p.x_meters_world, y: p.y_meters_world };
    if (this.#last) {
      const d = Math.hypot(pt.x - this.#last.x, pt.y - this.#last.y);
      // Ignore sub-2cm jitter so a stationary robot doesn't accrue distance.
      if (d > 0.02) this.distanceM += d;
    }
    this.#last = pt;
    this.trail = [...this.trail, pt].slice(-80);
  }

  async #tick() {
    const started = Date.now();
    try {
      const { data, error } = await createApi(fetch).state.get();
      this.latencyMs = Date.now() - started;
      if (error || !data) {
        this.online = false;
        return;
      }
      this.online = true;
      this.state = data as RobotState;
      this.#ingest(data as RobotState);
    } catch {
      this.online = false;
    }
  }

  /** Begin polling. No-op on the server or if already running. */
  start(intervalMs = 2000) {
    if (!browser || this.#timer) return;
    void this.#tick();
    this.#timer = setInterval(() => void this.#tick(), intervalMs);
  }

  stop() {
    if (this.#timer) clearInterval(this.#timer);
    this.#timer = null;
  }
}
