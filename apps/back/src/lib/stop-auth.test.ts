import { describe, expect, test } from "bun:test";

import { hasSignedSessionCookie } from "./stop-auth";

const SECRET = "test-secret-at-least-32-characters-long";

/** The exact signing better-call does (`signCookieValue`). */
async function signed(token: string, secret = SECRET): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(token),
  );
  const b64 = btoa(String.fromCharCode(...new Uint8Array(sig)));
  return encodeURIComponent(`${token}.${b64}`);
}

describe("hasSignedSessionCookie", () => {
  test("accepts a cookie this server signed", async () => {
    const header = `foo=bar; better-auth.session_token=${await signed("abc123")}`;
    expect(await hasSignedSessionCookie(header, SECRET)).toBe(true);
  });

  test("accepts the __Secure- name used over HTTPS", async () => {
    const header = `__Secure-better-auth.session_token=${await signed("abc")}`;
    expect(await hasSignedSessionCookie(header, SECRET)).toBe(true);
  });

  test("refuses one signed with another secret", async () => {
    const header = `better-auth.session_token=${await signed("abc", "another-secret-of-sufficient-length!!")}`;
    expect(await hasSignedSessionCookie(header, SECRET)).toBe(false);
  });

  test("refuses a token whose signature was kept but the token changed", async () => {
    const good = decodeURIComponent(await signed("abc"));
    const forged = `zzz${good.slice(good.indexOf("."))}`;
    const header = `better-auth.session_token=${encodeURIComponent(forged)}`;
    expect(await hasSignedSessionCookie(header, SECRET)).toBe(false);
  });

  test("refuses no cookie, garbage, and an empty secret", async () => {
    expect(await hasSignedSessionCookie(null, SECRET)).toBe(false);
    expect(
      await hasSignedSessionCookie("better-auth.session_token=nope", SECRET),
    ).toBe(false);
    const header = `better-auth.session_token=${await signed("abc")}`;
    expect(await hasSignedSessionCookie(header, "")).toBe(false);
  });
});
