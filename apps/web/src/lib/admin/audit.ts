/**
 * The command log (GET /audit) as /health's admin section shows it — plain
 * module, tested.
 *
 * A row's `status` is what back recorded: `error` for a transport failure or
 * a body that reported one. A row recorded `ok` can still be a stub that only
 * logged, a walk that did not arrive, or a bring-up the robot acked and
 * ignored — so when the result body was stored, it goes through the SAME
 * classifier every other surface uses (`skill-outcome.ts`). One result, one
 * meaning, on every page.
 */

import { classifySkillResult, type OutcomeKind } from "../robot/skill-outcome";

export type AuditSource = "agent" | "voice" | "direct";

export type AuditRow = {
  id: string;
  createdAt: string | Date;
  skillName: string;
  params: Record<string, unknown> | null;
  result: unknown;
  status: "ok" | "error";
  error: string | null;
  durationMs: number | null;
  chatId: string | null;
  source: AuditSource | null;
  user: { id: string; name: string; email: string } | null;
};

export type AuditFilters = {
  skill: string;
  status: "" | "ok" | "error";
  source: "" | AuditSource;
};

export const SOURCE_LABEL: Record<AuditSource, string> = {
  agent: "Agente",
  voice: "Voz",
  direct: "Directo",
};

export function sourceLabel(source: AuditSource | null): string {
  // Rows written before the column existed. Not "direct": we do not know.
  return source ? SOURCE_LABEL[source] : "Sin origen";
}

/** The query for one page. Empty filters are omitted, not sent as "". */
export function auditQuery(
  filters: AuditFilters,
  before: string | null,
  limit = 50,
): Record<string, string> {
  const q: Record<string, string> = { limit: String(limit) };
  const skill = filters.skill.trim();
  if (skill) q.skill = skill;
  if (filters.status) q.status = filters.status;
  if (filters.source) q.source = filters.source;
  if (before) q.before = before;
  return q;
}

export type RowOutcome = {
  kind: OutcomeKind;
  text: string;
  detail: string | null;
};

export function rowOutcome(row: AuditRow): RowOutcome {
  const hasResult = row.result !== null && row.result !== undefined;
  if (row.status === "error") {
    if (hasResult) {
      const o = classifySkillResult(row.result);
      if (o.kind === "error")
        return { kind: "error", text: o.text, detail: o.detail ?? row.error };
    }
    return { kind: "error", text: "Falló", detail: row.error };
  }
  if (!hasResult) return { kind: "ok", text: "Enviado", detail: null };
  const o = classifySkillResult(row.result);
  return { kind: o.kind, text: o.text, detail: o.detail };
}

const EXACT = new Intl.DateTimeFormat("es-AR", {
  dateStyle: "short",
  timeStyle: "medium",
});

/** Exact local timestamp, for the `title` of a relative time. */
export function exactTime(at: string | Date): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "—" : EXACT.format(d);
}

export function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** Who, in one line. Null means nobody we know — never invent a name. */
export function whoLabel(user: AuditRow["user"]): string {
  if (!user) return "Desconocido";
  return user.name || user.email || user.id;
}

/**
 * Why a GET /audit read produced no rows, worded by WHO failed.
 *
 *  - `expired` — a 401. A session problem; the caller sends the admin to log in.
 *  - `noApi`   — Eden never throws on a failed fetch: it resolves with
 *    `{status: 503, value: <the thrown Error>}`. A real back 503 carries a JSON
 *    body, so an Error value means the browser never reached back at all, and
 *    "the server failed" would blame the wrong machine.
 *  - `failed`  — 5xx: back broke while reading. Its body is NEVER shown: a
 *    database error's message can carry the SQL it was running, and an admin
 *    screen is not where that belongs (back logs it).
 *  - `forbidden` / `rejected` — 4xx: back understood and said no. Its
 *    `message` is the validator's own words (`status must be one of ok,
 *    error`), English and useful, so it is kept — verbatim, tagged `en` by the
 *    template.
 */
export type AuditError = {
  kind: "expired" | "noApi" | "failed" | "forbidden" | "rejected";
  text: string;
  /** Back's own words, English. Only ever set for a 4xx. */
  verbatim: string | null;
};

export function auditErrorText(status: unknown, value: unknown): AuditError {
  if (value instanceof Error) {
    const timeout = value.name === "TimeoutError";
    return {
      kind: "noApi",
      text: timeout
        ? "El servidor no contestó a tiempo (8 s)."
        : "La consola no llega al servidor (apps/back). Revisá que esté corriendo.",
      verbatim: null,
    };
  }
  const code = typeof status === "number" ? status : null;
  if (code === 401) {
    return { kind: "expired", text: "Tu sesión venció.", verbatim: null };
  }
  if (code === 403) {
    return {
      kind: "forbidden",
      text: "Tu cuenta ya no figura como admin para el servidor. Actualizá el rol en Configuración.",
      verbatim: null,
    };
  }
  if (code == null || code >= 500) {
    return {
      kind: "failed",
      text: `El servidor falló al leer el registro${code ? ` (HTTP ${code})` : ""}. El detalle queda en el log de apps/back.`,
      verbatim: null,
    };
  }
  const message =
    value &&
    typeof value === "object" &&
    typeof (value as { message?: unknown }).message === "string"
      ? (value as { message: string }).message.trim() || null
      : null;
  return {
    kind: "rejected",
    text: `El servidor rechazó la consulta (HTTP ${code}).`,
    verbatim: message,
  };
}
