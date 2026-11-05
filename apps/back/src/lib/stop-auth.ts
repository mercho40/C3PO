/**
 * Is this request carrying a session cookie THIS server signed?
 *
 * For one decision only: whether PARAR may go out when the normal session
 * lookup cannot run. `getSession` needs Postgres once the 5-minute cookie cache
 * is stale (lib/auth.ts), so a database outage turned the console's stop into a
 * 500 that never reached the robot.
 *
 * Better Auth stores the session cookie as `token.signature`, the signature
 * being base64 HMAC-SHA256 of the token under BETTER_AUTH_SECRET (better-call's
 * `signCookieValue`, checked in the installed 1.3.5). Verifying it proves only
 * that this server issued the cookie at some point — not that the session is
 * current or unrevoked. That is the right bar for a STOP and for nothing else:
 * the worst a stale cookie can do here is stop the robot.
 */

const NAMES = [
  "better-auth.session_token",
  "__Secure-better-auth.session_token",
];

function sessionCookie(cookieHeader: string | null): string | null {
  if (!cookieHeader) return null;
  for (const part of cookieHeader.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const name = part.slice(0, eq).trim();
    if (!NAMES.includes(name)) continue;
    try {
      return decodeURIComponent(part.slice(eq + 1).trim());
    } catch {
      return null;
    }
  }
  return null;
}

export async function hasSignedSessionCookie(
  cookieHeader: string | null,
  secret: string,
): Promise<boolean> {
  const value = sessionCookie(cookieHeader);
  if (!value || !secret) return false;
  const dot = value.lastIndexOf(".");
  if (dot < 1) return false;
  const token = value.slice(0, dot);
  const signature = value.slice(dot + 1);
  // The shape better-call itself insists on before verifying.
  if (signature.length !== 44 || !signature.endsWith("=")) return false;
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"],
    );
    const sig = Uint8Array.from(atob(signature), (c) => c.charCodeAt(0));
    return await crypto.subtle.verify(
      "HMAC",
      key,
      sig,
      new TextEncoder().encode(token),
    );
  } catch {
    return false;
  }
}
