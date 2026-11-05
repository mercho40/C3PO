/**
 * /vr-control's decisions, without the page.
 *
 * Every rule in here used to be an inline expression in a 1460-line component,
 * which is how several of them went wrong without anybody noticing: the gesture
 * gate read a posture that was stale while offline, the bring-up steps ignored
 * the posture printed right above them, a step's "Listo" survived the robot
 * falling back to damp, and the arm mirror let the operator press a button the
 * bridge had already said it would refuse. They are small rules, and each one
 * is the difference between the page telling the truth and the page sending
 * somebody in a headset to debug a robot that is fine.
 *
 * Plain module, no runes, so `bun test` can load it (tests/vr-logic.test.ts).
 * Type-only imports are erased, so `#lib` paths are safe here.
 */

import type { SkillOutcome } from "./skill-outcome";
import type { EstopPhase } from "./estop";
import type { TeleopStatus } from "#lib/teleop/stream.js";

// ---------------------------------------------------------------------------
// Posture gates
// ---------------------------------------------------------------------------

/**
 * Postures in which the robot is running a walk program.
 *
 * Walking needs one (FSM 500/501 — `walk_velocity` is refused with an RPC error
 * from anything else), and so does every dedicated gesture tool: each declares
 * `fsm_state_in_{walk,walk_waist,run}`. The firmware refuses gestures from any
 * other state with 7302, which used to arrive as a bare "Falló" — the "go debug
 * the robot" signal for a robot that is simply standing still.
 *
 * `run` is FSM 802, a label the bridge itself calls suspect; it is included
 * because a gesture has executed on hardware from exactly that id (wave,
 * 2026-08-15), and refusing to let the operator try would be the page guessing.
 */
export const WALK_FSM = ["walk", "walk_waist", "run"] as const;
export const GESTURE_FSM = WALK_FSM;

/**
 * The one-shot gestures, each its own bridge tool. ONE list for the page's
 * tiles and the headset's menu, so the two cannot offer different things.
 * `dance` (apps/bridge/src/bridge/skills/dance.py) is, like walk_velocity, not
 * yet live-tested — the catalogue's `works.real` flag drives the badge rather
 * than hiding the button.
 */
export const PRESETS = [
  { name: "wave", label: "Saludar" },
  { name: "dance", label: "Bailar" },
  { name: "shake_hand", label: "Dar la mano" },
  { name: "hug", label: "Abrazar" },
  { name: "clap", label: "Aplaudir" },
  { name: "release_arm", label: "Relajar brazos" },
] as const;

export type PresetName = (typeof PRESETS)[number]["name"];

/**
 * The headset menu's entries. `catalogueFailed` is why this is not just
 * `worksReal[name] === true`: an unreadable catalogue leaves the map empty,
 * and treating empty as "nothing is verified" is the safe reading — the menu
 * shows everything struck through and fires nothing, rather than quietly
 * offering skills whose status could not be checked.
 */
export function headsetMenu(
  worksReal: Record<string, boolean>,
  catalogueFailed: boolean,
): { name: string; label: string; verified: boolean }[] {
  return PRESETS.map((p) => ({
    name: p.name,
    label: p.label,
    verified: !catalogueFailed && worksReal[p.name] === true,
  }));
}

/**
 * Whether the robot is in a posture an action needs.
 *
 * `offline` is its own answer, not a "no": `RobotLive.state` is KEPT across
 * failures, so reading `state.posture` without `online` judged the gate on a
 * stale reading — and the warning then said "ahora está en Sin conexión", which
 * is not a posture.
 */
export type PostureGate = "ok" | "offline" | "wrong_posture";

export function postureGate(
  posture: string | null | undefined,
  online: boolean,
  allowed: readonly string[],
): PostureGate {
  if (!online) return "offline";
  return posture && allowed.includes(posture) ? "ok" : "wrong_posture";
}

// ---------------------------------------------------------------------------
// Bring-up
// ---------------------------------------------------------------------------

/**
 * One bring-up step.
 *
 * There used to be a `load` here too — "how the body behaves once this step has
 * taken" — and it coloured the "Estado actual" marker. Step 3's was `moving`, so
 * the marker on «Habilitar marcha» was cyan for any walk program, including a
 * robot standing perfectly still at zero stick. The marker now takes the LIVE
 * load (`readPosture` with measured motion): what the body is doing, not what
 * the step could make it do.
 */
export type BringUpStep = {
  name: "damp" | "prepare" | "start_walking_waist";
  label: string;
  hint: string;
};

/**
 * The order that actually worked on this robot (2026-08-15):
 * damp -> prepare (4) -> start_walking_waist (501). **501, not 500** — the two
 * are walk programs selected by waist DoF and this body reports mode_machine 5,
 * the 29-DoF/3-DoF-waist variant. 500 was the blocker for two whole sessions of
 * Start() returning success and doing nothing.
 */
export const BRING_UP_STEPS: readonly BringUpStep[] = [
  {
    name: "damp",
    label: "1 · Amortiguar",
    hint: "Punto de partida seguro. El robot se sostiene blando.",
  },
  {
    name: "prepare",
    label: "2 · Preparar",
    hint: "FSM 4. De pie y con torque; habilita el espejo de brazos.",
  },
  {
    name: "start_walking_waist",
    label: "3 · Habilitar marcha",
    hint: "FSM 501. Antes: los dos pies apoyados cargando peso (arnés flojo) y ~5 s quieto después de Preparar. Habilita caminar, girar y los gestos.",
  },
];

/** The step a posture means the robot has reached; -1 = before step 1. */
const SEQUENCE_INDEX: Record<string, number> = {
  zero_torque: -1,
  damp: 0,
  preparation: 1,
  walk: 2,
  walk_waist: 2,
  run: 2,
};

/** Known postures that are not on the sequence. Damp is the way back from any. */
const OFF_SEQUENCE = new Set([
  "squat",
  "squat_up",
  "seating",
  "lie_up",
  "climb",
  "dance",
]);

/**
 * - `done`    — a step the robot is already past.
 * - `current` — the step whose result the robot is in right now.
 * - `next`    — the one to press.
 * - `todo`    — later.
 */
export type StepMark = "done" | "current" | "next" | "todo";

export type BringUpProgress = {
  /** False when the posture cannot be placed on the sequence (offline, unknown). */
  known: boolean;
  marks: Record<BringUpStep["name"], StepMark | null>;
  /** The step to press next, or null when the sequence is complete or unknown. */
  next: BringUpStep["name"] | null;
  /** The posture is known but off the sequence (squat, seated, lying…). */
  offSequence: boolean;
};

/**
 * Place the live posture on the bring-up sequence.
 *
 * The three steps used to look identical whatever the FSM, with the posture
 * printed right above them — so the operator had to translate "Preparado" into
 * "step 2 is done" in their head, in a headset.
 */
export function bringUpProgress(
  posture: string | null | undefined,
  online: boolean,
): BringUpProgress {
  const none = {
    damp: null,
    prepare: null,
    start_walking_waist: null,
  } as BringUpProgress["marks"];
  if (!online || !posture) {
    return { known: false, marks: none, next: null, offSequence: false };
  }
  const offSequence = OFF_SEQUENCE.has(posture);
  const idx = SEQUENCE_INDEX[posture] ?? (offSequence ? -1 : null);
  if (idx === null) {
    return { known: false, marks: none, next: null, offSequence: false };
  }
  const marks = { ...none };
  BRING_UP_STEPS.forEach((step, i) => {
    marks[step.name] =
      i < idx
        ? "done"
        : i === idx
          ? "current"
          : i === idx + 1
            ? "next"
            : "todo";
  });
  const next = BRING_UP_STEPS[idx + 1]?.name ?? null;
  return { known: true, marks, next, offSequence };
}

/**
 * How long a high-danger tile must be held before it dispatches.
 *
 * The console spent its confirmation friction on logout and chat delete and
 * none on the actions that can hurt someone: "3 · Habilitar marcha" (catalogue
 * dangerLevel `high` — it puts a humanoid into a walk program) ran on one tap,
 * in a headset where a hand ray brushing past a tile is an ordinary event. A
 * modal is wrong there (it would cover PARAR); a short hold is not: a pinch held
 * for 600 ms is deliberate, a brushed tile is not.
 */
export const HOLD_CONFIRM_MS = 600;

/**
 * Used only when the catalogue could not be read: an unreadable danger level
 * must not quietly drop the friction on the one step that needs it.
 */
const FALLBACK_DANGER: Record<string, string> = {
  start_walking_waist: "high",
};

/** Whether a skill's tile must be held to confirm. */
export function needsHoldConfirm(
  name: string,
  dangerLevels: Record<string, string>,
): boolean {
  return (dangerLevels[name] ?? FALLBACK_DANGER[name]) === "high";
}

/**
 * The one bring-up step that stays dispatchable while another preset is
 * running. Damp is the safe state — the same thing a stop falls back to — so a
 * running gesture must never be the reason the operator cannot reach it.
 */
export const SAFE_STEP = "damp";

// ---------------------------------------------------------------------------
// Outcomes that know how old they are
// ---------------------------------------------------------------------------

/** An outcome, plus when it arrived and what the robot was doing then. */
export type StampedOutcome = {
  outcome: SkillOutcome;
  at: number;
  /** Posture the outcome is "about". Re-stamped during the settle window. */
  posture: string | null;
};

/**
 * How long after an outcome arrives the live posture may still change because
 * of it. A bring-up step's "Listo" CAUSES a posture change, and the 2 s /state
 * poll reports it a beat later — clearing outcomes on any posture change would
 * erase the success at the exact moment it takes effect. Inside this window the
 * outcome adopts the new posture instead of being cleared by it.
 */
export const OUTCOME_SETTLE_MS = 5000;

/** After this long an outcome is history, not state. */
export const OUTCOME_TTL_MS = 60_000;

export function stampOutcome(
  outcome: SkillOutcome,
  now: number,
  posture: string | null,
): StampedOutcome {
  return { outcome, at: now, posture };
}

/**
 * Keep an outcome only while it still describes the robot in front of you.
 *
 * "Listo" on "3 · Habilitar marcha" used to stay on screen after a PARAR or a
 * fall put the robot back in damp — a past result displayed next to live state
 * with no age. Now an outcome goes when the posture moves on (after the settle
 * window), or after the TTL. An unknown posture (offline) is no information, so
 * it neither clears nor re-stamps anything; only the TTL runs.
 */
export function pruneOutcome(
  o: StampedOutcome | null,
  now: number,
  posture: string | null,
): StampedOutcome | null {
  if (!o) return null;
  const age = now - o.at;
  if (age > OUTCOME_TTL_MS) return null;
  if (posture == null) return o;
  if (o.posture == null || age <= OUTCOME_SETTLE_MS) {
    return o.posture === posture ? o : { ...o, posture };
  }
  return o.posture === posture ? o : null;
}

/**
 * `pruneOutcome` over a record. Returns the SAME object when nothing changed,
 * so a 1 s clock calling this does not rewrite $state every second.
 */
export function pruneOutcomes(
  rec: Record<string, StampedOutcome>,
  now: number,
  posture: string | null,
): Record<string, StampedOutcome> {
  let changed = false;
  const out: Record<string, StampedOutcome> = {};
  for (const [key, o] of Object.entries(rec)) {
    const kept = pruneOutcome(o, now, posture);
    if (kept !== o) changed = true;
    if (kept) out[key] = kept;
  }
  return changed ? out : rec;
}

/**
 * The bridge's explanation, when it adds something to the label.
 *
 * `detail` is verbatim — usually English (a firmware note, an exception repr) —
 * so it is marked `en` for screen readers, except for the few details this
 * console wrote itself.
 */
export function outcomeDetail(
  o: SkillOutcome,
): { text: string; lang: "en" | "es" } | null {
  if (!o.detail || o.detail === o.code) return null;
  const ours = o.code === "bridge_unavailable" || o.code === "network";
  return { text: o.detail, lang: ours ? "es" : "en" };
}

// ---------------------------------------------------------------------------
// Walk dispatch
// ---------------------------------------------------------------------------

/**
 * How long a refused motion is held off before the loop may send it again.
 *
 * The head-yaw loop re-dispatches every 900 ms for as long as the head is
 * turned. A refusal (not in a walk program, another operator's stream holds
 * locomotion, Isaac mode) would otherwise be re-sent — and its message
 * overwritten — on every tick, forever. A fresh press of a walk button is a
 * deliberate retry and skips the hold-off.
 */
export const WALK_REFUSAL_BACKOFF_MS = 4000;

export type WalkDispatch = "move" | "stop";

/**
 * What a `walk_velocity` answer means for the page.
 *
 * - `refuse`      — a motion the robot did not perform. Clear the held button,
 *                   say why, hold off re-sending.
 * - `clear`       — a motion was accepted; any earlier refusal is out of date.
 * - `stop_failed` — the page stopped motion it had started and the stop did not
 *                   land. The firmware still halts at the end of the last pulse,
 *                   but the operator should know the explicit stop was lost.
 * - `ignore`      — nothing to tell the operator.
 *
 * The 0.5 s stop pulses are precautionary — sent on stream open, on VR exit, on
 * teardown — usually when this page never moved anything. Reporting THOSE
 * refusals would greet a non-admin with "requiere admin" for a stop they never
 * asked for. And while our own teleop stream holds locomotion the bridge refuses
 * `walk_velocity` with `teleop_active` — correctly, and the "other operator" it
 * means is us.
 */
export type WalkAction = "refuse" | "clear" | "stop_failed" | "ignore";

export function walkResultAction(
  o: SkillOutcome,
  ctx: {
    dispatch: WalkDispatch;
    /** The raw `phase` of the result body, if any. */
    phase: string | null;
    /** This page's teleop socket holds locomotion (open or stalled). */
    streamHolds: boolean;
    /** A move had been sent since the previous stop when this stop went out. */
    movedSinceStop: boolean;
  },
): WalkAction {
  const refused = o.kind === "error" || o.kind === "warn";
  if (!refused) return ctx.dispatch === "move" ? "clear" : "ignore";
  if (ctx.streamHolds && ctx.phase === "teleop_active") return "ignore";
  if (ctx.dispatch === "stop")
    return ctx.movedSinceStop ? "stop_failed" : "ignore";
  return "refuse";
}

// ---------------------------------------------------------------------------
// The console's PARAR, as this page's own locomotion sees it
// ---------------------------------------------------------------------------

/**
 * The e-stop phases in which the stop itself is still in progress — the button
 * says "Parando…". Nothing on this page may command motion while one holds: a
 * walk_velocity sent now would race the stop the operator just asked for.
 */
const STOP_IN_FLIGHT: ReadonlySet<EstopPhase> = new Set([
  "sending",
  "accepted",
]);

export function stopInFlight(phase: EstopPhase): boolean {
  return STOP_IN_FLIGHT.has(phase);
}

/**
 * Whether an e-stop phase change must halt this page's own locomotion.
 *
 * PARAR (`stop_everything`) cancels the running walk task on the bridge, and
 * the bridge does NOT block walk_velocity afterwards. This page has two writers
 * of its own that kept going through a stop: the head-yaw loop re-sent
 * walk_velocity(0, vyaw) every 900 ms for as long as the head stayed turned,
 * and a held walk button kept stepping if a tick fired before the cancelled
 * answer came back. So every press halts the page, latched (see
 * `VrControl.#haltForEstop`).
 *
 * - every press passes through `sending`, so that is the trigger;
 * - a phase that leaves `idle` without passing through it is a stop this page
 *   did not see start — halt anyway (it cannot happen today; the store could
 *   change);
 * - on mount (`prev === null`) only a stop still in flight halts: a "Detenido"
 *   left over from the dashboard is history, and latching a page that never
 *   moved would only print a re-arm instruction nobody needs.
 */
export function estopHalts(prev: EstopPhase | null, next: EstopPhase): boolean {
  if (next === prev || next === "idle") return false;
  if (next === "sending") return true;
  if (prev === "idle") return true;
  return prev === null && stopInFlight(next);
}

/**
 * Whether a precautionary zero velocity is worth sending — on leaving
 * /vr-control, and when the teleop stream takes over locomotion.
 *
 * It used to be sent on every teardown — so merely navigating away from an idle
 * page dispatched walk_velocity, which the audit log now records as a walk
 * command from somebody who never touched the robot. It is kept whenever this
 * page could have motion of its own outstanding: a move sent since the last
 * stop, a held button, the control loop running, or a live VR session (whose
 * head-yaw loop can be mid-turn).
 */
export function precautionaryStopNeeded(s: {
  movedSinceStop: boolean;
  walking: boolean;
  loopActive: boolean;
  vrActive: boolean;
}): boolean {
  return s.movedSinceStop || s.walking || s.loopActive || s.vrActive;
}

/**
 * Whether the bridge is driving real hardware, for deciding whether a camera
 * connection is worth opening.
 *
 * `liveEnv` is null whenever the link is down. This used to fall back to the
 * PAGE-LOAD reading, captured once per full load — so a single timed-out poll
 * after the bridge came up in `real` unmounted the camera panel and the headset
 * said the camera "ni siquiera se intentó" with a picture on screen. The
 * fallback is the last reading the poller actually got (it keeps `state`
 * across failures), which only falls back to the seed if nothing live ever
 * arrived.
 */
export function isRealHardware(
  liveEnv: string | null | undefined,
  lastEnv: string | null | undefined,
): boolean {
  return (liveEnv ?? lastEnv ?? null) === "real";
}

// ---------------------------------------------------------------------------
// Arm mirror
// ---------------------------------------------------------------------------

export type ArmReadinessKind =
  | "no_stream"
  | "unknown"
  | "disabled_env"
  | "not_real"
  | "enter_vr"
  | "ready"
  | "engaged";

export type ArmReadiness = {
  kind: ArmReadinessKind;
  text: string;
  /** Whether "Activar espejo" may be pressed. Turning it OFF is always allowed. */
  canEngage: boolean;
};

/**
 * Whether the arm mirror can engage, from what the bridge already reports.
 *
 * The status frame has carried `arm.enabled_by_env` and `arm.sim_mode` all
 * along. The page ignored both: a static footnote claimed rt/arm_sdk "viene
 * deshabilitado" even with TELEOP_ARM_ENABLED=1, and the operator could press
 * "Activar espejo" only to get the bridge's English exception back. Every gate
 * the bridge would refuse on is checked here first, in Spanish, before the
 * press — the bridge stays the authority (its own preconditions still run and
 * `arm_error` is still shown), this just stops the page offering a dead button.
 */
export function armReadiness(input: {
  streaming: boolean;
  vrActive: boolean;
  status: TeleopStatus | null;
}): ArmReadiness {
  const { streaming, vrActive, status } = input;
  if (!streaming) {
    return {
      kind: "no_stream",
      text: "Conectá el puente primero.",
      canEngage: false,
    };
  }
  if (status?.arm.enabled_by_env === false) {
    return {
      kind: "disabled_env",
      text: "Deshabilitado en el puente (TELEOP_ARM_ENABLED no está en 1): falta verificar los signos de las articulaciones con el robot a la vista.",
      canEngage: false,
    };
  }
  if (status && status.arm.sim_mode !== "real") {
    return {
      kind: "not_real",
      text: `El puente está en modo ${status.arm.sim_mode}: el espejo de brazos solo funciona con el robot real.`,
      canEngage: false,
    };
  }
  if (!vrActive) {
    return {
      kind: "enter_vr",
      text: "Entrá en VR primero: el espejo copia tus manos desde el visor.",
      canEngage: false,
    };
  }
  if (!status) {
    // Status arrives about twice a second; until it does the bridge's own
    // preconditions are the only check, and they will still say no if needed.
    return {
      kind: "unknown",
      text: "Esperando el estado del puente…",
      canEngage: true,
    };
  }
  if (status.arm.engaged) {
    return {
      kind: "engaged",
      text: `Brazos tomados · peso ${status.arm.weight}`,
      canEngage: true,
    };
  }
  return { kind: "ready", text: "Listo para activar.", canEngage: true };
}

/** The bridge's hand-driver names, for an operator. */
export const HANDS_LABEL: Record<string, string> = {
  none: "Sin manos",
  brainco: "BrainCo",
  dex3: "Dex3",
};

/** `known: false` means `text` is the bridge's raw token, shown verbatim. */
export function handsLabel(raw: string | null | undefined): {
  text: string;
  known: boolean;
} {
  if (!raw) return { text: "—", known: true };
  const label = HANDS_LABEL[raw];
  return label ? { text: label, known: true } : { text: raw, known: false };
}

// ---------------------------------------------------------------------------
// WebXR entry
// ---------------------------------------------------------------------------

export type XrErrorCopy = {
  /** Spanish, actionable. */
  text: string;
  /** The browser's own message, verbatim (English), as a secondary readout. */
  raw: string | null;
};

const XR_ERROR_TEXT: Record<string, string> = {
  NotSupportedError:
    "Este navegador o visor no soporta la sesión inmersiva que se pidió.",
  SecurityError:
    "El navegador bloqueó WebXR: la página tiene que abrirse por HTTPS o desde localhost.",
  NotAllowedError:
    "Se rechazó el permiso para entrar en VR. Aceptá el aviso del visor e intentá de nuevo.",
  InvalidStateError:
    "Ya hay una sesión inmersiva abierta. Salí de ella antes de entrar otra vez.",
};

/**
 * Turn whatever `requestSession` threw into operator copy.
 *
 * The browser's DOMException message is English and phrased for developers
 * ("The specified session configuration is not supported"); it used to be the
 * whole error line. Known names get a Spanish sentence with the raw message kept
 * underneath for whoever debugs it. Our own errors (xr-teleop throws plain
 * `Error`s already written in Spanish) pass through as they are.
 */
export function xrErrorCopy(err: unknown): XrErrorCopy {
  const name =
    err && typeof err === "object" && "name" in err
      ? String((err as { name: unknown }).name)
      : "";
  const message =
    err && typeof err === "object" && "message" in err
      ? String((err as { message: unknown }).message)
      : typeof err === "string"
        ? err
        : "";
  const known = XR_ERROR_TEXT[name];
  if (known) return { text: known, raw: message || name };
  if (name === "Error" && message) return { text: message, raw: null };
  return {
    text: "No se pudo iniciar la sesión VR.",
    raw: message || name || null,
  };
}

/**
 * Whether the page is reaching its server through `adb reverse`.
 *
 * The USB-specific diagnosis ("se perdió el enlace USB… el robot está bien")
 * is only true for a headset loading the console from `localhost` over the
 * cable. On a desktop the same failed probe means the console server is down,
 * and blaming a cable that does not exist sends the operator the wrong way.
 */
export function isAdbLink(hostname: string, vrSupported: boolean): boolean {
  return vrSupported && (hostname === "localhost" || hostname === "127.0.0.1");
}

// ---------------------------------------------------------------------------
// Bridge mode
// ---------------------------------------------------------------------------

/**
 * What the env banner should say.
 *
 * - `offline`  — we cannot see the bridge, so its mode is UNKNOWN. Saying "not
 *                in real mode" here was a claim about a thing nobody could see.
 * - `not_real` — the bridge answered and said stub/isaac.
 * - null       — real hardware, confirmed live.
 */
export type EnvBanner =
  | { kind: "offline"; lastEnv: string | null }
  | { kind: "not_real"; env: string | null }
  | null;

/** The bridge's SIM_MODE, for an operator. Unknown values pass through. */
export function envLabel(env: string | null | undefined): string {
  if (!env) return "desconocido";
  return (
    { real: "robot real", isaac: "Isaac Sim", stub: "stub (simulado)" }[env] ??
    env
  );
}

/** The teleop socket's state in two words — for the in-headset strip. */
export function teleopShort(
  state: "connecting" | "open" | "stalled" | "closed" | "error",
): string {
  return {
    connecting: "conectando…",
    open: "conectado",
    stalled: "sin respuesta",
    closed: "desconectado",
    error: "desconectado",
  }[state];
}

export function envBanner(
  online: boolean,
  liveEnv: string | null | undefined,
  lastEnv: string | null | undefined,
): EnvBanner {
  if (!online) return { kind: "offline", lastEnv: lastEnv ?? null };
  if (liveEnv === "real") return null;
  return { kind: "not_real", env: liveEnv ?? null };
}

// ---------------------------------------------------------------------------
// Catalogue
// ---------------------------------------------------------------------------

export type CatalogueHealth = {
  /** No usable catalogue: the `works.real` flags are unknown, not true. */
  failed: boolean;
  /** Seconds old, when the catalogue is a cached copy; null when live. */
  staleAgeS: number | null;
  /** Why, verbatim from apps/back (English), when there is a reason. */
  error: string | null;
};

/**
 * Read GET /skills's envelope honestly.
 *
 * apps/back's `getCatalogue()` deliberately never throws: with the bridge down
 * it answers HTTP 200 with `skills: []` and a `bridge_error` (or a cached copy
 * with `source: "cache"`). Keying the failure on the HTTP error alone meant the
 * flag was never set in the very case it was written for, so every "Sin probar
 * en real" badge vanished silently on a cold start.
 */
export function catalogueHealth(input: {
  httpFailed: boolean;
  bridgeError?: string | null;
  skillCount: number;
  source?: string | null;
  ageSeconds?: number | null;
}): CatalogueHealth {
  if (input.httpFailed) {
    return { failed: true, staleAgeS: null, error: input.bridgeError ?? null };
  }
  if (input.skillCount === 0) {
    return {
      failed: true,
      staleAgeS: null,
      error: input.bridgeError ?? "empty_catalogue",
    };
  }
  if (input.source === "cache") {
    return {
      failed: false,
      staleAgeS: input.ageSeconds ?? null,
      error: input.bridgeError ?? null,
    };
  }
  return { failed: false, staleAgeS: null, error: null };
}

/**
 * Which badge a skill tile gets.
 *
 * `untested` — the bridge says nobody has watched it run on the robot.
 * `unknown`  — we could not read the flag. A missing entry must never read as
 *              verified; that was the bug behind the vanished badges.
 */
export function realBadge(
  worksReal: Record<string, boolean>,
  name: string,
): "untested" | "unknown" | null {
  const v = worksReal[name];
  if (v === true) return null;
  return v === false ? "untested" : "unknown";
}

// ---------------------------------------------------------------------------
// The firmware's gesture catalogue
// ---------------------------------------------------------------------------

/**
 * Every preset action the robot's own GetActionList reports (23), by firmware
 * name.
 *
 * SOURCE OF TRUTH: `apps/bridge/src/bridge/sdk/g1_protocol.py` (`Gesture`),
 * exposed by `apps/bridge/src/bridge/skills/gesture.py` (`GESTURE_CATALOGUE`).
 * This is a mirror, used only because the bridge's `gesture` tool types `name`
 * as a free string (the list lives in its description), so the catalogue schema
 * carries no enum. When the bridge makes `name` a Literal, `gestureNamesFromSchema`
 * picks the list up from GET /skills and this copy stops being read.
 */
export const GESTURE_CATALOGUE_FALLBACK = [
  "blow_kiss_both_hands",
  "blow_kiss_left_hand",
  "blow_kiss_right_hand",
  "both_hands_up",
  "both_hands_up_deviate_right",
  "box_both_hand_win",
  "box_left_hand_win",
  "box_right_hand_win",
  "clamp",
  "forward_push",
  "heart_both_hands",
  "heart_right_hand",
  "high_five",
  "hug",
  "refuse",
  "release_arm",
  "right_hand_on_heart",
  "right_hand_up",
  "shake_hand",
  "turn_back_wave",
  "ultraman_ray",
  "wave_above_head",
  "wave_under_head",
] as const;

export const GESTURE_LABELS: Record<string, string> = {
  blow_kiss_both_hands: "Beso (dos manos)",
  blow_kiss_left_hand: "Beso (mano izquierda)",
  blow_kiss_right_hand: "Beso (mano derecha)",
  both_hands_up: "Manos arriba",
  both_hands_up_deviate_right: "Manos arriba, a la derecha",
  box_both_hand_win: "Festejo de boxeo (dos manos)",
  box_left_hand_win: "Festejo de boxeo (izquierda)",
  box_right_hand_win: "Festejo de boxeo (derecha)",
  clamp: "Aplaudir",
  forward_push: "Empujar hacia adelante",
  heart_both_hands: "Corazón (dos manos)",
  heart_right_hand: "Corazón (mano derecha)",
  high_five: "Chocar los cinco",
  hug: "Abrazar",
  refuse: "Rechazar",
  release_arm: "Relajar brazos",
  right_hand_on_heart: "Mano al corazón",
  right_hand_up: "Mano derecha arriba",
  shake_hand: "Dar la mano",
  turn_back_wave: "Saludo de espaldas",
  ultraman_ray: "Rayo Ultraman",
  wave_above_head: "Saludar (arriba)",
  wave_under_head: "Saludar (abajo)",
};

/**
 * Per-action gating the firmware reports, as operator hints. Only one action in
 * the whole table is FSM-gated (`g1_protocol.ACTION_REQUIRES_FSM`); the
 * mode_machine-gated ones need a 29/27-DoF body, which this robot is, so they
 * carry no hint.
 */
export const GESTURE_HINTS: Record<string, string> = {
  turn_back_wave: "Necesita el programa de marcha (paso 3).",
};

/**
 * What every gesture tile says about itself while the posture gate is shut.
 *
 * The panel's callout already said the firmware would refuse them (7302), and
 * the tiles below it looked exactly as pressable as ever — so the warning and
 * the buttons contradicted each other. The tiles are NOT disabled (the gate
 * warns, it does not block: the posture can be stale, and the firmware stays
 * the authority); they are dimmed and carry the reason themselves, so a hand
 * ray aimed at one tile reads why without scrolling back up.
 *
 * Offline is no caveat: the posture is unknown, not known to be wrong.
 */
export function gestureCaveat(gate: PostureGate): string | null {
  return gate === "wrong_posture" ? "Necesita marcha (paso 3)" : null;
}

/**
 * Firmware actions that already have a dedicated tile in the preset grid,
 * mapped from the tool that dispatches them (each tool's docstring in
 * apps/bridge/src/bridge/mcp_server.py names its id). Listing them again under
 * "Más gestos" would put two "Abrazar" buttons on one screen with no way to tell
 * them apart.
 */
export const PRESET_FIRMWARE: Record<string, string> = {
  wave: "wave_above_head",
  clap: "clamp",
  hug: "hug",
  shake_hand: "shake_hand",
  release_arm: "release_arm",
};

/** `name.enum` from the `gesture` tool's JSON Schema, if the bridge declares one. */
export function gestureNamesFromSchema(parameters: unknown): string[] | null {
  if (!parameters || typeof parameters !== "object") return null;
  const props = (parameters as { properties?: unknown }).properties;
  if (!props || typeof props !== "object") return null;
  const name = (props as Record<string, unknown>).name;
  if (!name || typeof name !== "object") return null;
  const values = (name as { enum?: unknown }).enum;
  if (!Array.isArray(values)) return null;
  const names = values.filter((v): v is string => typeof v === "string");
  return names.length ? names : null;
}

export type MoreGesture = {
  name: string;
  label: string;
  /** False when `label` is the firmware's own token (a name this file lacks). */
  known: boolean;
  hint: string | null;
};

/** The "Más gestos" grid: the catalogue minus what the preset grid already has. */
export function moreGestures(
  names: readonly string[],
  presetNames: readonly string[],
): MoreGesture[] {
  const covered = new Set(
    presetNames.map((p) => PRESET_FIRMWARE[p]).filter(Boolean),
  );
  return names
    .filter((n) => !covered.has(n))
    .map((n) => ({
      name: n,
      label: GESTURE_LABELS[n] ?? n,
      known: n in GESTURE_LABELS,
      hint: GESTURE_HINTS[n] ?? null,
    }));
}
