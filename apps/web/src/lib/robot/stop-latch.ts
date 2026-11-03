/**
 * The PARAR latch, as the console reads it from `GET /safety/status`.
 *
 * After a stop, the bridge refuses every motion tool, for the agent and the
 * console alike, until an operator resumes («Reanudar» → `POST /safety/ack`,
 * or re-engaging the headset). Before that existed, PARAR stopped the robot and
 * the agent's very next step moved it again.
 *
 * Pure: the poller in stop-latch.svelte.ts holds the state, this decides what
 * it means, so it can be tested without a browser.
 */

export type SafetyStatus = {
  stop_standing?: boolean | null;
  stopped_at?: number | null;
  acknowledged_at?: number | null;
};

/** Standing only when the bridge says so. Never inferred, never from a failed read. */
export function stopStanding(status: SafetyStatus | null): boolean {
  return status?.stop_standing === true;
}

/** "hace 12 s" / "hace 3 min", from the bridge's unix stamp. Null when unknown. */
export function stoppedAgo(
  status: SafetyStatus | null,
  nowMs: number,
): string | null {
  const at = status?.stopped_at;
  if (typeof at !== "number" || at <= 0) return null;
  const s = Math.max(0, Math.round(nowMs / 1000 - at));
  return s < 90 ? `hace ${s} s` : `hace ${Math.round(s / 60)} min`;
}

/** What to tell the operator when «Reanudar» fails. */
export function resumeError(httpStatus: number, body: unknown): string {
  const code = (body as { error?: string } | null)?.error;
  if (httpStatus === 403 && code === "admin_required")
    return "Solo un administrador puede reanudar.";
  if (httpStatus === 503)
    return "Reanudar no está configurado (falta SAFETY_CONFIRM_SECRET).";
  if (httpStatus === 502) return "El puente no responde: no se pudo reanudar.";
  return `No se pudo reanudar (${httpStatus}${code ? `: ${code}` : ""}).`;
}
