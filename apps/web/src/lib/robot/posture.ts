/**
 * The robot's FSM posture, translated for a human standing next to it.
 *
 * `/state` returns the firmware's own token — `zero_torque`, `walk_waist`,
 * `squat_up`, `unknown(802)` — and the console used to print exactly that,
 * capitalized. An operator should not have to read the G1's mode table.
 *
 * The grouping into `load` is the part that carries safety meaning. Of
 * everything on this screen, the fact that decides whether it is safe to stand
 * next to the robot is not its battery or its latency: it is whether the machine
 * is holding itself up, hanging limp, or moving under power.
 *
 * Mode indices and labels come from `apps/bridge/src/bridge/sdk/g1_protocol.py`.
 */

/** How the body is behaving, physically. Drives both colour and figure. */
export type Load = "limp" | "damped" | "holding" | "moving" | "unknown";

/** Which schematic pose to draw. */
export type Pose =
  | "stand"
  | "walk"
  | "squat"
  | "sit"
  | "lie"
  | "slump"
  | "unknown";

export type PostureInfo = {
  /** Spanish label for the operator. */
  label: string;
  /** One line on what the robot is physically doing. */
  detail: string;
  load: Load;
  pose: Pose;
};

const TABLE: Record<string, PostureInfo> = {
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
    label: "Subiendo",
    detail: "Locomoción activa sobre desnivel.",
    load: "moving",
    pose: "walk",
  },
  walk: {
    label: "Caminando",
    detail: "Locomoción activa. Mantené distancia.",
    load: "moving",
    pose: "walk",
  },
  walk_waist: {
    label: "Caminando",
    detail: "Locomoción activa con control de cintura.",
    load: "moving",
    pose: "walk",
  },
  run: {
    label: "Corriendo",
    detail: "Locomoción activa. Mantené distancia.",
    load: "moving",
    pose: "walk",
  },
  dance: {
    label: "Bailando",
    detail: "Secuencia de movimiento en curso.",
    load: "moving",
    pose: "walk",
  },
  no_data_yet: {
    label: "Sin datos",
    detail: "El puente todavía no recibió un estado del robot.",
    load: "unknown",
    pose: "unknown",
  },
};

const UNKNOWN: PostureInfo = {
  label: "Desconocido",
  detail: "El robot informa un modo que este panel no reconoce.",
  load: "unknown",
  pose: "unknown",
};

const OFFLINE: PostureInfo = {
  label: "Sin conexión",
  detail: "No hay estado del robot. Lo que sigue abajo es la última lectura.",
  load: "unknown",
  pose: "unknown",
};

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

/**
 * @param posture raw FSM token from `/state`, or null when it hasn't loaded
 * @param online whether the last poll reached the bridge
 */
export function readPosture(
  posture: string | null | undefined,
  online: boolean,
): PostureInfo {
  if (!online) return OFFLINE;
  if (!posture) return UNKNOWN;
  return TABLE[posture] ?? UNKNOWN;
}
