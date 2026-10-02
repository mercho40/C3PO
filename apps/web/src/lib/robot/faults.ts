/**
 * The bridge's fault tokens, translated for the operator.
 *
 * `get_state` reports faults as machine tokens —
 * `fsm_rpc_failing_12_polls__restart_the_bridge_if_this_persists`,
 * `stale_pose_34s`, `low_battery_15pct` — and the console printed them verbatim.
 * posture.ts already argues an operator should not have to read the G1's mode
 * table; faults are the ACTIONABLE part of the state, so they deserve that more.
 *
 * Every fault keeps its raw token: whoever is debugging the bridge needs to
 * know exactly which string produced the sentence.
 *
 * Token shapes come from `apps/bridge/src/bridge/sdk/state.py` (`get_state`).
 * The important split those tokens encode: some faults are the ROBOT's (low
 * battery), and most are the BRIDGE's own health (a dead subscription, a failing
 * RPC). "Restart the bridge" and "the robot is broken" are different afternoons.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

export type FaultSeverity = "danger" | "warn";

/** Which part of the system the fault is about. */
export type FaultScope = "robot" | "bridge";

export type Fault = {
  /** The token exactly as the bridge sent it. */
  raw: string;
  /** One Spanish sentence on what is wrong. */
  text: string;
  /** What to do about it, when there is something to do. */
  action: string | null;
  severity: FaultSeverity;
  scope: FaultScope;
  /** Seconds, for the staleness faults. */
  ageS?: number;
};

type Rule = {
  match: RegExp;
  build: (m: RegExpMatchArray) => Omit<Fault, "raw">;
};

const RULES: Rule[] = [
  {
    match: /^no_lowstate_received$/,
    build: () => ({
      text: "El puente todavía no recibió ningún estado del robot.",
      action:
        "Si persiste tras un reinicio, reiniciá el servicio c3po: tras el arranque, el puente puede quedar sin enlace DDS.",
      severity: "danger",
      scope: "bridge",
    }),
  },
  {
    match: /^fsm_rpc_failing_(\d+)_polls/,
    build: (m) => ({
      text: `El puente no logra leer el modo del robot (${m[1]} consultas seguidas fallaron).`,
      action: "Si persiste, reiniciá el puente.",
      severity: "danger",
      scope: "bridge",
    }),
  },
  {
    match: /^stale_fsm_([\d.]+)s$/,
    build: (m) => ({
      text: `La postura es de hace ${Math.round(Number(m[1]))} s: el modo del robot dejó de actualizarse.`,
      action: "Tratá la postura como historia, no como el estado actual.",
      severity: "warn",
      scope: "bridge",
      ageS: Number(m[1]),
    }),
  },
  {
    match: /^stale_pose_([\d.]+)s$/,
    build: (m) => ({
      text: `La posición es de hace ${Math.round(Number(m[1]))} s: la odometría dejó de llegar.`,
      action:
        "No midas distancias ni ángulos hasta que vuelva. Si no vuelve, reiniciá el puente.",
      severity: "warn",
      scope: "bridge",
      ageS: Number(m[1]),
    }),
  },
  {
    match: /^stale_lowstate_([\d.]+)s$/,
    build: (m) => ({
      text: `El estado de bajo nivel llegó hace ${Number(m[1]).toFixed(1)} s (se espera cada 2 ms).`,
      action: "Revisá el enlace DDS del puente.",
      severity: "warn",
      scope: "bridge",
      ageS: Number(m[1]),
    }),
  },
  {
    match: /^low_battery_(\d+)pct$/,
    build: (m) => ({
      text: `Batería baja: ${m[1]} %.`,
      action: "Llevá el robot a cargar antes de empezar otra tarea.",
      severity: "danger",
      scope: "robot",
    }),
  },
];

/** Translate one fault token. Unknown tokens are kept, never hidden. */
export function readFault(raw: string): Fault {
  for (const rule of RULES) {
    const m = raw.match(rule.match);
    if (m) return { raw, ...rule.build(m) };
  }
  return {
    raw,
    text: "Fallo informado por el puente.",
    action: null,
    severity: "warn",
    scope: "bridge",
  };
}

/** Translate a fault list, most severe first (stable within a severity). */
export function readFaults(raw: readonly string[] | null | undefined): Fault[] {
  const faults = (raw ?? []).map(readFault);
  return [
    ...faults.filter((f) => f.severity === "danger"),
    ...faults.filter((f) => f.severity === "warn"),
  ];
}

/**
 * Which readings the faults say are HISTORY rather than current.
 *
 * The bridge keeps reporting the last posture and pose after their
 * subscriptions die, and marks them stale only through these tokens. Any panel
 * showing posture or position must dim it when this says so — "The posture
 * below is real, but it is HISTORY. Say so." (state.py).
 */
export function staleness(raw: readonly string[] | null | undefined): {
  posture: number | null;
  pose: number | null;
  link: number | null;
} {
  const out = {
    posture: null as number | null,
    pose: null as number | null,
    link: null as number | null,
  };
  for (const token of raw ?? []) {
    const fsm = token.match(/^stale_fsm_([\d.]+)s$/);
    if (fsm) out.posture = Number(fsm[1]);
    const pose = token.match(/^stale_pose_([\d.]+)s$/);
    if (pose) out.pose = Number(pose[1]);
    const low = token.match(/^stale_lowstate_([\d.]+)s$/);
    if (low) out.link = Number(low[1]);
  }
  return out;
}
