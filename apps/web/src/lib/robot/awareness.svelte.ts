/**
 * What the robot SEES and HEARS, polled for the operator console.
 *
 *   back  GET /telemetry/surroundings  ->  the D7 world-model snapshot
 *   back  GET /telemetry/voice         ->  recent speech + whether it can hear
 *
 * Both are the bridge's own telemetry, proxied behind Better Auth. The snapshot
 * is the SAME object the agent is handed — not a console-specific view — so an
 * operator and the agent can never be looking at different worlds while
 * debugging the same incident.
 *
 * NOTHING HERE IS RECOMPUTED. Staleness, offline-ness and ages all come from
 * the bridge as it reported them. A console that re-derives "is this stale?"
 * from a local clock will eventually disagree with the robot about reality, and
 * the operator has no way to tell which one is wrong.
 *
 * THE CENTRAL DISTINCTION THIS UI EXISTS TO PRESERVE: an empty list of objects
 * is NOT a clear path. D7 sends `sources.detector = "offline"` and a
 * plain-language note for exactly that case, and both are surfaced rather than
 * rendered as a reassuring empty state.
 */

import { PUBLIC_API_URL } from "$env/static/public";

/** One thing perception reports, egocentric: 0° ahead, POSITIVE LEFT. */
export type SeenObject = {
  label: string;
  range_m: number;
  bearing_deg: number;
  confidence?: number;
  age_s?: number;
};

/** Per-source health. "offline" is a first-class answer, never absence. */
export type SourceStatus = "ok" | "stale" | "offline";

export type Surroundings = {
  version: number;
  sources: Record<string, SourceStatus>;
  objects?: SeenObject[];
  objects_omitted?: number;
  free_space?: Record<string, number>;
  landmarks?: SeenObject[];
  notes?: string[];
  pose?: Record<string, number>;
  report_age_s?: number;
};

export type HeardItem = {
  text: string;
  kind: "speech" | "stop";
  age_s: number;
};

export type Voice = {
  running: boolean;
  error: string | null;
  /** False means push-to-talk: silence usually means nobody held the button. */
  always_listening: boolean;
  audio_source: string;
  mic_ever_open: boolean;
  seconds_since_audio: number | null;
  utterances: number;
  recent: HeardItem[];
};

const DEFAULT_POLL_MS = 1000;

export class Awareness {
  surroundings = $state<Surroundings | null>(null);
  voice = $state<Voice | null>(null);
  /** Why there is nothing, in words an operator can act on. Null when fine. */
  reason = $state<string | null>(null);
  /** Distinguishes "the robot reports nothing" from "we cannot reach back". */
  unreachable = $state(false);

  #timer: ReturnType<typeof setInterval> | null = null;
  #inFlight = false;

  /** True when perception is running well enough that an empty scene means
   *  something. Read this before showing "nothing detected" as reassurance. */
  get detectorOnline(): boolean {
    return this.surroundings?.sources?.detector === "ok";
  }

  /** Objects sorted nearest-first — the order an operator scans in. */
  get objects(): SeenObject[] {
    return [...(this.surroundings?.objects ?? [])].sort(
      (a, b) => a.range_m - b.range_m,
    );
  }

  async #tick(): Promise<void> {
    // A slow response must never stack polls; at 1 Hz that becomes a queue of
    // requests describing moments that have already passed.
    if (this.#inFlight) return;
    this.#inFlight = true;
    try {
      const [scene, voice] = await Promise.all([
        fetch(`${PUBLIC_API_URL}/telemetry/surroundings`, {
          credentials: "include",
        }),
        fetch(`${PUBLIC_API_URL}/telemetry/voice`, { credentials: "include" }),
      ]);

      if (scene.ok) {
        this.surroundings = (await scene.json()) as Surroundings;
        this.reason = null;
        this.unreachable = false;
      } else if (scene.status === 503) {
        // A real answer: the bridge is up and has nothing to report. Keep the
        // last scene on screen — its ages keep counting, which is honest —
        // and say why rather than blanking to a false empty world.
        const body = await scene.json().catch(() => ({}));
        this.reason = (body as { error?: string }).error ?? "no perception";
        this.unreachable = false;
      } else {
        this.reason = "bridge unreachable";
        this.unreachable = true;
      }

      this.voice = voice.ok ? ((await voice.json()) as Voice) : null;
    } catch {
      this.reason = "console cannot reach the API";
      this.unreachable = true;
    } finally {
      this.#inFlight = false;
    }
  }

  start(pollMs: number = DEFAULT_POLL_MS): void {
    if (this.#timer !== null) return;
    void this.#tick();
    this.#timer = setInterval(() => void this.#tick(), pollMs);
  }

  stop(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }
}
