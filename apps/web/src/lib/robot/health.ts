/**
 * What /health ("Salud") says about each part of the system — decided here,
 * in one plain module, so it can be tested.
 *
 * The diagnostics already flowed through the backend — `get_state`'s `raw`
 * block, the catalogue envelope, the telemetry routes, the camera's /status,
 * the costmap's headers, the Nav2 gate — and the console threw most of it
 * away. This turns each source into one row an operator can read:
 *
 *   { name, tone: ok | warn | danger | unknown, summary, age, detail, action }
 *
 * THE RULES ARE THE CONSOLE'S, applied per row:
 *  - `unknown` is a first-class tone. "We could not look" is never rendered as
 *    "fine", and the overall line can never say "todo en orden" while a row is
 *    unknown.
 *  - A held reading is not a current one. When the link is down, battery and
 *    faults are the LAST reading and say so; they do not keep their colours.
 *  - Ages come from whoever measured them: the robot's own `*_age_s` where it
 *    reports one, otherwise when this console last got an answer.
 *  - Bridge text stays verbatim (`hint`, `error`, `note`) — tagged `en` for the
 *    template, because it is English and a screen reader should say so. When
 *    it is a string we KNOW (the gate's "never armed", the costmap and camera
 *    hints), a Spanish rendering goes first and the verbatim English follows
 *    as the secondary line (`translateBridgeText`). Unknown text is never
 *    guessed at: it stays verbatim, alone.
 *
 * Thresholds mirror the bridge's own (`bridge/sdk/state.py`): lowstate is stale
 * after 1 s, pose after 2 s, the FSM after 10 s. Re-deriving different numbers
 * here would make /health and the robot's own faults disagree about the same
 * reading.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

import type { RobotState } from "@back/routes/state";
import type { GateStatus } from "./gate.svelte";
import type { VoiceLoopState } from "./voice-loop";
import { readFaults, type Fault } from "./faults";
import { LINK, LINK_DETAIL, type LinkProblem } from "./link-copy";
import { translateBridgeText } from "./bridge-text";
import { batteryLevel, formatAge, formatLatency, formatSince } from "./format";

export type HealthTone = "ok" | "warn" | "danger" | "unknown";

/** One labelled reading under a row. `lang` marks verbatim bridge text. */
export type HealthFact = {
  label: string;
  value: string;
  tone?: HealthTone;
  lang?: "en";
};

export type HealthRow = {
  id: string;
  name: string;
  tone: HealthTone;
  /** One line, Spanish. */
  summary: string;
  /** How old the reading behind `summary` is, already worded ("hace 3 s"). */
  age: string | null;
  /** A sentence of explanation, when the summary is not enough. */
  detail: string | null;
  /** Verbatim bridge text (hint/error/note), shown tagged as English. */
  bridgeText?: string | null;
  /** `bridgeText` in Spanish, when it is a string we know. Shown first. */
  bridgeEs?: string | null;
  /** What to do about it. */
  action?: string | null;
  facts: HealthFact[];
  /** Translated faults, for the Fallos row. */
  faults?: Fault[];
};

/**
 * One HTTP read, as the probe saw it.
 *
 * `network` is "no HTTP answer at all" — refused, DNS, CORS, mixed content or
 * the timeout. Those are indistinguishable from a browser on purpose (CORS
 * hides the cause), so the copy names the candidates instead of guessing one.
 */
export type Read<T> =
  | { state: "idle" }
  | { state: "skipped"; reason: string }
  | {
      state: "ok";
      at: number;
      ms: number;
      status: number;
      data: T;
      headers?: Record<string, string | null>;
    }
  | { state: "http"; at: number; ms: number; status: number; body: unknown }
  | { state: "network"; at: number; timeout: boolean };

export const TONE_RANK: Record<HealthTone, number> = {
  ok: 0,
  unknown: 1,
  warn: 2,
  danger: 3,
};

/** Tailwind text colour per tone. Unknown is muted, never green. */
export const TONE_TEXT: Record<HealthTone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger-soft",
  unknown: "text-ink-mute",
};

export const TONE_LABEL: Record<HealthTone, string> = {
  ok: "Bien",
  warn: "Atención",
  danger: "Falla",
  unknown: "Sin datos",
};

export function worstTone(tones: readonly HealthTone[]): HealthTone {
  return tones.reduce<HealthTone>(
    (worst, t) => (TONE_RANK[t] > TONE_RANK[worst] ? t : worst),
    "ok",
  );
}

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`;

/**
 * The line at the top of the page. Worst tone wins, and "todo en orden" is
 * only ever said when EVERY row is ok — an unknown row is a gap, not a pass.
 */
export function overall(rows: readonly HealthRow[]): {
  tone: HealthTone;
  summary: string;
} {
  if (rows.length === 0)
    return { tone: "unknown", summary: "Sin datos todavía." };
  const count = (t: HealthTone) => rows.filter((r) => r.tone === t).length;
  const danger = count("danger");
  const warn = count("warn");
  const unknown = count("unknown");
  const parts = [
    danger ? plural(danger, "falla", "fallas") : null,
    warn ? plural(warn, "aviso", "avisos") : null,
    unknown
      ? plural(unknown, "subsistema sin datos", "subsistemas sin datos")
      : null,
  ].filter(Boolean);
  return {
    tone: worstTone(rows.map((r) => r.tone)),
    summary: parts.length
      ? parts.join(" · ")
      : `Todo en orden (${plural(rows.length, "subsistema", "subsistemas")}).`,
  };
}

// ---------------------------------------------------------------------------
// Shared wording for reads that did not produce data
// ---------------------------------------------------------------------------

const str = (v: unknown): string | null =>
  typeof v === "string" && v.length > 0 ? v : null;

function bodyField(body: unknown, key: string): string | null {
  return body && typeof body === "object"
    ? str((body as Record<string, unknown>)[key])
    : null;
}

// Known bridge strings, in Spanish, live in `./bridge-text` — shared with the
// map and camera copy. Re-exported here for existing importers.
export { translateBridgeText };

/** `bridgeText` and its Spanish, for spreading into a row. */
function bridge(
  text: string | null | undefined,
): Pick<HealthRow, "bridgeText" | "bridgeEs"> {
  const verbatim = str(text);
  return { bridgeText: verbatim, bridgeEs: translateBridgeText(verbatim) };
}

/** Age of the console's own last answer from a read. */
function readAge(read: Read<unknown>, now: number): string | null {
  return "at" in read ? formatSince(read.at, now) : null;
}

/**
 * A back route that did not answer with data. 502 `bridge_unavailable` is the
 * bridge; 502 otherwise is the bridge misbehaving; no answer is back itself.
 * (401 never reaches here — the probe sends the operator to log in.)
 */
function failedBackRead(
  read: Extract<Read<unknown>, { state: "http" | "network" }>,
  now: number,
): Pick<
  HealthRow,
  "tone" | "summary" | "detail" | "age" | "bridgeText" | "bridgeEs"
> {
  if (read.state === "network") {
    return {
      tone: "danger",
      summary: read.timeout ? "Sin respuesta a tiempo" : LINK.noApi,
      detail: read.timeout
        ? "El servidor no contestó dentro del plazo; puede estar colgado esperando al puente."
        : LINK_DETAIL.noApi,
      age: readAge(read, now),
    };
  }
  const code = bodyField(read.body, "error");
  if (code === "bridge_unavailable") {
    return {
      tone: "danger",
      summary: LINK.noBridge,
      detail: LINK_DETAIL.noBridge,
      age: readAge(read, now),
    };
  }
  return {
    tone: "danger",
    summary: `El servidor respondió HTTP ${read.status}`,
    detail: null,
    ...bridge(code),
    age: readAge(read, now),
  };
}

function idleRow(id: string, name: string): HealthRow {
  return {
    id,
    name,
    tone: "unknown",
    summary: "Consultando…",
    age: null,
    detail: null,
    facts: [],
  };
}

// ---------------------------------------------------------------------------
// Servidor — GET /health (public)
// ---------------------------------------------------------------------------

export type ServerHealth = {
  status: string;
  timestamp: number;
  auth?: { providers?: string[] };
};

/** Above this, the server answers but slowly enough to be worth saying. */
export const SERVER_SLOW_MS = 1000;

export function classifyServer(
  read: Read<ServerHealth>,
  now: number,
): HealthRow {
  const id = "server";
  const name = "Servidor";
  if (read.state === "idle" || read.state === "skipped")
    return idleRow(id, name);
  if (read.state !== "ok") {
    return { id, name, facts: [], ...failedBackRead(read, now) };
  }
  const slow = read.ms > SERVER_SLOW_MS;
  const providers = read.data.auth?.providers ?? [];
  return {
    id,
    name,
    tone: slow ? "warn" : "ok",
    summary: slow
      ? `Responde, pero lento (${formatLatency(read.ms)})`
      : `Responde en ${formatLatency(read.ms)}`,
    age: readAge(read, now),
    detail: null,
    facts: [
      {
        label: "Latencia",
        value: formatLatency(read.ms),
        tone: slow ? "warn" : undefined,
      },
      {
        label: "Ingreso con terceros",
        value: providers.length
          ? providers.join(", ")
          : "solo correo y contraseña",
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Puente y catálogo — GET /skills envelope
// ---------------------------------------------------------------------------

export type CatalogueEnvelope = {
  count: number;
  source: "bridge" | "cache";
  age_seconds: number;
  bridge_error?: string;
  rejected?: string[];
};

export function classifyCatalogue(
  read: Read<CatalogueEnvelope>,
  now: number,
): HealthRow {
  const id = "catalogue";
  const name = "Puente y catálogo";
  if (read.state === "idle" || read.state === "skipped")
    return idleRow(id, name);
  if (read.state !== "ok") {
    return { id, name, facts: [], ...failedBackRead(read, now) };
  }
  const c = read.data;
  const facts: HealthFact[] = [
    { label: "Habilidades", value: String(c.count) },
    {
      label: "Origen",
      value:
        c.source === "cache"
          ? `caché (${formatAge(c.age_seconds)})`
          : "en vivo",
      tone: c.source === "cache" ? "warn" : undefined,
    },
  ];
  if (c.rejected?.length) {
    facts.push({
      label: "Descartadas",
      value: c.rejected.join(", "),
      tone: "warn",
      lang: "en",
    });
  }

  // Nothing cached and no bridge: back reports emptiness honestly, and so do we.
  if (c.bridge_error && c.count === 0) {
    return {
      id,
      name,
      tone: "danger",
      summary: "El puente no responde y no hay catálogo guardado",
      age: readAge(read, now),
      detail:
        "Sin catálogo, el agente no tiene herramientas. PARAR sigue funcionando: no depende del catálogo.",
      ...bridge(c.bridge_error),
      action: "Revisá el túnel SSH o el servicio c3po en el robot.",
      facts,
    };
  }
  if (c.source === "cache") {
    return {
      id,
      name,
      tone: "warn",
      summary: `El puente no responde — catálogo de ${formatAge(c.age_seconds)}`,
      age: formatAge(c.age_seconds),
      detail:
        "La consola muestra lo último que el puente publicó. Las órdenes al robot van a fallar hasta que vuelva.",
      ...bridge(c.bridge_error),
      action: "Revisá el túnel SSH o el servicio c3po en el robot.",
      facts,
    };
  }
  if (c.rejected?.length) {
    return {
      id,
      name,
      tone: "warn",
      summary: `En vivo, pero ${plural(c.rejected.length, "herramienta quedó afuera", "herramientas quedaron afuera")}`,
      age: readAge(read, now),
      detail:
        "El puente las ofrece con metadatos ilegibles, así que se excluyen en vez de adivinar su nivel de peligro.",
      facts,
    };
  }
  return {
    id,
    name,
    tone: "ok",
    summary: `En vivo · ${plural(c.count, "habilidad", "habilidades")}`,
    age: readAge(read, now),
    detail: null,
    facts,
  };
}

// ---------------------------------------------------------------------------
// Enlace con el robot — the shared RobotLive poller + get_state's raw block
// ---------------------------------------------------------------------------

export const LOWSTATE_STALE_S = 1.0;
export const POSE_STALE_S = 2.0;
export const FSM_STALE_S = 10.0;
/** Round trip for /state (browser → back → MCP get_state → back). */
export const LINK_SLOW_MS = 1500;

export type LinkInput = {
  online: boolean;
  problem: LinkProblem | null;
  latencyMs: number | null;
  simulated: boolean;
  state: RobotState | null;
  lastOkAt: number | null;
  loaded: boolean;
};

const ageText = (s: number | null | undefined) =>
  s == null ? "nunca" : `${s < 10 ? s.toFixed(2) : Math.round(s)} s`;

export function classifyLink(input: LinkInput, now: number): HealthRow {
  const id = "link";
  const name = "Enlace con el robot";
  const s = input.state;
  const raw = s?.raw;
  const facts: HealthFact[] = [];
  if (s) facts.push({ label: "Entorno", value: s.env });
  // Only a poll that ANSWERED has a round trip worth printing. `latencyMs` is
  // measured on failures too — with back down it is the few ms it takes to be
  // refused — and "Respuesta /state 5 ms" beside "Sin conexión" reads as a
  // fast, healthy link.
  facts.push({
    label: "Respuesta /state",
    value:
      input.online && input.lastOkAt ? formatLatency(input.latencyMs) : "—",
  });
  if (raw) {
    facts.push(
      {
        label: "rt/lowstate",
        value: `${ageText(raw.lowstate_age_s)} · ${raw.lowstate_messages_received} msgs`,
        tone:
          raw.lowstate_age_s != null && raw.lowstate_age_s > LOWSTATE_STALE_S
            ? "warn"
            : undefined,
      },
      {
        label: `Pose (${raw.pose_source})`,
        value: `${ageText(raw.pose_age_s)} · ${raw.pose_messages_received} msgs`,
        tone:
          raw.pose_age_s != null && raw.pose_age_s > POSE_STALE_S
            ? "warn"
            : raw.pose_messages_received === 0
              ? "warn"
              : undefined,
      },
      {
        label: "FSM",
        value:
          raw.fsm_id == null
            ? `sin id · ${s?.posture ?? "—"}`
            : `${raw.fsm_id}${raw.fsm_mode != null ? ` (modo ${raw.fsm_mode})` : ""} · ${ageText(raw.fsm_age_s)}`,
        tone:
          raw.fsm_age_s != null && raw.fsm_age_s > FSM_STALE_S
            ? "warn"
            : undefined,
      },
    );
  }

  if (!input.loaded && !s) return { ...idleRow(id, name), facts };

  if (!input.online) {
    const problem = input.problem ?? "offline";
    return {
      id,
      name,
      tone: "danger",
      summary: LINK[problem],
      age: input.lastOkAt
        ? `última lectura ${formatSince(input.lastOkAt, now)}`
        : null,
      detail: LINK_DETAIL[problem],
      facts,
    };
  }

  if (input.simulated) {
    return {
      id,
      name,
      tone: "warn",
      summary: "Puente en modo stub: datos ficticios",
      age: formatSince(input.lastOkAt, now),
      detail:
        "Todo lo que informa el robot (batería, postura, posición) es inventado. No hay robot ni simulador detrás.",
      facts,
    };
  }

  // Ordered by how much each one invalidates everything else on the page.
  const warnings: { summary: string; detail: string; action?: string }[] = [];
  if (s?.faults.includes("no_lowstate_received")) {
    warnings.push({
      summary: "El puente no recibe estado del robot",
      detail: "Ningún mensaje de rt/lowstate desde que arrancó el puente.",
      action:
        "Si el robot está encendido, reiniciá el servicio c3po: tras un arranque el puente puede quedar sin enlace DDS.",
    });
  }
  if (raw?.lowstate_age_s != null && raw.lowstate_age_s > LOWSTATE_STALE_S) {
    warnings.push({
      summary: `rt/lowstate llegó hace ${ageText(raw.lowstate_age_s)}`,
      detail:
        "Se espera cada 2 ms. Todo lo que depende del estado de bajo nivel es historia.",
      action: "Revisá el enlace DDS del puente (eth0).",
    });
  }
  // The post-reboot race (eth0 vs network-online): subscriptions flow, RPC
  // writers are dead. fsm_id null with a FRESH lowstate is that signature, not
  // "no controller loaded" — and it is fixed by restarting the service.
  if (
    s?.env === "real" &&
    raw &&
    raw.fsm_id == null &&
    raw.lowstate_age_s != null &&
    raw.lowstate_age_s <= LOWSTATE_STALE_S
  ) {
    warnings.push({
      summary: "El puente no lee el modo del robot (fsm_id nulo)",
      detail:
        "Los mensajes llegan pero las consultas RPC no: tras reiniciar el robot, el servicio suele arrancar antes que eth0.",
      action:
        "Reiniciá el servicio del puente en el robot (c3po restart). No es un robot roto.",
    });
  }
  if (raw?.fsm_age_s != null && raw.fsm_age_s > FSM_STALE_S) {
    warnings.push({
      summary: `El modo es de hace ${ageText(raw.fsm_age_s)}`,
      detail: "La postura que ves es la última conocida, no la actual.",
      action: "Si persiste, reiniciá el puente.",
    });
  }
  if (raw && raw.pose_messages_received === 0) {
    warnings.push({
      summary: "Sin odometría todavía",
      detail: `Ningún mensaje de pose (${raw.pose_source}). Caminar a un punto y medir distancias no funciona sin ella.`,
    });
  } else if (raw?.pose_age_s != null && raw.pose_age_s > POSE_STALE_S) {
    warnings.push({
      summary: `La posición es de hace ${ageText(raw.pose_age_s)}`,
      detail:
        "La odometría dejó de llegar; distancias y ángulos no son confiables.",
      action: "Si no vuelve, reiniciá el puente.",
    });
  }
  const slow = input.latencyMs != null && input.latencyMs > LINK_SLOW_MS;
  if (warnings.length) {
    const [first, ...rest] = warnings;
    return {
      id,
      name,
      tone: "warn",
      summary: first!.summary,
      age: formatSince(input.lastOkAt, now),
      detail: [first!.detail, ...rest.map((w) => w.summary)].join(" · "),
      action: first!.action ?? rest.find((w) => w.action)?.action ?? null,
      facts,
    };
  }
  return {
    id,
    name,
    tone: slow ? "warn" : "ok",
    summary: slow
      ? `${LINK.online}, pero lento (${formatLatency(input.latencyMs)})`
      : LINK.online,
    age: formatSince(input.lastOkAt, now),
    detail: slow
      ? "Cada lectura pasa por el puente (get_state); una respuesta lenta suele ser el puente o el Wi-Fi del robot."
      : null,
    facts,
  };
}

// ---------------------------------------------------------------------------
// Batería
// ---------------------------------------------------------------------------

/** A battery reading older than this (s) is not presented as current. */
export const BATTERY_STALE_S = 10;

export function classifyBattery(input: LinkInput, now: number): HealthRow {
  const id = "battery";
  const name = "Batería";
  const s = input.state;
  const raw = s?.raw;
  const pct = s?.battery_pct ?? null;
  const soh = raw?.battery_soh_pct ?? null;
  const current = raw?.battery_current_ma ?? null;

  const facts: HealthFact[] = [
    { label: "Carga", value: pct == null ? "—" : `${Math.round(pct)} %` },
    {
      label: "Salud (SoH)",
      value: soh == null ? "—" : `${soh} %`,
      tone: soh != null && soh > 100 ? "warn" : undefined,
    },
    {
      label: "Corriente",
      value:
        current == null
          ? "—"
          : current < 0
            ? `${Math.abs(current)} mA · descargando`
            : current > 0
              ? `${current} mA · cargando`
              : "0 mA · en reposo",
    },
  ];
  if (raw?.battery_age_s != null) {
    facts.push({
      label: "Última muestra BMS",
      value: ageText(raw.battery_age_s),
    });
  }

  if (!input.loaded && !s) return { ...idleRow(id, name), facts };

  if (!input.online) {
    return {
      id,
      name,
      tone: "unknown",
      summary:
        pct == null ? "Sin lectura" : `Última lectura: ${Math.round(pct)} %`,
      age: input.lastOkAt ? formatSince(input.lastOkAt, now) : null,
      detail:
        "Sin enlace con el robot: esto es la última lectura, no la carga actual.",
      facts,
    };
  }
  if (input.simulated) {
    return {
      id,
      name,
      tone: "unknown",
      summary: "Ficticia (stub)",
      age: null,
      detail: "El puente en modo stub inventa la batería.",
      facts,
    };
  }
  // The rt/lowstate dual writer: a second process publishes an incompatible
  // layout, and its samples decode as garbage. SoH above 100 is the impossible
  // value that marks them (99 is real ageing — do not discard it). While the
  // corrupt writer is latched, the charge figure beside it is unusable.
  if (soh != null && soh > 100) {
    return {
      id,
      name,
      tone: "warn",
      summary: "Lectura sospechosa (SoH > 100 %)",
      age: raw?.battery_age_s != null ? formatAge(raw.battery_age_s) : null,
      detail:
        "Esa muestra viene del publicador incompatible de rt/lowstate: la carga que la acompaña no es confiable.",
      action:
        "Confirmá la carga en el robot o el control remoto antes de planificar una tarea larga.",
      facts,
    };
  }
  if (pct == null) {
    return {
      id,
      name,
      tone: "unknown",
      summary: "Sin lectura del BMS",
      age: null,
      detail:
        s?.env === "real"
          ? "El robot todavía no publicó su estado de batería."
          : "El simulador no publica batería; null no significa batería llena.",
      facts,
    };
  }
  if (raw?.battery_age_s != null && raw.battery_age_s > BATTERY_STALE_S) {
    return {
      id,
      name,
      tone: "warn",
      summary: `${Math.round(pct)} %, pero de hace ${ageText(raw.battery_age_s)}`,
      age: formatAge(raw.battery_age_s),
      detail: "El BMS dejó de publicar; la carga real puede ser menor.",
      facts,
    };
  }
  const level = batteryLevel(pct);
  const flow =
    current == null
      ? ""
      : current < 0
        ? " · descargando"
        : current > 0
          ? " · cargando"
          : "";
  return {
    id,
    name,
    tone: level === "critical" ? "danger" : level === "low" ? "warn" : "ok",
    summary: `${Math.round(pct)} %${flow}`,
    age:
      raw?.battery_age_s != null
        ? formatAge(raw.battery_age_s)
        : formatSince(input.lastOkAt, now),
    detail:
      level === "critical"
        ? "Por debajo del umbral del fabricante (20 %)."
        : level === "low"
          ? "Planificá la sesión cerca del cargador."
          : null,
    action:
      level === "critical"
        ? "Llevá el robot a cargar antes de otra tarea."
        : null,
    facts,
  };
}

// ---------------------------------------------------------------------------
// Fallos — get_state faults, translated
// ---------------------------------------------------------------------------

export function classifyFaults(input: LinkInput, now: number): HealthRow {
  const id = "faults";
  const name = "Fallos";
  const s = input.state;
  if (!input.loaded && !s) return idleRow(id, name);
  const faults = readFaults(s?.faults);

  if (!input.online) {
    return {
      id,
      name,
      tone: "unknown",
      summary: faults.length
        ? `Última lectura: ${plural(faults.length, "fallo", "fallos")}`
        : "Sin lectura actual",
      age: input.lastOkAt ? formatSince(input.lastOkAt, now) : null,
      detail: "Sin enlace: la lista de fallos puede haber cambiado.",
      facts: [],
      faults,
    };
  }
  if (input.simulated) {
    return {
      id,
      name,
      tone: "unknown",
      summary: "Sin fallos informados (datos ficticios)",
      age: null,
      detail: "El stub nunca informa fallos; eso no dice nada del robot.",
      facts: [],
      faults,
    };
  }
  if (faults.length === 0) {
    return {
      id,
      name,
      tone: "ok",
      summary: "Sin fallos",
      age: formatSince(input.lastOkAt, now),
      detail: null,
      facts: [],
      faults,
    };
  }
  const danger = faults.some((f) => f.severity === "danger");
  const bridgeOnly = faults.every((f) => f.scope === "bridge");
  return {
    id,
    name,
    tone: danger ? "danger" : "warn",
    summary: plural(faults.length, "fallo", "fallos"),
    age: formatSince(input.lastOkAt, now),
    detail: bridgeOnly
      ? "Todos son de la salud del puente, no del robot."
      : null,
    facts: [],
    faults,
  };
}

// ---------------------------------------------------------------------------
// Percepción — GET /telemetry/surroundings
// ---------------------------------------------------------------------------

export type SurroundingsLite = {
  sources: Record<string, string>;
  report_age_s?: number;
  notes?: string[];
};

const SOURCE_NAME: Record<string, string> = {
  pose: "posición",
  detector: "detector",
  lidar: "LiDAR",
  costmap: "costmap",
};

const SOURCE_STATE: Record<string, string> = {
  ok: "bien",
  stale: "desactualizado",
  offline: "apagado",
};

/** A snapshot older than this (s) is reported as such. */
export const SCENE_STALE_S = 5;

export function classifyPerception(
  read: Read<SurroundingsLite>,
  now: number,
): HealthRow {
  const id = "perception";
  const name = "Percepción";
  if (read.state === "idle" || read.state === "skipped")
    return idleRow(id, name);
  if (read.state === "http" && read.status === 503) {
    return {
      id,
      name,
      tone: "warn",
      summary: "El puente no pudo armar la escena",
      age: readAge(read, now),
      detail: "No es una escena vacía: nadie está mirando.",
      ...bridge(bodyField(read.body, "error")),
      facts: [],
    };
  }
  if (read.state !== "ok") {
    return { id, name, facts: [], ...failedBackRead(read, now) };
  }
  const entries = Object.entries(read.data.sources ?? {});
  const facts: HealthFact[] = entries.map(([key, value]) => ({
    label: SOURCE_NAME[key] ?? key,
    value: SOURCE_STATE[value] ?? value,
    tone: value === "ok" ? "ok" : value === "stale" ? "warn" : "danger",
  }));
  const age = read.data.report_age_s;
  if (age != null) facts.push({ label: "Escena", value: formatAge(age) });

  if (entries.length === 0) {
    return {
      id,
      name,
      tone: "unknown",
      summary: "El puente no informa fuentes",
      age: readAge(read, now),
      detail: null,
      facts,
    };
  }
  const offline = entries
    .filter(([, v]) => v === "offline")
    .map(([k]) => SOURCE_NAME[k] ?? k);
  const stale = entries
    .filter(([, v]) => v === "stale")
    .map(([k]) => SOURCE_NAME[k] ?? k);
  const sceneStale = age != null && age > SCENE_STALE_S;
  const tone: HealthTone =
    offline.length === entries.length
      ? "danger"
      : offline.length || stale.length || sceneStale
        ? "warn"
        : "ok";
  const summary =
    tone === "ok"
      ? "Todas las fuentes al día"
      : offline.length === entries.length
        ? "Sin percepción: todas las fuentes apagadas"
        : [
            offline.length ? `apagado: ${offline.join(", ")}` : null,
            stale.length ? `desactualizado: ${stale.join(", ")}` : null,
            sceneStale ? `escena de ${formatAge(age)}` : null,
          ]
            .filter(Boolean)
            .join(" · ")
            .replace(/^./, (c) => c.toUpperCase());
  return {
    id,
    name,
    tone,
    summary,
    age: age != null ? formatAge(age) : readAge(read, now),
    detail: offline.includes("detector")
      ? "Con el detector apagado, una lista vacía de objetos NO significa camino libre."
      : null,
    action: offline.length
      ? "Levantá el stage de percepción que corresponda (c3po perception up <stage>)."
      : null,
    facts,
  };
}

// ---------------------------------------------------------------------------
// Voz — GET /telemetry/voice + the voice loop's own status
// ---------------------------------------------------------------------------

export type VoiceTelemetry = {
  running: boolean;
  error: string | null;
  always_listening: boolean;
  audio_source?: string;
  mic_ever_open: boolean;
  seconds_since_audio: number | null;
  utterances?: number;
};

/** With an always-on mic, this long without audio means the capture is dead (s). */
export const MIC_SILENT_S = 60;

export function classifyVoice(
  read: Read<VoiceTelemetry>,
  loop: { state: VoiceLoopState | null; reason: string | null },
  now: number,
): HealthRow {
  const id = "voice";
  const name = "Voz";
  const facts: HealthFact[] = [];
  const ls = loop.state;
  if (ls) {
    facts.push({
      label: "Conversación",
      value: ls.running
        ? `activa · ${ls.engine === "openai-realtime" ? "Realtime" : "local"}`
        : "apagada",
    });
  }
  if (loop.reason)
    facts.push({
      label: "Estado de la conversación",
      value: loop.reason,
      tone: "warn",
    });

  if (read.state === "idle" || read.state === "skipped")
    return { ...idleRow(id, name), facts };
  if (read.state === "http" && read.status === 503) {
    return {
      id,
      name,
      tone: "warn",
      summary: "El puente no expone la telemetría de voz",
      age: readAge(read, now),
      detail: "Puede que el stack de voz no esté instalado en este puente.",
      ...bridge(bodyField(read.body, "error")),
      facts,
    };
  }
  if (read.state !== "ok")
    return { id, name, facts, ...failedBackRead(read, now) };

  const v = read.data;
  facts.unshift(
    {
      label: "Micrófono",
      value: v.always_listening ? "siempre abierto" : "pulsar para hablar",
    },
    {
      label: "Último audio",
      value:
        v.seconds_since_audio == null
          ? "nunca"
          : formatAge(v.seconds_since_audio),
    },
  );
  if (v.audio_source)
    facts.push({ label: "Fuente", value: v.audio_source, lang: "en" });
  if (v.utterances != null)
    facts.push({ label: "Frases oídas", value: String(v.utterances) });

  if (v.error) {
    return {
      id,
      name,
      tone: "danger",
      summary: "El micrófono del robot falló",
      age: readAge(read, now),
      detail: null,
      ...bridge(v.error),
      facts,
    };
  }
  if (!v.running) {
    return {
      id,
      name,
      tone: "warn",
      summary: "El robot no está escuchando",
      age: readAge(read, now),
      detail:
        "La captura de audio del puente está detenida: ninguna conversación puede oír nada.",
      facts,
    };
  }
  if (
    v.always_listening &&
    v.seconds_since_audio != null &&
    v.seconds_since_audio > MIC_SILENT_S
  ) {
    return {
      id,
      name,
      tone: "warn",
      summary: `Micrófono abierto, sin audio ${formatAge(v.seconds_since_audio)}`,
      age: readAge(read, now),
      detail:
        "Con un micrófono siempre abierto, tanto silencio suele ser una captura caída.",
      facts,
    };
  }
  return {
    id,
    name,
    tone: "ok",
    summary: v.always_listening
      ? "Escuchando (micrófono siempre abierto)"
      : v.mic_ever_open
        ? "Escuchando (pulsar para hablar)"
        : "Listo · nadie apretó el botón del micrófono todavía",
    age: readAge(read, now),
    detail: null,
    facts,
  };
}

// ---------------------------------------------------------------------------
// Cámara — the robot camera's own /status (direct from the browser)
// ---------------------------------------------------------------------------

export type CameraStatus = {
  live: boolean;
  source?: string;
  frame_age_s: number | null;
  frames?: number;
  hint?: string;
};

export type CameraConfig = {
  /** PUBLIC_ROBOT_CAM_URL, trimmed. Empty = not configured. */
  robotUrl: string;
  /** PUBLIC_SIM_CAM_HOST. */
  simHost: string;
  /** The console's own protocol, for the mixed-content explanation. */
  pageProtocol: string;
};

export function classifyCamera(
  read: Read<CameraStatus>,
  config: CameraConfig,
  now: number,
): HealthRow {
  const id = "camera";
  const name = "Cámara";
  const facts: HealthFact[] = [];
  if (config.robotUrl) facts.push({ label: "Fuente", value: config.robotUrl });

  if (!config.robotUrl) {
    return {
      id,
      name,
      tone: "unknown",
      summary: "Modo simulador",
      age: null,
      detail:
        "PUBLIC_ROBOT_CAM_URL no está configurado, así que la consola usa las cámaras WebRTC del simulador" +
        (config.simHost ? ` en ${config.simHost}` : " en este mismo host") +
        ". Su estado se ve en Cámaras; acá no se sondea.",
      facts,
    };
  }
  if (read.state === "idle" || read.state === "skipped")
    return { ...idleRow(id, name), facts };
  if (read.state === "network") {
    const mixed =
      config.pageProtocol === "https:" && config.robotUrl.startsWith("http:");
    return {
      id,
      name,
      tone: "danger",
      summary: read.timeout
        ? "La cámara no contestó a tiempo"
        : "No se pudo leer el estado de la cámara",
      age: readAge(read, now),
      detail: mixed
        ? "La consola es HTTPS y la cámara HTTP: el navegador bloquea ese contenido mixto."
        : "Sin respuesta del puente en esa dirección. Puede ser la red (¿estás en la LAN del robot?), el nombre mDNS, o CORS — el navegador no dice cuál.",
      facts,
    };
  }
  if (read.state === "http") {
    return {
      id,
      name,
      tone: read.status === 503 ? "warn" : "danger",
      summary:
        read.status === 503
          ? "El puente no tiene cámara en este modo"
          : `La cámara respondió HTTP ${read.status}`,
      age: readAge(read, now),
      detail: null,
      ...bridge(bodyField(read.body, "hint") ?? bodyField(read.body, "error")),
      facts,
    };
  }
  const c = read.data;
  if (c.source) facts.push({ label: "Servidor", value: c.source, lang: "en" });
  facts.push({
    label: "Último cuadro",
    value: c.frame_age_s == null ? "nunca" : `${c.frame_age_s.toFixed(1)} s`,
  });
  if (c.live) {
    return {
      id,
      name,
      tone: "ok",
      summary: LINK.live,
      age: readAge(read, now),
      detail: null,
      facts,
    };
  }
  return {
    id,
    name,
    tone: "warn",
    summary:
      c.source === "none" || (c.frames ?? 0) === 0
        ? "Ninguna cámara entrega imagen"
        : `${LINK.noSignal}: sin cuadros nuevos`,
    age: readAge(read, now),
    detail: null,
    ...bridge(c.hint),
    action:
      "Mirá Cámaras para el detalle, o revisá quién tiene /dev/video4 (c3po camera take).",
    facts,
  };
}

// ---------------------------------------------------------------------------
// Mapa — GET /map/costmap.png (headers only matter)
// ---------------------------------------------------------------------------

export type CostmapProbe = { bytes: number };

export function classifyMap(read: Read<CostmapProbe>, now: number): HealthRow {
  const id = "map";
  const name = "Mapa";
  if (read.state === "idle" || read.state === "skipped")
    return idleRow(id, name);
  if (read.state === "http" && read.status === 503) {
    return {
      id,
      name,
      tone: "warn",
      summary: "Sin costmap: no hay un stage de Nav2 corriendo",
      age: readAge(read, now),
      detail: "No es un mapa vacío: nadie lo está publicando.",
      ...bridge(bodyField(read.body, "hint") ?? bodyField(read.body, "error")),
      facts: [],
    };
  }
  if (read.state !== "ok")
    return { id, name, facts: [], ...failedBackRead(read, now) };

  const h = read.headers ?? {};
  const ageS = Number.parseFloat(h["x-c3po-age-s"] ?? "");
  const stale = (h["x-c3po-stale"] ?? "").toLowerCase() === "true";
  const w = h["x-c3po-width"];
  const hh = h["x-c3po-height"];
  const res = h["x-c3po-resolution-m"];
  const facts: HealthFact[] = [
    {
      label: "Antigüedad",
      value: Number.isFinite(ageS) ? formatAge(ageS) : "—",
      tone: stale ? "warn" : undefined,
    },
    {
      label: "Tamaño",
      value:
        w && hh ? `${w} × ${hh} celdas${res ? ` · ${res} m/celda` : ""}` : "—",
    },
    { label: "Imagen", value: `${read.data.bytes} B` },
  ];
  if (h["x-c3po-frame"])
    facts.push({ label: "Marco", value: h["x-c3po-frame"]!, lang: "en" });
  return {
    id,
    name,
    tone: stale ? "warn" : Number.isFinite(ageS) ? "ok" : "unknown",
    summary: stale
      ? `Costmap desactualizado (${Number.isFinite(ageS) ? formatAge(ageS) : "edad desconocida"})`
      : Number.isFinite(ageS)
        ? "Costmap al día"
        : "Costmap sin edad informada",
    age: Number.isFinite(ageS) ? formatAge(ageS) : readAge(read, now),
    detail: stale
      ? "Nav2 dejó de publicar; el mapa que ves es el último que llegó."
      : null,
    facts,
  };
}

// ---------------------------------------------------------------------------
// Navegación autónoma — the Nav2 cmd_vel gate (shared Gate poller)
// ---------------------------------------------------------------------------

export function classifyGate(
  gate: {
    status: GateStatus | null;
    reason: string | null;
    /** `reason` is the bridge's English (a 503 body), not the console's Spanish. */
    reasonFromBridge?: boolean;
    loaded: boolean;
    lastOkAt: number | null;
  },
  now: number,
): HealthRow {
  const id = "gate";
  const name = "Navegación autónoma";
  const g = gate.status;
  if (!gate.loaded && !g) return idleRow(id, name);
  if (gate.reason) {
    // Unknown, never "closed": a gate we cannot read may be open — and if the
    // last reading said armed, that is the thing to say.
    const lastArmed = g?.enabled === true;
    const unread = "Que no se pueda leer no significa que esté cerrada.";
    // The poller's reason is either its own Spanish (it could not ask) or the
    // bridge's English (a 503 body); only the latter is bridge text, and the
    // poller says which.
    const own = gate.reasonFromBridge !== true;
    return {
      id,
      name,
      tone: lastArmed ? "warn" : "unknown",
      summary: lastArmed
        ? "Sin lectura actual · la última decía ARMADA"
        : "No se puede leer la compuerta de Nav2",
      age: gate.lastOkAt
        ? `última lectura ${formatSince(gate.lastOkAt, now)}`
        : null,
      detail: own ? `${gate.reason} ${unread}` : unread,
      ...(own ? {} : bridge(gate.reason)),
      action: lastArmed
        ? "Si no tiene que moverse solo, tocá PARAR (o Shift+Espacio)."
        : null,
      facts: [],
    };
  }
  if (!g) return idleRow(id, name);
  const facts: HealthFact[] = [
    { label: "cmd_vel recibidos", value: String(g.cmd_vel_received) },
    {
      label: "Descartados (cerrada)",
      value: String(g.dropped_while_disabled),
    },
    {
      label: "Recortados",
      value: String(g.clamped),
      tone: g.clamped > 0 ? "warn" : undefined,
    },
    {
      label: "Planificador",
      value:
        g.cmd_vel_age_s == null
          ? "no publica"
          : `último cmd_vel ${formatAge(g.cmd_vel_age_s)}`,
    },
  ];
  if (g.last_sent) {
    facts.push({
      label: "Último enviado",
      value: `vx ${g.last_sent[0]?.toFixed(2)} · vy ${g.last_sent[1]?.toFixed(2)} · wz ${g.last_sent[2]?.toFixed(2)}${g.last_sent_age_s != null ? ` · ${formatAge(g.last_sent_age_s)}` : ""}`,
    });
  }
  if (g.enabled) {
    return {
      id,
      name,
      tone: "warn",
      summary:
        g.arm_expires_in_s != null
          ? `ARMADA · se cierra en ${Math.max(0, Math.round(g.arm_expires_in_s))} s`
          : "ARMADA",
      age: formatSince(gate.lastOkAt, now),
      detail:
        g.cmd_vel_age_s == null
          ? "Nav2 puede mover el robot, aunque ahora no hay planificador publicando."
          : "Nav2 puede mover el robot ahora mismo.",
      action:
        "PARAR (arriba, o Shift+Espacio) cierra la compuerta para cualquiera.",
      facts,
    };
  }
  return {
    id,
    name,
    tone: "ok",
    summary: "Cerrada",
    age: formatSince(gate.lastOkAt, now),
    detail: g.disabled_reason
      ? null
      : "Nada fuera de la consola y del agente puede mover el robot.",
    ...bridge(g.disabled_reason),
    facts,
  };
}
