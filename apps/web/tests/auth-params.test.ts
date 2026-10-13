/**
 * The sign-in pages' URL contract.
 *
 * `redirectTo` is attacker-controlled (anyone can send a /login link), so the
 * open-redirect cases are pinned; the provider list decides whether a button
 * that leads to a "missing client_id" page is drawn at all.
 */

import { describe, expect, test } from "bun:test";
import {
  DEFAULT_REDIRECT,
  oauthErrorMessage,
  readProviders,
  safeRedirectTo,
} from "../src/routes/login/auth-params";

describe("safeRedirectTo", () => {
  test("same-origin paths survive, with their query", () => {
    expect(safeRedirectTo("/vr-control")).toBe("/vr-control");
    expect(safeRedirectTo("/chat?id=abc")).toBe("/chat?id=abc");
  });

  test("missing or empty falls back to the dashboard", () => {
    expect(safeRedirectTo(null)).toBe(DEFAULT_REDIRECT);
    expect(safeRedirectTo(undefined)).toBe(DEFAULT_REDIRECT);
    expect(safeRedirectTo("")).toBe(DEFAULT_REDIRECT);
  });

  test("anything that can leave the origin is refused", () => {
    for (const evil of [
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "\\\\evil.example",
      "javascript:alert(1)",
      "dashboard",
      "/\tevil",
      "/%0a",
    ]) {
      const out = safeRedirectTo(evil);
      // "/%0a" is an encoded path — harmless, it stays on this origin.
      if (evil === "/%0a") expect(out).toBe("/%0a");
      else expect(out).toBe(DEFAULT_REDIRECT);
    }
  });

  test("non-ASCII and spaces are refused — they cannot go in a Location header", () => {
    // `?redirectTo=/%E6%97%A5` arrives here decoded; `Location: /日` threw
    // "Cannot convert argument to a ByteString" → a 500 for a signed-in user.
    for (const raw of ["/日", "/chat?id=é", "/ok path", "/x y", "/ "]) {
      expect(safeRedirectTo(raw)).toBe(DEFAULT_REDIRECT);
    }
    // The same destination, still percent-encoded, is fine.
    expect(safeRedirectTo("/%E6%97%A5")).toBe("/%E6%97%A5");
    // Whatever survives must be a valid header value.
    for (const raw of [
      "/vr-control",
      "/chat?id=abc&x=%20",
      "/日",
      "/a\u007fb",
    ]) {
      expect(
        () => new Headers({ location: safeRedirectTo(raw) }),
      ).not.toThrow();
    }
  });

  test("the auth pages themselves would loop", () => {
    expect(safeRedirectTo("/login")).toBe(DEFAULT_REDIRECT);
    expect(safeRedirectTo("/login?reason=expired")).toBe(DEFAULT_REDIRECT);
    expect(safeRedirectTo("/signup")).toBe(DEFAULT_REDIRECT);
  });
});

describe("readProviders", () => {
  test("only known providers from /health", () => {
    expect(readProviders({ auth: { providers: ["github"] } })).toEqual([
      "github",
    ]);
    expect(
      readProviders({ auth: { providers: ["google", "myspace", 3] } }),
    ).toEqual(["google"]);
  });

  test("malformed or missing means no buttons", () => {
    expect(readProviders(null)).toEqual([]);
    expect(readProviders({ status: "ok" })).toEqual([]);
    expect(readProviders({ auth: { providers: "github" } })).toEqual([]);
  });
});

describe("oauthErrorMessage", () => {
  test("no code, no message", () => {
    expect(oauthErrorMessage(null)).toBeNull();
    expect(oauthErrorMessage("")).toBeNull();
  });

  test("known and unknown codes are Spanish", () => {
    expect(oauthErrorMessage("access_denied")).toContain("Cancelaste");
    expect(oauthErrorMessage("something_new")).toContain("No se pudo ingresar");
  });
});
