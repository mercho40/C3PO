/**
 * What the e-stop knows — the decisions behind the PARAR button, as plain logic.
 *
 * The button used to keep its own `outcome` per instance and decide everything
 * from the HTTP status alone. Three things went wrong with that, all of them in
 * the direction this console must never err:
 *
 *  - An unreachable bridge read as "Reintentar". back stopped answering 404 for
 *    this skill (stop_everything skips the catalogue lookup so a cold cache
 *    cannot block it), so the only path to "Sin puente — usá la parada física"
 *    was dead, and the operator was told to retry a button that could not work.
 *  - A 200 whose damp fallback ran out of retries (`real_damp_fallback_succeeded:
 *    false`) was shown as an accepted stop.
 *  - A 6 s timer cleared EVERY accepted press — "Sin confirmar" and "Parando…"
 *    included — back to a calm "Parar", while the robot might still be walking.
 *
 * And /vr-control mounts a second StopButton inside the headset's dom-overlay (the
 * only stop visible while the headset is on), so two buttons on one screen could
 * disagree about the same press. The press now lives in one store
 * (`estop.svelte.ts`); this module is everything it decides, testable.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import { classifyInvoke, type SkillOutcome } from "./skill-outcome";

/**
 * - `idle`        — nothing pressed (or a confirmed stop has cleared).
 * - `sending`     — the request is in flight.
 * - `accepted`    — 200, waiting for a telemetry reading taken AFTER the order.
 * - `stopped`     — that reading shows the robot out of a locomotion mode.
 * - `unconfirmed` — 200, but telemetry is down: nobody can see whether it stopped.
 * - `unverifiable` — 200 and telemetry is up, but its posture cannot vouch for
 *                   anything: unknown (no FSM id — the post-reboot race),
 *                   stale (`stale_fsm_*`), or fabricated (stub). Not moving
 *                   is not the same as known to be stopped.
 * - `holdingGait` — zero velocity went out, and the robot is still in its
 *                   walking controller BY DESIGN: in sim (no damp there), and on
 *                   real hardware when the robot acknowledged the zero-velocity
 *                   burst, so the bridge left it standing instead of damping it
 *                   (`stopHeldGait`). Posture cannot confirm the stop. Not an
 *                   alarm; not a pass.
 * - `stillMoving` — real hardware, still in a locomotion mode well after the 200.
 * - `notDamped`   — 200, but the bridge says the damp fallback failed.
 * - `unreachable` — the order never left: bridge down, back down, network.
 * - `noAnswer`    — the request timed out; nobody knows whether it arrived.
 * - `rejected`    — back or the bridge returned an error for the stop itself.
 * - `expired`     — 401: the order did not go out because the session lapsed.
 */
export type EstopPhase =
  | "idle"
  | "sending"
  | "accepted"
  | "stopped"
  | "unconfirmed"
  | "unverifiable"
  | "holdingGait"
  | "stillMoving"
  | "notDamped"
  | "unreachable"
  | "noAnswer"
  | "rejected"
  | "expired";

/** What the button needs from the live `/state` poll. */
export type Telemetry = {
  online: boolean;
  /**
   * Under power and free to move: a gait controller is loaded (it steps the
   * moment it gets a velocity, even standing still) or the posture is motion
   * by definition (a dance).
   */
  moving: boolean;
  /**
   * Whether the posture is a real, current reading the stop can be judged by:
   * not "unknown"/"no_data_yet"/`unknown(N)`, not stale (`stale_fsm_*`), not a
   * stub's fabrication. `readPosture` maps every one of those to "not moving",
   * so judging by `moving` alone confirmed stops nobody had seen happen.
   */
  known: boolean;
  /**
   * Roughly when the last SUCCESSFUL reading was requested (ms). Only a reading
   * requested after the stop was accepted can confirm it — the poll that was
   * already in flight when the operator pressed describes the robot before.
   */
  sampledAt: number | null;
  /** `env` from /state: "real", "isaac", "stub", … */
  env: string | null;
};

/** How long a real robot may stay in a locomotion mode after a 200 before we say so. */
export const STILL_MOVING_MS = 6000;
/** How long a confirmed "Detenido" stays up. Only a confirmed stop clears itself. */
export const STOPPED_CLEAR_MS = 6000;
/**
 * Client-side ceiling on the stop request. back gives the bridge 8 s before it
 * answers 504 `bridge_timeout` (the stop keeps running on the bridge either
 * way); this is the backstop for back itself not answering. Past it the button
 * must stop saying "Parando…" — that is failure dressed as progress — and say
 * it does not know.
 */
export const STOP_REQUEST_TIMEOUT_MS = 10_000;

/** Every phase that needs a decision from the operator and must not clear itself. */
export const FAILED_PHASES: ReadonlySet<EstopPhase> = new Set([
  "stillMoving",
  "notDamped",
  "unreachable",
  "noAnswer",
  "rejected",
  "expired",
]);

/**
 * Classify the stop request's own answer. `res` is the Eden pair; `timedOut`
 * means our AbortSignal fired, `threw` means fetch itself rejected.
 */
export function classifyStopResponse(res: {
  data?: unknown;
  error?: { status: unknown; value?: unknown } | null;
  timedOut?: boolean;
  threw?: boolean;
}): { phase: EstopPhase; outcome: SkillOutcome | null } {
  if (res.timedOut) return { phase: "noAnswer", outcome: null };
  if (res.threw) return { phase: "unreachable", outcome: null };
  const outcome = classifyInvoke(res);
  if (res.error) {
    const status =
      typeof res.error.status === "number" ? res.error.status : null;
    if (status === 401) return { phase: "expired", outcome };
    // back gave up WAITING, but the stop was dispatched and is still running on
    // the bridge (zero velocity may already be out; a damp may still land).
    // "Never left" would be false — "no answer" is the truth.
    if (outcome.code === "bridge_timeout" || status === 504) {
      return { phase: "noAnswer", outcome };
    }
    // The order never reached the robot. Retrying the button cannot fix any of
    // these; the physical stop can.
    if (
      outcome.code === "bridge_unavailable" ||
      outcome.code === "network" ||
      status === null ||
      status === 503
    ) {
      return { phase: "unreachable", outcome };
    }
    return { phase: "rejected", outcome };
  }
  // A 200 whose damp fallback failed is the one place a "success" can mean the
  // robot is still powered. stop_everything reports it honestly; so do we.
  if (outcome.code === "real_damp_fallback_failed") {
    return { phase: "notDamped", outcome };
  }
  if (outcome.kind === "error") return { phase: "rejected", outcome };
  return { phase: "accepted", outcome };
}

/**
 * Whether the bridge kept the robot in its walking controller on purpose.
 *
 * Since 2026-10-15 stop_everything damps a real robot only when it did not
 * acknowledge the zero-velocity burst — damping drops a standing robot. A stop
 * that landed reports `stop_burst_acked > 0` and no damp reason, and the robot
 * stays in its walk mode at zero velocity, so "still in a locomotion mode" is
 * the expected reading, not "Sigue en movimiento". A bridge older than that
 * change sends neither field, and its stop is judged as before.
 */
export function stopHeldGait(data: unknown): boolean {
  if (!data || typeof data !== "object") return false;
  const b = data as Record<string, unknown>;
  return (
    typeof b.stop_burst_acked === "number" &&
    b.stop_burst_acked > 0 &&
    b.real_damp_fallback_reason == null
  );
}

/**
 * Where an accepted press goes as telemetry arrives. Pure: the store calls it on
 * every reading and on a timer, and nothing else moves the phase forward.
 */
export function advance(
  phase: EstopPhase,
  acceptedAt: number | null,
  t: Telemetry,
  now: number,
  /** `stopHeldGait` of the stop's answer. */
  heldGait = false,
): EstopPhase {
  if (acceptedAt == null) return phase;

  const fresh = t.sampledAt != null && t.sampledAt > acceptedAt;

  // A confirmed stop that the robot then leaves (someone re-enabled the gait)
  // must not keep saying "Detenido" for the rest of its 6 s.
  if (phase === "stopped") {
    return t.online && t.known && fresh && t.moving ? "idle" : phase;
  }

  if (
    phase !== "accepted" &&
    phase !== "unconfirmed" &&
    phase !== "unverifiable" &&
    phase !== "holdingGait" &&
    phase !== "stillMoving"
  ) {
    return phase;
  }

  // Offline is NOT confirmation. `readPosture` collapses an unreachable bridge
  // to load "unknown", which is not "moving" — so judging by `moving` alone, the
  // button used to turn green and claim "Detenido" the moment telemetry
  // dropped, which is exactly when it is least entitled to: the robot may well
  // still be walking, and we can no longer see. A known-bad state stays bad.
  if (!t.online) return phase === "stillMoving" ? phase : "unconfirmed";

  if (!fresh) return phase === "unconfirmed" ? "accepted" : phase;

  // A posture we cannot vouch for decides nothing, either way: an unknown or
  // stub reading must not confirm the stop, and a stale "walk" must not raise
  // "Sigue en movimiento" from history.
  if (!t.known) return "unverifiable";
  if (!t.moving) return "stopped";
  if (now - acceptedAt < STILL_MOVING_MS) return "accepted";
  // Off real hardware stop_everything sends zero velocity but no damp, so the
  // walking controller keeps running and the posture keeps saying "walk". On
  // real hardware the same is true once the robot acknowledged the burst
  // (`heldGait`). That is the design, not a failed stop — calling it "Sigue en
  // movimiento" would be an alarm that is wrong every time, and alarms that are
  // wrong get ignored.
  return t.env === "real" && !heldGait ? "stillMoving" : "holdingGait";
}

export type EstopTone =
  "idle" | "pending" | "stopped" | "unconfirmed" | "failed";

export type EstopView = {
  /** Visible label from `sm` up — also the accessible name. */
  label: string;
  /** Visible label on phones, where the topbar has no room for the long ones. */
  short: string;
  tone: EstopTone;
  /** Spinner instead of an icon. */
  spinning: boolean;
};

const VIEW: Record<EstopPhase, EstopView> = {
  idle: { label: "Parar", short: "Parar", tone: "idle", spinning: false },
  sending: {
    label: "Parando…",
    short: "Parando…",
    tone: "pending",
    spinning: true,
  },
  accepted: {
    label: "Parando…",
    short: "Parando…",
    tone: "pending",
    spinning: true,
  },
  stopped: {
    label: "Detenido",
    short: "Detenido",
    tone: "stopped",
    spinning: false,
  },
  unconfirmed: {
    label: "Sin confirmar",
    short: "Sin confirmar",
    tone: "unconfirmed",
    spinning: false,
  },
  unverifiable: {
    label: "Sin confirmar",
    short: "Sin confirmar",
    tone: "unconfirmed",
    spinning: false,
  },
  holdingGait: {
    label: "Velocidad cero",
    short: "Vel. cero",
    tone: "unconfirmed",
    spinning: false,
  },
  stillMoving: {
    label: "Sigue en movimiento",
    short: "Se mueve",
    tone: "failed",
    spinning: false,
  },
  notDamped: {
    label: "Sin amortiguar",
    short: "Reintentar",
    tone: "failed",
    spinning: false,
  },
  unreachable: {
    label: "Sin puente",
    short: "Sin puente",
    tone: "failed",
    spinning: false,
  },
  noAnswer: {
    label: "Sin respuesta",
    short: "Reintentar",
    tone: "failed",
    spinning: false,
  },
  rejected: {
    label: "Reintentar",
    short: "Reintentar",
    tone: "failed",
    spinning: false,
  },
  expired: {
    label: "Sesión vencida",
    short: "Sesión",
    tone: "failed",
    spinning: false,
  },
};

export function estopView(phase: EstopPhase): EstopView {
  return VIEW[phase];
}

/**
 * What to tell the operator, beyond the button's one word. `alert` phases are
 * announced assertively and shown as a danger banner; `polite` ones are quiet
 * progress ("Parando…", "Detenido").
 *
 * `code` is the classified outcome's code: for `unreachable` it says WHICH hop
 * failed — back reported the bridge down (`bridge_unavailable`), or the browser
 * never reached back at all. Same instruction either way; a different place to
 * look afterwards.
 */
export function estopMessage(
  phase: EstopPhase,
  code: string | null = null,
): { level: "alert" | "warn" | "polite"; title: string; body: string } | null {
  switch (phase) {
    case "sending":
    case "accepted":
      return {
        level: "polite",
        title: "Parando…",
        body: "Orden de parada enviada. Esperando que la telemetría lo confirme.",
      };
    case "stopped":
      return {
        level: "polite",
        title: "Detenido",
        body: "La telemetría confirma que el robot salió del modo de marcha.",
      };
    case "unconfirmed":
      return {
        level: "warn",
        title: "Parada sin confirmar",
        body: "La orden de parada salió, pero se perdió la telemetría antes de confirmarla. No se puede verificar si el robot se detuvo.",
      };
    case "unverifiable":
      return {
        level: "warn",
        title: "Parada sin confirmar",
        body: "La orden de parada salió, pero la telemetría no informa una postura confiable (desconocida, desactualizada o simulada). No se puede verificar si el robot se detuvo.",
      };
    case "holdingGait":
      return {
        level: "warn",
        title: "Velocidad cero enviada",
        body: "La parada mandó velocidad cero sin amortiguar, así que el robot queda de pie en modo de marcha y la postura no puede confirmar que esté quieto. Miralo. Para dejarlo sin fuerza, Amortiguar — solo con el robot sostenido.",
      };
    case "stillMoving":
      return {
        level: "alert",
        title: "El robot sigue en modo de marcha",
        body: `Pasaron ${STILL_MOVING_MS / 1000} s desde la orden de parada y la telemetría todavía lo muestra en locomoción. Usá la parada física.`,
      };
    case "notDamped":
      return {
        level: "alert",
        title: "La parada no logró amortiguar el robot",
        body: "Se mandó velocidad cero, pero el pedido de amortiguación falló en todos los intentos: el robot puede seguir con torque. Volvé a pulsar PARAR o usá la parada física.",
      };
    case "unreachable":
      return {
        level: "alert",
        title: "La orden de parada no salió",
        body:
          code === "bridge_unavailable"
            ? "El servidor no pudo alcanzar el puente del robot. El robot puede seguir en movimiento. Usá la parada física."
            : "La consola no llegó al servidor, así que la orden no llegó al robot. El robot puede seguir en movimiento. Usá la parada física.",
      };
    case "noAnswer":
      return {
        level: "alert",
        title: "La orden de parada no respondió",
        body: `Sin respuesta en ${STOP_REQUEST_TIMEOUT_MS / 1000} s: no se sabe si llegó al robot. Si sigue en movimiento, usá la parada física.`,
      };
    case "rejected":
      return {
        level: "alert",
        title: "No se pudo enviar la orden de parada",
        body: "El servidor devolvió un error. El robot puede seguir en movimiento.",
      };
    case "expired":
      return {
        level: "alert",
        title: "La orden de parada no salió: tu sesión venció",
        body: "Si el robot se sigue moviendo, usá la parada física. Después volvé a iniciar sesión.",
      };
    default:
      return null;
  }
}

/** The global e-stop shortcut, as shown to operators. */
export const STOP_HOTKEY_LABEL = "Shift+Espacio";

/** Minimal structural view of an event target, so tests can pass plain objects. */
type TargetLike = {
  tagName?: string;
  isContentEditable?: boolean;
  type?: string;
} | null;

/** Input types where Space types a character rather than activating something. */
const NON_TEXT_INPUTS = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

/** Whether the keystroke belongs to a field the operator is typing into. */
export function isTypingTarget(target: TargetLike): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName?.toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    return !NON_TEXT_INPUTS.has((target.type ?? "text").toLowerCase());
  }
  return false;
}

/**
 * Shift+Space, anywhere in the console, except while typing.
 *
 * Plain Space would be one stray keystroke away from a stop; Ctrl/Cmd/Alt
 * combinations collide with browser and OS shortcuts. Key repeat is ignored —
 * holding the keys must not queue a burst of stop requests.
 */
export function isStopHotkey(e: {
  key: string;
  code?: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  repeat?: boolean;
  target: TargetLike;
}): boolean {
  if (!e.shiftKey || e.ctrlKey || e.metaKey || e.altKey || e.repeat) {
    return false;
  }
  if (e.code !== "Space" && e.key !== " ") return false;
  return !isTypingTarget(e.target);
}
