/**
 * What a skill result MEANS — one classifier for every surface that shows one.
 *
 * THE BRIDGE NEVER RAISES. A firmware refusal comes back as a successful tool
 * result carrying `{status: "failed", phase: "rpc_error", error: "rpc_error_code_7401"}`,
 * apps/back answers HTTP 200, and any surface that reads only the HTTP status
 * calls it success. Worse, several outcomes that are not failures are not
 * successes either: a bring-up step the firmware acked but did not honour
 * (`acked_no_transition`), a walk that timed out short of its target
 * (`result.arrived: false`), a request the stub bridge logged without sending
 * (`phase: "stub" | "logged_only"`), a stop whose damp fallback ran out of retries
 * (`real_damp_fallback_succeeded: false`).
 *
 * There used to be four classifiers for this one shape — the e-stop (HTTP status
 * only), the VR tiles, the VR walk loop (HTTP status only) and the chat tool
 * cards (`error` string only) — and the same bring-up result rendered as a
 * failure in /vr-control and as a green "Completado" in /chat. Now there is one.
 *
 * Plain module, no runes, so `bun test` can load it.
 */

/**
 * - `ok`         — it did what was asked, as far as the bridge can tell.
 * - `warn`       — it did not fail, but it did not do the thing either
 *                  (cancelled, stub, not applicable, did not arrive).
 * - `error`      — it failed or was refused.
 * - `unverified` — it was sent and accepted, and nobody checked the result.
 *                  Not the same as failed, and must not read as either.
 */
export type OutcomeKind = "ok" | "warn" | "error" | "unverified";

export type SkillOutcome = {
  kind: OutcomeKind;
  /** Short Spanish label — fits a badge or a tile. */
  text: string;
  /**
   * More context. For a RESULT body it is the bridge's own explanation,
   * verbatim (usually English); for a transport failure (`classifyHttpError`)
   * it is the console's own Spanish hint, or back's `detail` verbatim. Tag
   * `lang` accordingly — `code` tells the two apart.
   */
  detail: string | null;
  /** The machine code behind the outcome (`rpc_error_code_7401`, `bridge_unavailable`, …). */
  code: string | null;
  /** True when the bridge says it only pretended (stub/logged_only/`stub: true`). */
  simulated: boolean;
};

/** Firmware/bridge codes worth naming. Unknown codes fall through to the code. */
export const RPC_HINTS: Record<string, string> = {
  rpc_error_code_7401:
    "Un brazo quedó sosteniendo la pose anterior — relajá los brazos",
  rpc_error_code_7400:
    "rt/arm_sdk está ocupado — apagá el espejo de brazos (o el stack del compañero)",
  rpc_error_code_7404: "El servicio de brazos rechazó el gesto en este estado",
  rpc_error_code_7302:
    "El robot no está en modo de marcha — habilitá la marcha primero",
};

/** Phases whose meaning is the same wherever they appear. */
const PHASE_TEXT: Record<string, string> = {
  teleop_active: "Otro operador tiene la locomoción (teleoperación activa)",
  arm_sdk_engaged: "Los brazos están tomados por el espejo de teleoperación",
  unknown_gesture: "Gesto desconocido",
  bad_arguments: "Parámetros inválidos",
  settle_timeout: "El brazo no llegó a la pose a tiempo",
  driver_failed: "Falló el driver",
  release_failed: "Hecho, pero no logró soltar el brazo",
  exception: "Error interno del puente",
  unavailable: "No disponible en este robot",
};

const str = (v: unknown): string | null =>
  typeof v === "string" && v.length > 0 ? v : null;

const obj = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;

function make(
  kind: OutcomeKind,
  text: string,
  extra: Partial<Omit<SkillOutcome, "kind" | "text">> = {},
): SkillOutcome {
  return {
    kind,
    text,
    detail: extra.detail ?? null,
    code: extra.code ?? null,
    simulated: extra.simulated ?? false,
  };
}

/**
 * Classify a skill's RESULT BODY — an HTTP 200 from `/skills/:name/invoke`, or a
 * tool output in an agent chat. Never looks at transport; see `classifyHttpError`.
 */
export function classifySkillResult(body: unknown): SkillOutcome {
  const b = obj(body);
  if (!b) return make("ok", "Enviado");

  const status = str(b.status);
  const result = obj(b.result) ?? {};
  const phase = str(b.phase) ?? "";
  const err = str(b.error);
  // `gesture` (and other task-runtime skills) put their explanation inside
  // `result.note`, not at the top level.
  const note =
    str(b.note) ??
    str(b.message) ??
    str(b.hint) ??
    str(b.reason) ??
    str(result.note);
  const simulated =
    b.stub === true || phase === "stub" || phase === "logged_only";

  // A failure, explicitly. The bridge's code is the most specific thing we have.
  if (status === "failed" || err) {
    const code = err ?? (phase || null);
    const text =
      (err && RPC_HINTS[err]) ??
      PHASE_TEXT[phase] ??
      (phase === "rpc_error" ? "El robot rechazó el pedido" : null) ??
      "Falló";
    return make("error", text, { detail: note ?? err, code, simulated });
  }

  // The stop's damp fallback is the one place a 200 can mean "the robot may
  // still be powered and moving". stop_everything reports it honestly.
  if (b.real_damp_fallback_succeeded === false) {
    return make("error", "La parada no logró amortiguar el robot", {
      detail: note,
      code: "real_damp_fallback_failed",
    });
  }

  if (b.ok === false) {
    return make("error", note ? `Rechazado: ${note}` : "Rechazado", {
      detail: note,
      code: str(b.reason),
    });
  }

  if (status === "cancelled" || phase === "cancelled") {
    return make("warn", "Cancelado", { detail: note, code: "cancelled" });
  }

  if (status === "not_applicable") {
    return make("warn", "No aplica en este modo", {
      detail: note,
      code: "not_applicable",
    });
  }

  if (status === "unavailable") {
    return make("warn", "No disponible", { detail: note, code: "unavailable" });
  }

  if (status === "not_found") {
    return make("warn", "No encontrado", { detail: note, code: "not_found" });
  }

  // Logged but never sent — the stub bridge, or Isaac for firmware-only calls.
  if (phase === "stub" || phase === "logged_only") {
    return make("warn", "Registrado, no enviado (simulación)", {
      detail: note,
      code: phase,
      simulated: true,
    });
  }

  // The firmware acks FSM ids it will not honour — SetFsmId(99999) returns 0 —
  // so a zero code proves nothing about a bring-up step. The bridge checks
  // afterwards and says so.
  if (phase === "acked_no_transition" || result.transitioned === false) {
    // 2026-10-15: 501 from FSM 4 on the hanger, feet not loaded. The fix is
    // physical, so say it in the operator's language, not the bridge's.
    const walkRefused =
      (result.fsm_target === 501 || result.fsm_target === 500) &&
      result.fsm_after === 4;
    return make(
      "error",
      walkRefused
        ? "No pasó a marcha: apoyá los pies en el piso"
        : "Aceptado, pero el robot NO cambió de estado",
      {
        detail: walkRefused
          ? "El robot quedó en FSM 4 (preparado). Para caminar los dos pies tienen que cargar peso: bajá el arnés hasta que apoye (cuerda floja pero enganchada), esperá ~5 s después de Preparar y volvé a probar."
          : note,
        code: "acked_no_transition",
      },
    );
  }

  if (result.arrived === false) {
    return make(
      "warn",
      phase === "timeout"
        ? "Se agotó el tiempo sin llegar"
        : "No llegó al destino",
      { detail: note, code: phase || "not_arrived", simulated },
    );
  }

  if (phase === "release_failed") {
    return make("warn", PHASE_TEXT.release_failed, {
      detail: note,
      code: phase,
    });
  }

  if (result.transitioned === true) {
    return make("ok", "Listo", { detail: note, simulated });
  }

  // `transitioned: null` means nobody verified — distinct from failed.
  if ("transitioned" in result && result.transitioned === null) {
    return make("unverified", "Enviado (sin confirmar)", { detail: note });
  }

  // A stub bridge answers `{status: "ok", stub: true}` for motion it never
  // sent (walk_velocity, turn, …). Fabricated is not done.
  if (b.stub === true) {
    return make("warn", "Simulado — no se envió al robot", {
      detail: note,
      code: "stub",
      simulated: true,
    });
  }

  if (status === "running") {
    return make("unverified", "En curso", { detail: note, code: "running" });
  }

  return make("ok", status === "completed" ? "Completado" : "Enviado", {
    detail: note,
    simulated,
  });
}

/**
 * Classify a FAILED REQUEST to `/skills/:name/invoke` (or `/tasks/...`).
 *
 * `status` is null when the request never got an HTTP answer (network error).
 * `body` is back's error body — `{error: "bridge_unavailable"}`,
 * `{error: "tool_error", detail}`, `{error: "admin_required"}`, …
 */
export function classifyHttpError(
  status: number | null,
  body: unknown,
): SkillOutcome {
  const b = obj(body);
  const code = str(b?.error);
  const detail = str(b?.detail) ?? str(b?.message);

  if (status == null) {
    return make("error", "No se pudo contactar al servidor", {
      code: "network",
    });
  }
  if (status === 401) {
    return make("error", "Sesión vencida", { code: "unauthorized" });
  }
  if (status === 403 || code === "admin_required") {
    return make("warn", "Requiere una cuenta admin", {
      detail,
      code: "admin_required",
    });
  }
  if (status === 422 || code === "invalid_params") {
    return make("error", "Parámetros inválidos", {
      detail: detail ?? JSON.stringify(b?.issues ?? null),
      code: "invalid_params",
    });
  }
  if (status === 404 || code === "skill_not_found") {
    return make("error", "Habilidad desconocida para el puente", {
      code: "skill_not_found",
    });
  }
  // 502 is the only thing apps/back turns a bridge problem into. Which 502 it
  // is matters: an unreachable bridge cannot be fixed by retrying the button.
  if (code === "bridge_unavailable") {
    return make("error", "El puente no responde", {
      detail: "¿Está activo c3po-bridge.service, o el túnel SSH?",
      code,
    });
  }
  // back stopped WAITING; the skill was dispatched and may still be running on
  // the bridge. Not the same as never sent.
  if (status === 504 || code === "bridge_timeout") {
    return make("error", "El puente no respondió a tiempo", {
      detail: "La orden se envió; puede seguir ejecutándose en el robot.",
      code: "bridge_timeout",
    });
  }
  if (code === "tool_error") {
    return make("error", "El puente devolvió un error", { detail, code });
  }
  return make("error", `Falló (HTTP ${status})`, { detail, code });
}

/** Classify an Eden `{data, error}` pair from `/skills/:name/invoke`. */
export function classifyInvoke(res: {
  data?: unknown;
  error?: { status: unknown; value?: unknown } | null;
}): SkillOutcome {
  if (res.error) {
    // Eden never throws for a failed fetch: it returns `{status: 503, value:
    // <the thrown Error>}`. A real back 503 carries a JSON body, so an Error
    // value means the browser never reached back — "Falló (HTTP 503)" was a lie
    // about who answered.
    if (res.error.value instanceof Error) return classifyHttpError(null, null);
    const status =
      typeof res.error.status === "number" ? res.error.status : null;
    return classifyHttpError(status, res.error.value);
  }
  return classifySkillResult(res.data);
}

/** Tailwind text colour per outcome kind. */
export const OUTCOME_TONE: Record<OutcomeKind, string> = {
  ok: "text-ok",
  warn: "text-warn",
  error: "text-danger-soft",
  unverified: "text-ink-dim",
};
