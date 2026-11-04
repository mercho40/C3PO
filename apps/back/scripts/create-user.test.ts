/**
 * The create-user CLI's argument and password rules. The database half is a
 * thin wrapper over Better Auth and is checked by hand (SESSION-CHECKLIST §3c).
 */

import { describe, expect, test } from "bun:test";
import {
  MIN_PASSWORD_LENGTH,
  describeError,
  parseArgs,
  passwordProblem,
} from "./create-user";

describe("parseArgs", () => {
  test("email and name create a plain user; the email is lowercased", () => {
    expect(parseArgs(["Op@X.com", "Ana", "Pérez"])).toEqual({
      kind: "create",
      email: "op@x.com",
      name: "Ana Pérez",
      admin: false,
    });
  });

  test("--admin anywhere makes an admin", () => {
    expect(parseArgs(["--admin", "op@x.com", "Ana"])).toMatchObject({
      kind: "create",
      admin: true,
    });
  });

  test("--reset-password and --delete take exactly one email", () => {
    expect(parseArgs(["--reset-password", "operador@c3po.local"])).toEqual({
      kind: "reset-password",
      email: "operador@c3po.local",
    });
    expect(parseArgs(["--delete", "operador@c3po.local"])).toEqual({
      kind: "delete",
      email: "operador@c3po.local",
    });
    expect(parseArgs(["--delete", "a@x.com", "b@x.com"])).toHaveProperty(
      "error",
    );
    expect(parseArgs(["--delete"])).toHaveProperty("error");
  });

  test("bad input is an error, never a guess", () => {
    for (const argv of [
      [],
      ["op@x.com"],
      ["not-an-email", "Ana"],
      ["op@x.com", "Ana", "--admni"],
      ["--delete", "--reset-password", "op@x.com"],
      ["--delete", "--admin", "op@x.com"],
    ]) {
      expect(parseArgs(argv)).toHaveProperty("error");
    }
  });
});

describe("passwordProblem", () => {
  test(`fewer than ${MIN_PASSWORD_LENGTH} characters is refused`, () => {
    expect(passwordProblem("a")).not.toBeNull();
    expect(passwordProblem("x".repeat(MIN_PASSWORD_LENGTH - 1))).not.toBeNull();
    expect(passwordProblem("x".repeat(MIN_PASSWORD_LENGTH))).toBeNull();
  });

  test("leading or trailing whitespace is refused", () => {
    expect(passwordProblem("something-typeable\n")).not.toBeNull();
    expect(passwordProblem(" something-typeable")).not.toBeNull();
    expect(passwordProblem("something typeable")).toBeNull();
  });
});

describe("describeError", () => {
  test("an existing account says what to do instead", () => {
    for (const code of [
      "USER_ALREADY_EXISTS",
      "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL",
    ]) {
      expect(describeError({ body: { code } })).toContain("--reset-password");
    }
  });

  test("other codes and plain errors pass through", () => {
    expect(
      describeError({ body: { code: "INVALID_EMAIL", message: "bad" } }),
    ).toBe("INVALID_EMAIL: bad");
    expect(describeError(new Error("boom"))).toBe("boom");
  });
});
