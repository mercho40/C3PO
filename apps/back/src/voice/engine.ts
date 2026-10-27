/**
 * Which voice engine a session runs on — chosen by the operator per session,
 * with `VOICE_ENGINE` only supplying the default.
 *
 * Plain functions over plain inputs, so the rules are testable without booting
 * the route (which pulls in the database, the bridge client and the agent).
 *
 * ONE ENGINE AT A TIME. Both engines share the robot's one microphone and one
 * speaker. A start naming a different engine than the running one is refused,
 * not started alongside: the two would answer the same sentence twice, in two
 * voices, and each would talk over the other's playback.
 */

export type VoiceEngine = "legacy" | "openai-realtime";

type Env = Record<string, string | undefined>;

/** The deployment's default. `VOICE_ENGINE=realtime` predates per-session choice. */
export function defaultEngine(env: Env = process.env): VoiceEngine {
  return env.VOICE_ENGINE === "realtime" ? "openai-realtime" : "legacy";
}

/**
 * Engines a start can succeed on. Realtime without a key used to fail only
 * after a chat row had been created and deleted again; saying so up front lets
 * the console grey the option out with the reason instead.
 */
export function availableEngines(env: Env = process.env): VoiceEngine[] {
  return env.OPENAI_API_KEY ? ["legacy", "openai-realtime"] : ["legacy"];
}

export type EngineDecision =
  | { ok: true; engine: VoiceEngine }
  | { ok: false; status: 409; error: "other_engine_running" }
  | { ok: false; status: 503; error: "engine_unavailable" };

/** Whether a start may proceed, and on which engine. */
export function chooseEngine(input: {
  requested: VoiceEngine | undefined;
  running: VoiceEngine | null;
  fallback: VoiceEngine;
  available: readonly VoiceEngine[];
}): EngineDecision {
  const engine = input.requested ?? input.fallback;
  if (input.running !== null && input.running !== engine)
    return { ok: false, status: 409, error: "other_engine_running" };
  if (!input.available.includes(engine))
    return { ok: false, status: 503, error: "engine_unavailable" };
  return { ok: true, engine };
}
