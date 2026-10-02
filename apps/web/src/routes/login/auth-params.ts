/**
 * What the /login and /signup loads read from their URL and from back: where to
 * go after signing in, which OAuth providers exist, and what an OAuth failure
 * Better Auth bounced back here means. Shared by both page loads.
 *
 * The protected layout and the pollers redirect here with
 * `?redirectTo=<path>&reason=expired`, so a Quest operator whose session lapsed
 * mid-teleop lands back on /vr-control rather than the dashboard. That
 * parameter is attacker-controlled — anyone can send a link to
 * `/login?redirectTo=https://evil.example` — so only same-origin paths survive.
 *
 * Plain module, no `$` imports, so `bun test` can load it.
 */

export const DEFAULT_REDIRECT = "/dashboard";

/**
 * `raw` if it is a same-origin absolute path, else the dashboard.
 *
 * Rejects `//host` and `/\host` (browsers treat both as protocol-relative), any
 * backslash (URL parsers normalise it to `/`), and the auth pages themselves (a
 * redirect back to /login is a loop).
 *
 * And anything that is not printable ASCII — controls (URL parsers strip them),
 * spaces, and every non-ASCII character. A real destination never has one: the
 * console builds `redirectTo` from `url.pathname + url.search`, which are
 * already percent-encoded. A hand-made `?redirectTo=/%E6%97%A5` decodes to
 * `/日`, and a `Location: /日` header is not a valid ByteString — building the
 * redirect THREW, so a signed-in operator following that link got a 500.
 */
export function safeRedirectTo(raw: string | null | undefined): string {
  if (!raw || raw.length > 2048) return DEFAULT_REDIRECT;
  if (!raw.startsWith("/") || raw.startsWith("//")) return DEFAULT_REDIRECT;
  if (raw.includes("\\")) return DEFAULT_REDIRECT;
  if (/[^\x21-\x7e]/.test(raw)) return DEFAULT_REDIRECT;
  const path = raw.split(/[?#]/, 1)[0];
  if (path === "/login" || path === "/signup" || path.startsWith("/login/")) {
    return DEFAULT_REDIRECT;
  }
  return raw;
}

export type SocialProvider = "github" | "google";

/**
 * The OAuth providers back has credentials for, from its public `GET /health`.
 * Anything malformed is "none": a button for a provider that is not configured
 * sends the operator off-site to Google's or GitHub's "missing client_id" page.
 */
export function readProviders(body: unknown): SocialProvider[] {
  const list =
    body && typeof body === "object"
      ? (body as { auth?: { providers?: unknown } }).auth?.providers
      : undefined;
  if (!Array.isArray(list)) return [];
  return list.filter(
    (p): p is SocialProvider => p === "github" || p === "google",
  );
}

/**
 * Better Auth's OAuth callback sends failures to `errorCallbackURL?error=<code>`.
 * Those codes are machine vocabulary in English; the operator gets Spanish.
 * Null when there is no error.
 */
export function oauthErrorMessage(
  code: string | null | undefined,
): string | null {
  if (!code) return null;
  switch (code) {
    case "access_denied":
      return "Cancelaste el ingreso con el proveedor.";
    case "state_mismatch":
    case "please_restart_the_process":
    case "invalid_code":
    case "no_code":
    case "invalid_callback_request":
      return "El ingreso con el proveedor se interrumpió. Probá de nuevo.";
    case "account_not_linked":
    case "account_already_linked_to_different_user":
    case "unable_to_link_account":
      return "Ese email ya tiene una cuenta. Ingresá con tu email y contraseña.";
    case "email_not_found":
    case "email_doesn't_match":
      return "El proveedor no compartió un email que podamos usar.";
    case "signup_disabled":
      return "El registro con proveedores externos está deshabilitado.";
    case "oauth_provider_not_found":
      return "Ese método de ingreso no está habilitado en este servidor.";
    default:
      return "No se pudo ingresar con el proveedor. Probá de nuevo o usá tu email.";
  }
}
