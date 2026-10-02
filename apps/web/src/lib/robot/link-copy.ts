/**
 * One vocabulary for "the console cannot see the robot".
 *
 * The same condition used to be called "Offline", "Sin conexión", "sin enlace",
 * "Sin señal", "Sin puente", "bridge unreachable" and "back is unreachable" —
 * and /live-map showed four of those at once. Each word implies a different
 * fault and a different fix, so they are not interchangeable:
 *
 *  - `offline`       — /state did not answer. We do not know why.
 *  - `noBridge`      — back answered and SAID the bridge is down (502
 *                      bridge_unavailable). Only use when that was reported.
 *  - `noApi`         — the browser could not reach back at all.
 *  - `expired`       — back answered 401. Nothing is wrong with the robot.
 *  - `live`/`noSignal` — media vocabulary (camera, map). Never for the link.
 */
export const LINK = {
  online: "En línea",
  offline: "Sin conexión",
  noBridge: "Sin puente",
  noApi: "Sin acceso a la API",
  expired: "Sesión vencida",
  live: "En vivo",
  noSignal: "Sin señal",
  stale: "Desactualizado",
} as const;

/** Longer, actionable sentences for the same states. */
export const LINK_DETAIL = {
  offline:
    "La consola no recibe el estado del robot. Lo que ves es la última lectura.",
  /** Offline, and no reading ever arrived — there is no "last reading" to show. */
  never: "La consola todavía no recibió ningún estado del robot.",
  noBridge:
    "El servidor responde, pero el puente del robot no. Revisá el túnel SSH o el servicio c3po en el robot.",
  noApi:
    "La consola no llega al servidor (apps/back). Revisá que esté corriendo.",
  expired: "Tu sesión venció. Volvé a iniciar sesión.",
} as const;

/** Why there is no current state. (`never` is a display variant, not a cause.) */
export type LinkProblem = "offline" | "noBridge" | "noApi" | "expired";

/** Classify a back error body into the link vocabulary. */
export function linkFromError(
  status: number | null | undefined,
  body: unknown,
): LinkProblem {
  if (status === 401) return "expired";
  // Eden's shape for a fetch that threw: `{status: 503, value: Error}`.
  if (body instanceof Error) return "noApi";
  const code =
    body && typeof body === "object" && "error" in body
      ? (body as { error?: unknown }).error
      : undefined;
  if (code === "bridge_unavailable") return "noBridge";
  if (status == null) return "noApi";
  return "offline";
}
