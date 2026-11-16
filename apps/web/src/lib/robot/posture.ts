/**
 * The robot's FSM posture, translated for a human standing next to it.
 *
 * `/state` returns the firmware's own token — `zero_torque`, `walk_waist`,
 * `squat_up`, `unknown(803)` — and the console used to print exactly that,
 * capitalized. An operator should not have to read the G1's mode table.
 *
 * The grouping into `load` is the part that carries safety meaning. Of
 * everything on this screen, the fact that decides whether it is safe to stand
 * next to the robot is not its battery or its latency: it is whether the machine
 * is holding itself up, hanging limp, or moving under power.
 *
 * LOCOMOTION MODES ARE CONTROLLERS, NOT MOTION. 500/501 (walk, walk_waist) and
 * 801/802 (both reported as "run") load a gait controller that stands still at
 * zero stick — the bridge read 802 live on a robot "standing perfectly still"
 * and flags the label as suspect (g1_protocol.py MODE_LABEL; docs/ROBOT-API.md
 * §4.1: 802 is the Run Control mode the R2+A boot chain enters). These used to
 * read "CORRIENDO — Locomoción activa" with the cyan glow and the stride, on a
 * motionless robot. They are now labelled by what is loaded ("Control de
 * marcha"), and `moving` is only claimed when the caller has SEEN the pose
 * change (`motion.ts`).
 *
 * Mode indices and labels come from `apps/bridge/src/bridge/sdk/g1_protocol.py`.
 * Plain module, no runes, so `bun test` can load it.
 */

import { LINK, LINK_DETAIL, type LinkProblem } from "./link-copy";
import type { Motion } from "./motion";

/** How the body is behaving, physically. Drives both colour and figure. */
export type Load = "limp" | "damped" | "holding" | "moving" | "unknown";

/** Which schematic pose to draw. */
export type Pose =
  "stand" | "walk" | "squat" | "sit" | "lie" | "slump" | "unknown";

export type PostureInfo = {
  /** Spanish label for the operator. */
  label: string;
  /**
   * One line on what the robot is physically doing. On a stale reading it
   * carries the age ("Última lectura hace 34 s — …"), so it changes every
   * poll: never put it in a live region — use `note` + `since` for that.
   */
  detail: string;
  /**
   * `detail` without anything that ticks: the sentence a live region may
   * announce. Equal to `detail` except on a stale reading.
   */
  note: string;
  /** "Última lectura hace 34 s" on a stale reading, else null. Ticks. */
  since: string | null;
  load: Load;
  pose: Pose;
  /**
   * A gait controller is loaded: the robot can take a step the moment it is
   * sent a velocity, whether or not it is moving now. The stop button should
   * key off this, not off `load === "moving"`.
   */
  locomotion: boolean;
  /** The reading is history (a `stale_fsm_*` fault): label kept, load unknown. */
  stale: boolean;
};

type Entry = Omit<PostureInfo, "stale" | "locomotion" | "note" | "since"> & {
  locomotion?: boolean;
};

const CONTROLLER_DETAIL = "Controlador activo; puede dar pasos.";

const TABLE: Record<string, Entry> = {
  zero_torque: {
    label: "Sin torque",
    detail:
      "Las articulaciones no sostienen peso. El robot se desploma si nadie lo sujeta.",
    load: "limp",
    pose: "slump",
  },
  damp: {
    label: "Amortiguado",
    detail: "Sostén blando. Cede ante el empuje, pero no cae.",
    load: "damped",
    pose: "slump",
  },
  preparation: {
    label: "Preparado",
    detail: "De pie y con torque. Listo para caminar.",
    load: "holding",
    pose: "stand",
  },
  // The stub bridge's fixed posture. Not a firmware mode, but it is what the
  // stub reports, and "Desconocido" for it sent people looking for a fault.
  standing: {
    label: "De pie",
    detail: "De pie y con torque.",
    load: "holding",
    pose: "stand",
  },
  squat: {
    label: "En cuclillas",
    detail: "Sostiene la postura agachada.",
    load: "holding",
    pose: "squat",
  },
  squat_up: {
    label: "En cuclillas",
    detail: "Sostiene la postura agachada.",
    load: "holding",
    pose: "squat",
  },
  seating: {
    label: "Sentado",
    detail: "Apoyado, con las piernas hacia adelante.",
    load: "holding",
    pose: "sit",
  },
  lie_up: {
    label: "Acostado",
    detail: "En el piso, boca arriba.",
    load: "holding",
    pose: "lie",
  },
  climb: {
    label: "Control de escalada",
    detail: "Controlador de desnivel activo; puede dar pasos.",
    load: "moving",
    pose: "walk",
    locomotion: true,
  },
  walk: {
    label: "Modo marcha",
    detail: "Controlador de marcha activo; camina cuando recibe velocidad.",
    load: "moving",
    pose: "walk",
    locomotion: true,
  },
  walk_waist: {
    label: "Marcha con cintura",
    detail:
      "Controlador de marcha con cintura activo; camina cuando recibe velocidad.",
    load: "moving",
    pose: "walk",
    locomotion: true,
  },
  // 801 and 802 both arrive as "run". 802 was read on a motionless robot, so
  // the label names the controller, never the gait.
  run: {
    label: "Control de marcha",
    detail: CONTROLLER_DETAIL,
    load: "moving",
    pose: "walk",
    locomotion: true,
  },
  // A dance is motion by definition — the sequence runs once it starts.
  dance: {
    label: "Bailando",
    detail: "Secuencia de movimiento en curso. Mantené distancia.",
    load: "moving",
    pose: "walk",
  },
  no_data_yet: {
    label: "Sin datos",
    detail: "El puente todavía no recibió un estado del robot.",
    load: "unknown",
    pose: "unknown",
  },
  // Real mode with no FSM id: the RPC is not answering. That is a bridge
  // problem with a known fix, not "a mode this panel does not recognise".
  unknown: {
    label: "Sin lectura del FSM",
    detail:
      "Sin lectura del FSM — el puente no obtiene el modo; reiniciá el servicio si persiste.",
    load: "unknown",
    pose: "unknown",
  },
};

const UNRECOGNISED: Entry = {
  label: "Desconocido",
  detail: "El robot informa un modo que este panel no reconoce.",
  load: "unknown",
  pose: "unknown",
};

const NO_POSTURE: Entry = {
  label: "Sin datos",
  detail: "El estado llegó sin postura.",
  load: "unknown",
  pose: "unknown",
};

/**
 * The link is down. The label is the SAME word the topbar shows for the same
 * condition (`LINK[problem]`): the hero used to say "Sin conexión" under a
 * topbar saying "Sin puente", two names for one fault, each implying a
 * different fix.
 *
 * And the detail only claims a "last reading" when one exists. With no bridge
 * from the first poll, every value on screen is "—", and "Lo que ves es la
 * última lectura" was describing data that never arrived.
 */
function offline(
  problem: LinkProblem | null | undefined,
  hasReading: boolean,
): PostureInfo {
  const p = problem ?? "offline";
  let detail: string;
  if (!hasReading) {
    // Nothing to call "the last reading". Say so, then — when the cause is
    // known — what to check.
    detail =
      p === "offline"
        ? LINK_DETAIL.never
        : `${LINK_DETAIL.never} ${LINK_DETAIL[p]}`;
  } else {
    detail =
      p === "offline"
        ? LINK_DETAIL.offline
        : `${LINK_DETAIL[p]} Lo que ves es la última lectura.`;
  }
  return {
    label: LINK[p],
    detail,
    note: detail,
    since: null,
    load: "unknown",
    pose: "unknown",
    locomotion: false,
    stale: false,
  };
}

/**
 * Colour per load state, in one place.
 *
 * Every surface that shows the robot — the hero headline, the figure, the map
 * marker, the rail — has to encode load identically, or the palette stops
 * meaning anything. Cyan in particular is rationed: it says "under power and
 * moving", nowhere else. The map marker used to be cyan whenever the bridge was
 * reachable, which lit it up for a robot lying limp on the floor.
 */
export const LOAD_TEXT: Record<Load, string> = {
  moving: "text-cyan",
  limp: "text-danger-soft",
  damped: "text-warn",
  holding: "text-ink",
  unknown: "text-ink-mute",
};

export const LOAD_FILL: Record<Load, string> = {
  moving: "bg-cyan",
  limp: "bg-danger",
  damped: "bg-warn",
  holding: "bg-peri",
  unknown: "bg-ink-mute",
};

export const LOAD_BORDER: Record<Load, string> = {
  moving: "border-cyan",
  limp: "border-danger",
  damped: "border-warn",
  holding: "border-peri",
  unknown: "border-ink-mute",
};

/** The same encoding as a CSS colour, for SVG fills and strokes. */
export const LOAD_VAR: Record<Load, string> = {
  moving: "var(--c3-cyan)",
  limp: "var(--c3-danger)",
  damped: "var(--c3-warn)",
  holding: "var(--c3-peri)",
  unknown: "var(--c3-ink-mute)",
};

/** Spanish names for the load states — the map legend and aria-labels. */
export const LOAD_LABEL: Record<Load, string> = {
  moving: "en movimiento",
  limp: "sin torque",
  damped: "amortiguado",
  holding: "sostiene su peso",
  unknown: "sin dato",
};

export type PostureOptions = {
  /**
   * Whether the pose is actually changing (`MotionTracker`). OMIT it only
   * where motion cannot be observed: a locomotion mode then keeps the
   * conservative "moving" reading, which is what the stop button wants. Pass
   * "unknown" when you track motion but cannot tell yet — the robot is then
   * shown as holding itself up, which is true in every gait mode.
   */
  motion?: Motion;
  /**
   * Seconds since the FSM last updated, from `staleness(faults).posture`.
   * Non-null makes the reading history.
   */
  staleS?: number | null;
  /** The bridge is in stub mode: every value is fabricated. */
  stub?: boolean;
  /**
   * Why the link is down (`RobotLive.problem`), so the offline label matches
   * the topbar's. Omitted → the generic "Sin conexión".
   */
  problem?: LinkProblem | null;
  /**
   * Whether ANY reading has ever arrived (`live.state != null`). False picks
   * the "never" wording instead of "lo que ves es la última lectura".
   * Omitted → assumed true (the old behaviour).
   */
  hasReading?: boolean;
};

/**
 * @param posture raw FSM token from `/state`, or null when it hasn't loaded
 * @param online whether the last poll reached the bridge
 */
export function readPosture(
  posture: string | null | undefined,
  online: boolean,
  opts: PostureOptions = {},
): PostureInfo {
  if (!online) return offline(opts.problem, opts.hasReading ?? true);

  const entry: Entry = !posture
    ? NO_POSTURE
    : (TABLE[posture] ?? unrecognised(posture));
  const locomotion = entry.locomotion === true;

  let { label, detail, load, pose } = entry;

  if (locomotion && opts.motion !== undefined) {
    if (opts.motion === "moving") {
      load = "moving";
      pose = "walk";
      detail = `${detail} En movimiento: mantené distancia.`;
    } else {
      load = "holding";
      pose = "stand";
      if (opts.motion === "still") detail = `${detail} Quieto ahora.`;
    }
  }

  if (opts.stub) {
    detail =
      "Valor ficticio: el puente está en modo simulación (stub) y no hay robot.";
  }

  // History, not the present. The label stays — it is the last thing we knew
  // — but the load goes unknown (dimmed, dashed figure) and the detail says
  // how old it is. The bridge's own comment: "The posture below is real, but
  // it is HISTORY. Say so."
  //
  // The age is split out (`since`) so a live region can announce the label
  // and the static sentence once, instead of re-reading "hace 35 s", "hace
  // 37 s"… on every 2 s poll.
  if (opts.staleS != null) {
    const since = `Última lectura hace ${Math.round(opts.staleS)} s`;
    return {
      label,
      detail: `${since} — el modo dejó de actualizarse.`,
      note: "El modo dejó de actualizarse.",
      since,
      load: "unknown",
      pose,
      locomotion,
      stale: true,
    };
  }

  return {
    label,
    detail,
    note: detail,
    since: null,
    load,
    pose,
    locomotion,
    stale: false,
  };
}

function unrecognised(token: string): Entry {
  const id = token.match(/^unknown\((\d+)\)$/)?.[1];
  return id
    ? {
        ...UNRECOGNISED,
        detail: `El robot informa el modo ${id}, que este panel no reconoce.`,
      }
    : UNRECOGNISED;
}

/**
 * `fsm · run (802)` — the firmware token plus the index behind it, because
 * the token alone cannot tell 801 from 802 and the whole point of printing it
 * is to let whoever is debugging DDS trace the label to its source.
 */
export function fsmReadout(
  posture: string | null | undefined,
  fsmId: number | null | undefined,
): string | null {
  if (!posture) return null;
  return fsmId != null && !posture.includes(String(fsmId))
    ? `fsm · ${posture} (${fsmId})`
    : `fsm · ${posture}`;
}
