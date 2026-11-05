/**
 * ADMIN_EMAILS promotes only accounts that prove they own the address. Before,
 * whoever registered an allowlisted email first was made admin at the next
 * boot, verified or not.
 */

import { describe, expect, test } from "bun:test";
import { planAdminGrants } from "./admin-bootstrap";

const row = (
  email: string,
  emailVerified: boolean,
  role: string | null = null,
) => ({ id: `id_${email}`, email, role, emailVerified });

describe("planAdminGrants", () => {
  test("a verified account on the list is promoted", () => {
    const plan = planAdminGrants(["op@x.com"], [row("op@x.com", true)]);
    expect(plan.promote.map((r) => r.email)).toEqual(["op@x.com"]);
    expect(plan.unverified).toEqual([]);
  });

  test("an unverified account on the list is named, not promoted", () => {
    const plan = planAdminGrants(["op@x.com"], [row("op@x.com", false)]);
    expect(plan.promote).toEqual([]);
    expect(plan.unverified).toEqual(["op@x.com"]);
  });

  test("an existing admin is left alone, verified or not", () => {
    const plan = planAdminGrants(
      ["a@x.com", "b@x.com"],
      [row("a@x.com", false, "admin"), row("b@x.com", true, "admin")],
    );
    expect(plan.promote).toEqual([]);
    expect(plan.unverified).toEqual([]);
  });

  test("an address with no account is reported missing, case-insensitively", () => {
    const plan = planAdminGrants(
      ["op@x.com", "nobody@x.com"],
      [row("Op@X.com", true)],
    );
    expect(plan.missing).toEqual(["nobody@x.com"]);
    expect(plan.promote.map((r) => r.email)).toEqual(["Op@X.com"]);
  });
});
