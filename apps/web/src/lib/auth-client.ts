import { createAuthClient } from "better-auth/svelte";
import { organizationClient, adminClient } from "better-auth/client/plugins";
import { PUBLIC_API_URL } from "$env/static/public";

export const authClient = createAuthClient({
  baseURL: PUBLIC_API_URL,
  plugins: [organizationClient(), adminClient()],
});

/**
 * What the forms say when the request never got an answer. better-fetch only
 * turns a refused connection into an `{ error }` under `catchAllError`, which
 * Better Auth does not set — so it THROWS, and a form that only handled
 * `onError` spun "Ingresando…" forever with every input locked.
 */
export const AUTH_NETWORK_ERROR =
  "No se pudo contactar al servidor. Revisá tu conexión o probá de nuevo en un rato.";

/**
 * Better Auth's error codes, in the console's Spanish.
 *
 * The forms used to render `ctx.error.message` verbatim — "Invalid email or
 * password", "User already exists. Use another email." — the most common error
 * on the page, in English, inside a voseo console.
 */
const AUTH_ERRORS: Record<string, string> = {
  INVALID_EMAIL_OR_PASSWORD: "Email o contraseña incorrectos.",
  INVALID_PASSWORD: "Contraseña incorrecta.",
  INVALID_EMAIL: "Ese email no es válido.",
  USER_ALREADY_EXISTS:
    "Ya existe una cuenta con ese email. Ingresá o usá otro email.",
  USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL:
    "Ya existe una cuenta con ese email. Ingresá o usá otro email.",
  PASSWORD_TOO_SHORT: "La contraseña debe tener al menos 8 caracteres.",
  PASSWORD_TOO_LONG: "La contraseña es demasiado larga.",
  EMAIL_NOT_VERIFIED: "Tenés que verificar tu email antes de ingresar.",
  BANNED_USER: "Esta cuenta está suspendida. Hablá con un administrador.",
  PROVIDER_NOT_FOUND: "Ese método de ingreso no está habilitado.",
  TOO_MANY_REQUESTS: "Demasiados intentos. Esperá un momento y probá de nuevo.",
  // Back refused the request because of WHERE it came from: the console's
  // origin is not in its trusted origins (`WEB_URL` in apps/back/.env), or the
  // browser sent no Origin at all. Typical cause: opening the console by LAN IP
  // (a phone, the Quest) while back trusts only http://localhost:3001. "Probá
  // de nuevo" — what these fell through to — can never work.
  ...Object.fromEntries(
    [
      "INVALID_ORIGIN",
      "MISSING_OR_NULL_ORIGIN",
      "INVALID_CALLBACK_URL",
      "INVALID_NEW_USER_CALLBACK_URL",
      "INVALID_ERROR_CALLBACK_URL",
      "INVALID_REDIRECT_URL",
      "CROSS_SITE_NAVIGATION_LOGIN_BLOCKED",
    ].map((code) => [
      code,
      "El servidor no acepta pedidos desde esta dirección de la consola. Abrila con la dirección habitual; si es la habitual, hay que agregarla a WEB_URL en la configuración del servidor (apps/back).",
    ]),
  ),
};

/** Which field an error is about, for `aria-invalid`. */
export type AuthErrorField = "email" | "password" | "credentials" | null;

export function authErrorField(code: string | undefined): AuthErrorField {
  switch (code) {
    case "INVALID_EMAIL_OR_PASSWORD":
      return "credentials";
    case "INVALID_EMAIL":
    case "USER_ALREADY_EXISTS":
    case "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL":
      return "email";
    case "INVALID_PASSWORD":
    case "PASSWORD_TOO_SHORT":
    case "PASSWORD_TOO_LONG":
      return "password";
    default:
      return null;
  }
}

/** Spanish for a Better Auth `{ error }`. Never the raw English message. */
export function authErrorMessage(
  error: { code?: string; status?: number } | null | undefined,
): string {
  if (!error) return "No se pudo completar el pedido. Probá de nuevo.";
  // Better Auth's rate limiter answers 429 with a message and no code.
  if (error.status === 429) return AUTH_ERRORS.TOO_MANY_REQUESTS;
  if (error.code && AUTH_ERRORS[error.code]) return AUTH_ERRORS[error.code];
  if ((error.status ?? 0) >= 500) {
    return "El servidor tuvo un problema. Probá de nuevo en un rato.";
  }
  return "No se pudo completar el pedido. Probá de nuevo.";
}
