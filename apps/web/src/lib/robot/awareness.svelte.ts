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
import { LINK_DETAIL, linkFromError } from "./link-copy";
import { Poller, type PollResult } from "./poller.svelte";

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

/**
 * Which reads this instance makes. /voice renders only the "hears" half, and
 * fetching the D7 scene every second there was perception work on the
 * robot's Jetson for a panel nobody drew.
 */
export type AwarenessMode = "all" | "sees" | "hears";

/** Why a body that arrived could not be read. */
const UNREADABLE = "La respuesta del servidor llegó incompleta o no es válida.";

/**
 * Pauses while the tab is hidden: a background dashboard has nobody reading it,
 * and these two reads are perception and audio work on the robot's Jetson.
 */
export class Awareness extends Poller {
  surroundings = $state<Surroundings | null>(null);
  voice = $state<Voice | null>(null);
  /** Why there is no CURRENT scene, in words an operator can act on. Null when fine. */
  reason = $state<string | null>(null);
  /** Distinguishes "the robot reports nothing" from "we cannot reach back". */
  unreachable = $state(false);
  /**
   * The bridge answered 503: it is up but could not build a snapshot. Any
   * `surroundings` still held is the LAST scene, not the current one, and must
   * be presented as such — its ages are frozen at the moment it was received.
   */
  degraded = $state(false);
  /** Why there is no voice telemetry, when there isn't. */
  voiceReason = $state<string | null>(null);
  /**
   * The bridge's own words behind a 503 — English, verbatim ("could not build
   * a snapshot"). Kept apart from the Spanish `reason` so the panel can mark
   * it `lang="en"` instead of mixing two languages in one sentence.
   */
  bridgeReason = $state<string | null>(null);
  voiceBridgeReason = $state<string | null>(null);

  readonly mode: AwarenessMode;

  constructor(mode: AwarenessMode = "all") {
    super({ timeoutMs: 3000, pauseWhenHidden: true });
    this.mode = mode;
  }

  /** True when perception is running well enough that an empty scene means
   *  something. Read this before showing "nothing detected" as reassurance. */
  get detectorOnline(): boolean {
    return (
      !this.degraded &&
      !this.unreachable &&
      this.surroundings?.sources?.detector === "ok"
    );
  }

  /** Objects sorted nearest-first — the order an operator scans in. */
  get objects(): SeenObject[] {
    return [...(this.surroundings?.objects ?? [])].sort(
      (a, b) => a.range_m - b.range_m,
    );
  }

  protected async poll(signal: AbortSignal): Promise<PollResult> {
    const wantScene = this.mode !== "hears";
    const wantVoice = this.mode !== "sees";
    let scene: Response | null;
    let voice: Response | null;
    try {
      [scene, voice] = await Promise.all([
        wantScene
          ? fetch(`${PUBLIC_API_URL}/telemetry/surroundings`, {
              credentials: "include",
              signal,
            })
          : null,
        wantVoice
          ? fetch(`${PUBLIC_API_URL}/telemetry/voice`, {
              credentials: "include",
              signal,
            })
          : null,
      ]);
    } catch {
      const why = signal.aborted
        ? "El servidor tardó demasiado en responder."
        : LINK_DETAIL.noApi;
      if (wantScene) {
        this.reason = why;
        this.unreachable = true;
        this.degraded = false;
        this.bridgeReason = null;
      }
      if (wantVoice) {
        this.voiceReason = why;
        this.voiceBridgeReason = null;
      }
      return "error";
    }

    if (scene?.status === 401 || voice?.status === 401) return "unauthorized";

    const sceneOk = scene ? await this.#readScene(scene, signal) : true;
    const voiceOk = voice ? await this.#readVoice(voice, signal) : true;

    // The poll's verdict follows the scene when there is one (the voice half
    // is secondary there, as it always was); a voice-only instance has
    // nothing else to judge by. A 503 counts as an answer either way.
    return (scene ? sceneOk : voiceOk) ? "ok" : "error";
  }

  /** Apply a scene answer. False when there is no scene AND no honest 503. */
  async #readScene(scene: Response, signal: AbortSignal): Promise<boolean> {
    if (scene.ok) {
      // Read inside a try: a body that fails mid-read used to throw past the
      // poll into Poller's catch, which only counts a failure — so `reason`
      // stayed null and the OLD scene kept reading as current.
      let body: Surroundings;
      try {
        body = (await scene.json()) as Surroundings;
      } catch {
        this.reason = signal.aborted
          ? "El servidor tardó demasiado en responder."
          : UNREADABLE;
        this.bridgeReason = null;
        this.unreachable = true;
        this.degraded = false;
        return false;
      }
      this.surroundings = body;
      this.reason = null;
      this.bridgeReason = null;
      this.unreachable = false;
      this.degraded = false;
      return true;
    }
    if (scene.status === 503) {
      // A real answer: the bridge is up and has nothing to report. The last
      // scene is kept so the operator can still see it — but flagged, so the
      // panel can label it as history instead of the current world.
      const body = await scene.json().catch(() => ({}));
      this.reason = "El puente no pudo armar una escena nueva.";
      this.bridgeReason = (body as { error?: string }).error ?? null;
      this.unreachable = false;
      this.degraded = true;
      return true;
    }
    const body = await scene.json().catch(() => null);
    this.reason = LINK_DETAIL[linkFromError(scene.status, body)];
    this.bridgeReason = null;
    this.unreachable = true;
    this.degraded = false;
    return false;
  }

  /** Apply a voice answer. False when there is no answer worth the name. */
  async #readVoice(voice: Response, signal: AbortSignal): Promise<boolean> {
    if (voice.ok) {
      try {
        this.voice = (await voice.json()) as Voice;
        this.voiceReason = null;
        this.voiceBridgeReason = null;
        return true;
      } catch {
        // Same rule as the scene: an unreadable answer is not the last one.
        this.voice = null;
        this.voiceReason = signal.aborted
          ? "El servidor tardó demasiado en responder."
          : UNREADABLE;
        this.voiceBridgeReason = null;
        return false;
      }
    }
    const body = await voice.json().catch(() => null);
    this.voice = null;
    if (voice.status === 503) {
      this.voiceReason = "El puente no tiene telemetría de voz.";
      this.voiceBridgeReason =
        (body as { error?: string } | null)?.error ?? null;
      return true;
    }
    this.voiceReason = LINK_DETAIL[linkFromError(voice.status, body)];
    this.voiceBridgeReason = null;
    return false;
  }

  start(pollMs: number = DEFAULT_POLL_MS): void {
    super.start(pollMs);
  }
}
