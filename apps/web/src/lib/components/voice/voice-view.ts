/**
 * What /voice says about the voice loop — decided here, in a plain module,
 * so `bun test` can pin it (`voice-loop.svelte.ts` cannot be loaded by a test).
 *
 * Two engines report differently and the old card read only one of them:
 *
 *  - legacy (`engine: "legacy"`): the bridge transcribes, the agent answers.
 *    `conversation.phase` is idle | streaming | speaking, and the loop acts on
 *    spoken stop phrases (`stopsTriggered`).
 *  - realtime (`engine: "openai-realtime"`): speech-to-speech. The top-level
 *    `phase` is connecting | listening | speaking | error, `toolCalls` counts
 *    robot tool calls, and SPOKEN STOPS ARE INERT — back reports
 *    `stopsTriggered: null` precisely so nothing here can render a "0" that
 *    implies a guard which is not there.
 *
 * Counters are the loop's own, as it reported them. Nothing is recomputed.
 */

import type { VoiceLoopState, WireEngine } from "#lib/robot/voice-loop.js";

export type Engine = "legacy" | "realtime";

export const ENGINE_WIRE: Record<Engine, WireEngine> = {
  legacy: "legacy",
  realtime: "openai-realtime",
};

const fromWire = (wire: WireEngine | undefined): Engine =>
  wire === "openai-realtime" ? "realtime" : "legacy";

export function engineOf(state: VoiceLoopState | null): Engine {
  return fromWire(state?.engine);
}

/** What a start without a choice runs on — back's `VOICE_ENGINE`. */
export function defaultEngineOf(state: VoiceLoopState | null): Engine {
  return fromWire(state?.defaultEngine ?? state?.engine);
}

export const ENGINE_LABEL: Record<Engine, string> = {
  legacy: "Local (transcripción + agente)",
  realtime: "OpenAI Realtime (voz a voz)",
};

export type EngineOption = {
  key: Engine;
  label: string;
  /** Where the audio goes — the one fact that should decide the choice. */
  detail: string;
  /** Null when the option can be started; otherwise why not, in words. */
  unavailable: string | null;
};

/**
 * The engine picker's options. Availability is back's word, not a guess: a
 * back that predates the choice sends no `availableEngines` and ignores the
 * requested engine, so only the engine it reports is offered then.
 */
export function engineOptions(state: VoiceLoopState | null): EngineOption[] {
  const offered = (key: Engine) =>
    state?.availableEngines
      ? state.availableEngines.includes(ENGINE_WIRE[key])
      : state !== null && engineOf(state) === key;
  return [
    {
      key: "legacy",
      label: "Local",
      detail:
        "El audio no sale del robot: transcribe y habla el robot, y razona TIC AI en el campus.",
      unavailable: offered("legacy")
        ? null
        : "El servidor no ofrece este motor.",
    },
    {
      key: "realtime",
      label: "OpenAI Realtime",
      detail:
        "Voz a voz, más rápida y natural: el audio se envía a OpenAI. No hay frase de parada hablada.",
      unavailable: offered("realtime")
        ? null
        : "Falta OPENAI_API_KEY en apps/back.",
    },
  ];
}

export type StepStatus = "done" | "active" | "todo";

export type PhaseView = {
  steps: { key: string; label: string; status: StepStatus }[];
  /** The current phase in words. Null when idle. Deliberately NOT announced — see `sessionStatus`. */
  current: string | null;
  error: boolean;
};

const REALTIME_STEPS = [
  { key: "connecting", label: "Conectando" },
  { key: "listening", label: "Escuchando" },
  { key: "speaking", label: "Hablando" },
] as const;

const LEGACY_STEPS = [
  { key: "idle", label: "Escuchando" },
  { key: "streaming", label: "Pensando" },
  { key: "speaking", label: "Hablando" },
] as const;

function stepsAt(
  steps: readonly { key: string; label: string }[],
  activeKey: string | null,
): PhaseView["steps"] {
  const at = steps.findIndex((s) => s.key === activeKey);
  return steps.map((s, i) => ({
    ...s,
    status: at < 0 ? "todo" : i < at ? "done" : i === at ? "active" : "todo",
  }));
}

/**
 * The stepper. A realtime session that failed reports `phase: "error"` with
 * `running: false` — that is shown, not collapsed into "off".
 */
export function phaseView(state: VoiceLoopState | null): PhaseView {
  if (!state)
    return {
      steps: stepsAt(REALTIME_STEPS, null),
      current: null,
      error: false,
    };
  if (engineOf(state) === "realtime") {
    if (state.phase === "error") {
      return {
        steps: stepsAt(REALTIME_STEPS, null),
        current: "Error",
        error: true,
      };
    }
    const key = state.running ? (state.phase ?? null) : null;
    const steps = stepsAt(REALTIME_STEPS, key);
    return {
      steps,
      current: steps.find((s) => s.status === "active")?.label ?? null,
      error: false,
    };
  }
  const key = state.running ? (state.conversation?.phase ?? "idle") : null;
  const steps = stepsAt(LEGACY_STEPS, key);
  return {
    steps,
    current: steps.find((s) => s.status === "active")?.label ?? null,
    error: false,
  };
}

export type Counter = { label: string; value: string; hint?: string };

/**
 * Counters per engine. Realtime has no stops counter at all — see header.
 *
 * Only while a session RUNS. With nothing running there is nothing being
 * counted: legacy's counters are cumulative since back started (its `start()`
 * never resets them) and realtime's belong to a session that already ended, so
 * "0" — or "12" — beside "Apagada" read as a measurement of the present. The
 * tiles stay (the layout does not jump on start) and say "—".
 */
export function counters(state: VoiceLoopState | null): Counter[] {
  if (!state) return [];
  const live = state.running;
  const n = (v: number | null | undefined) =>
    !live || v == null ? "—" : String(v);
  if (engineOf(state) === "realtime") {
    return [
      {
        label: "Escuchado",
        value: n(state.utterancesHeard),
        hint: "Frases transcriptas en esta sesión",
      },
      {
        label: "Respuestas",
        value: n(state.agentRuns),
        hint: "Respuestas habladas en esta sesión",
      },
      {
        label: "Herramientas",
        value: n(state.toolCalls),
        hint: "Órdenes al robot en esta sesión",
      },
    ];
  }
  return [
    {
      label: "Escuchado",
      value: n(state.utterancesHeard),
      hint: "Frases oídas desde que arrancó el servidor",
    },
    {
      label: "Turnos",
      value: n(state.agentRuns),
      hint: "Turnos del agente desde que arrancó el servidor",
    },
    {
      label: "Paradas",
      value: n(state.stopsTriggered),
      hint: "Paradas por voz desde que arrancó el servidor",
    },
  ];
}

/**
 * What the counters count, in one visible line (the per-tile `title` is
 * invisible on a phone or a Quest). Legacy's do NOT restart with a session —
 * back keeps them since it booted — so a fresh session can open at "12".
 */
export function countersScope(state: VoiceLoopState | null): string | null {
  if (!state?.running) return null;
  return engineOf(state) === "realtime"
    ? "Contadores de esta sesión."
    : "Contadores acumulados desde que arrancó el servidor, no solo de esta sesión.";
}

export type StatusTone = "ok" | "warn" | "danger" | "mute";

/**
 * The hero's one status line — and its only live region.
 *
 * It changes on what an operator needs to hear (connecting, on, off, error,
 * someone else's, no robot, no current reading) and NOT on every turn: the
 * stepper's Escuchando ↔ Hablando alternation used to be announced on its own
 * live region every few seconds, which buried everything else a screen reader
 * had to say. The stepper is still there to read; it just does not talk.
 */
export function sessionStatus(input: {
  state: VoiceLoopState | null;
  /** Why the latest STATUS read failed; non-null means `state` is held. */
  reason: string | null;
  /**
   * The robot link's problem, in the shared vocabulary (`LINK[problem]`), or
   * null when the console sees the robot. A session back calls running cannot
   * hear or speak through a robot nobody can reach — it is not green.
   */
  robotLink: string | null;
}): { text: string; tone: StatusTone } {
  const s = input.state;
  if (!s)
    return { text: input.reason ? "Sin estado" : "Consultando…", tone: "mute" };
  const realtime = engineOf(s) === "realtime";
  const owner = ownership(s);
  const current: { text: string; tone: StatusTone } =
    realtime && s.phase === "error"
      ? { text: "Error", tone: "danger" }
      : !s.running
        ? { text: "Apagada", tone: "mute" }
        : owner === "other"
          ? { text: "En uso por otro operador", tone: "warn" }
          : realtime && s.phase === "connecting"
            ? { text: "Conectando…", tone: "mute" }
            : input.robotLink
              ? { text: `Activa · ${input.robotLink}`, tone: "warn" }
              : { text: "Activa", tone: "ok" };
  // A held reading keeps its words but loses its colour, and says so.
  return input.reason
    ? { text: `${current.text} (última lectura)`, tone: "mute" }
    : current;
}

/**
 * The microphone caveat, only while a session runs (when nothing runs, the
 * loop's `micEverOpen` is false by construction and saying "nothing heard"
 * contradicted the Oye panel beside it). Worded by what the bridge says the
 * mic is, never assuming push-to-talk.
 */
export function micWarning(state: VoiceLoopState | null): string | null {
  if (!state?.running || state.micEverOpen) return null;
  if (state.alwaysListening === true) {
    return "El micrófono está siempre abierto, pero todavía no llegó audio a esta sesión. Si sigue así, revisá Voz en Salud.";
  }
  if (state.alwaysListening === false) {
    return "El micrófono del robot es de pulsar para hablar: hasta que alguien apriete el botón no se oye nada, aunque haya gente hablando.";
  }
  return "Todavía no llegó audio del robot a esta sesión.";
}

export type Latency = { label: string; value: string };

/** Last-turn timings, legacy engine only (realtime reports none). */
export function lastTurnMetrics(state: VoiceLoopState | null): Latency[] {
  const t = state?.conversation?.lastTurn;
  if (!t) return [];
  const ms = (v: number | null) => (v == null ? "—" : `${Math.round(v)} ms`);
  return [
    { label: "Primer texto del modelo", value: ms(t.modelFirstDeltaMs) },
    { label: "Primera frase", value: ms(t.firstSentenceMs) },
    { label: "Primera voz", value: ms(t.firstSpeechMs) },
    { label: "Turno completo", value: ms(t.totalMs) },
  ];
}

/**
 * Who may act on the session. Realtime belongs to whoever started it; a second
 * operator sees it as in use and cannot end it (back would answer 409).
 */
export function ownership(
  state: VoiceLoopState | null,
): "none" | "yours" | "other" | "shared" {
  if (!state?.running) return "none";
  if (state.ownedByYou === true) return "yours";
  if (state.ownedByYou === false) return "other";
  return "shared";
}

/**
 * /voice's transcript list, worded honestly about what was looked at.
 *
 * Two traps the old copy fell into:
 *  - the list is the caller's `scanned` most recent voice chats (GET /chats
 *    filters by channel). When it came back full, older ones exist beyond it;
 *  - only the realtime engine saves transcripts. Telling an operator running
 *    the Local engine that sessions "se guardan cuando iniciás una sesión con
 *    el motor Realtime" read as a description of their own session.
 */
export function transcriptsCopy(input: {
  engine: Engine | null;
  count: number;
  capped: boolean;
  scanned: number;
}): { empty: string | null; scope: string | null; engine: string } {
  const engine =
    input.engine === "realtime"
      ? "Cada sesión con el motor actual (OpenAI Realtime) queda guardada acá."
      : input.engine === "legacy"
        ? "El motor actual (Local) no guarda transcripciones: solo quedan guardadas las sesiones con OpenAI Realtime."
        : "Solo quedan guardadas las sesiones con el motor OpenAI Realtime.";
  if (input.count === 0) {
    return {
      empty: "Todavía no hay conversaciones de voz guardadas en tu cuenta.",
      scope: null,
      engine,
    };
  }
  return {
    empty: null,
    scope: input.capped
      ? `Las ${input.scanned} más recientes; hay transcripciones más antiguas.`
      : null,
    engine,
  };
}
