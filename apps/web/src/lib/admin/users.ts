/**
 * User administration rules for /settings — plain module, tested.
 *
 * Better Auth's admin plugin (already mounted on both ends, `authClient.admin.*`)
 * enforces permissions itself. What it does not do is stop an admin from
 * locking THEMSELVES out: it refuses a self-ban, but happily lets the only
 * admin demote themselves, after which nobody can promote anyone without a
 * database prompt or an ADMIN_EMAILS edit plus a backend restart. The console
 * refuses those self-actions before they are sent.
 *
 * Errors arrive as Better Auth codes with English messages; operators read
 * Spanish. Unknown codes keep the server's message verbatim (tagged English by
 * the caller) rather than being replaced by a guess.
 */

export type UserRole = "admin" | "user";

export function isAdminRole(role: string | null | undefined): boolean {
  // Better Auth stores multiple roles comma-separated.
  return (role ?? "")
    .split(",")
    .map((r) => r.trim())
    .includes("admin");
}

/** The badge. Anything that is not admin is an operator — null included. */
export function roleLabel(
  role: string | null | undefined,
): "Admin" | "Operador" {
  return isAdminRole(role) ? "Admin" : "Operador";
}

export type SelfAction = "demote" | "ban" | "revoke";

/**
 * Why an action on `targetId` is refused for `actorId`, or null if allowed.
 * Only self-actions are refused here; everything else is Better Auth's call.
 */
export function selfActionBlock(
  actorId: string | null | undefined,
  targetId: string,
  action: SelfAction,
): string | null {
  if (!actorId || actorId !== targetId) return null;
  switch (action) {
    case "demote":
      return "No podés quitarte el rol de admin a vos mismo: te quedarías sin forma de deshacerlo. Pedíselo a otro admin.";
    case "ban":
      return "No podés bloquear tu propia cuenta.";
    case "revoke":
      return "Eso cerraría tu propia sesión. Usá «Cerrar sesión».";
  }
}

const ERROR_TEXT: Record<string, string> = {
  YOU_CANNOT_BAN_YOURSELF: "No podés bloquear tu propia cuenta.",
  YOU_ARE_NOT_ALLOWED_TO_CHANGE_USERS_ROLE:
    "Tu cuenta no tiene permiso para cambiar roles. Si te hicieron admin hace poco, tocá «Actualizar rol».",
  YOU_ARE_NOT_ALLOWED_TO_LIST_USERS:
    "Tu cuenta no tiene permiso para ver usuarios. Si te hicieron admin hace poco, tocá «Actualizar rol».",
  YOU_ARE_NOT_ALLOWED_TO_BAN_USERS:
    "Tu cuenta no tiene permiso para bloquear usuarios.",
  YOU_ARE_NOT_ALLOWED_TO_REVOKE_USERS_SESSIONS:
    "Tu cuenta no tiene permiso para cerrar sesiones de otros.",
  YOU_ARE_NOT_ALLOWED_TO_SET_NON_EXISTENT_VALUE: "Ese rol no existe.",
  INVALID_ROLE_TYPE: "Ese rol no existe.",
  USER_NOT_FOUND: "Ese usuario ya no existe.",
  UNAUTHORIZED: "Tu sesión venció. Volvé a iniciar sesión.",
  FORBIDDEN: "Tu cuenta no tiene permiso para esto.",
};

/**
 * A Better Auth client error, in Spanish. `verbatim` carries the server's own
 * message when the code is unknown, so the caller can show it tagged `en`.
 */
export function adminErrorText(
  error:
    | { code?: string | null; message?: string | null; status?: number }
    | null
    | undefined,
): { text: string; verbatim: string | null } {
  if (!error) return { text: "Error desconocido.", verbatim: null };
  const known = error.code ? ERROR_TEXT[error.code] : undefined;
  if (known) return { text: known, verbatim: null };
  if (error.status === 401)
    return { text: ERROR_TEXT.UNAUTHORIZED!, verbatim: null };
  if (error.status === 403)
    return { text: ERROR_TEXT.FORBIDDEN!, verbatim: error.message ?? null };
  if (error.status === 0 || error.status === undefined) {
    return {
      text: "No se pudo contactar al servidor (apps/back).",
      verbatim: error.message ?? null,
    };
  }
  // 5xx: back broke, it did not refuse. Its message is not shown — an
  // internal error's text can carry SQL or a stack frame; back logs it.
  if (error.status >= 500) {
    return {
      text: `El servidor falló al procesar el pedido (HTTP ${error.status}). El detalle queda en el log de apps/back.`,
      verbatim: null,
    };
  }
  return {
    text: `El servidor rechazó el pedido (HTTP ${error.status}).`,
    verbatim: error.message ?? null,
  };
}

/**
 * The visible sentence under your own row, naming what you cannot do to
 * yourself and why — or null when nothing is blocked.
 *
 * The reason used to live only in each disabled button's `title`: invisible on
 * a phone or a Quest, never announced by a screen reader for a disabled
 * control, and three separate tooltips for one fact. This is that fact once,
 * in text, and the blocked buttons point at it (`aria-describedby`).
 */
export function selfRowNote(blocked: readonly SelfAction[]): string | null {
  if (blocked.length === 0) return null;
  const verbs: Record<SelfAction, string> = {
    demote: "quitarte el rol de admin",
    ban: "bloquearte",
    revoke: "cerrar tus propias sesiones",
  };
  const order: SelfAction[] = ["demote", "ban", "revoke"];
  const list = order.filter((a) => blocked.includes(a)).map((a) => verbs[a]);
  const joined =
    list.length === 1
      ? list[0]!
      : `${list.slice(0, -1).join(", ")} ni ${list[list.length - 1]}`;
  const why = blocked.includes("demote")
    ? " Quitarte el admin no tendría vuelta atrás desde la consola: pedíselo a otro admin."
    : "";
  const out = blocked.includes("revoke")
    ? " Para salir, usá «Cerrar sesión»."
    : "";
  return `Es tu cuenta: no podés ${joined} desde acá.${why}${out}`;
}

/** Page size for the users table. */
export const USERS_PAGE = 20;
