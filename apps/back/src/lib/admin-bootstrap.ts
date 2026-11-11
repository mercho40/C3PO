/**
 * Grant `role: "admin"` to the accounts named in `ADMIN_EMAILS`.
 *
 * Better Auth's admin plugin reads `role` off the user row and provides no way
 * to put it there: `signUp` always creates a user with a null role, and the
 * plugin's own `setRole` endpoint is itself admin-gated. So the very first
 * admin cannot be made through the product at all — it has to be a manual
 * `UPDATE "user" SET role = 'admin'` against the database.
 *
 * That is a genuinely bad bootstrap, and it bit exactly the way it always does:
 * the operator signed in on the headset, found every skill greyed out with
 * "Se requiere una cuenta admin", and had no way to fix it from where they
 * were standing. Nothing was broken. There was simply no path from "I have an
 * account" to "I can drive the robot" that did not involve a terminal and a
 * psql prompt.
 *
 * So the allowlist lives in the environment beside the database URL, and is
 * reconciled at boot. Restarting the backend is a thing anyone can already do;
 * writing hand-rolled SQL against a production table at 2am is not.
 *
 * Deliberately narrow:
 *
 * - **Grants only.** It never demotes. Removing an address from the list stops
 *   future grants; it does not take a role away, because an env var that
 *   silently strips permissions when someone reformats a config line is a
 *   worse failure than a stale admin.
 * - **Empty by default.** An unset `ADMIN_EMAILS` grants nothing to nobody,
 *   which is the only safe reading of "no allowlist configured".
 * - **Existing accounts only.** It does not create users. An address in the
 *   list with no account is a no-op until one is made (scripts/create-user.ts;
 *   public sign-up is closed), and then the next boot promotes it.
 * - **Verified addresses only.** Matching on the email alone meant whoever
 *   registered an ADMIN_EMAILS address first got the robot, with nothing
 *   proving they own it. Sign-up is closed now, so no new account can squat an
 *   address, but rows made while it was open still can. Accounts from
 *   create-user.ts are marked verified (a person at the terminal vouched for
 *   them) and OAuth ones carry the provider's verdict. An unverified match is
 *   named in the log and left alone: delete it and recreate it with
 *   create-user.ts.
 * - **Never fatal.** A database that is not up yet must not stop the server
 *   from starting; the next restart reconciles.
 */

import { eq, inArray, sql } from "drizzle-orm";
import { db } from "#back/db/drizzle.js";
import { user } from "#back/db/schema.js";
import { env } from "#back/lib/env.js";

export function adminEmails(): string[] {
  return env.ADMIN_EMAILS.split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);
}

type AccountRow = {
  id: string;
  email: string;
  role: string | null;
  emailVerified: boolean;
};

/**
 * What reconcileAdmins does with the rows that matched the allowlist. Pure, so
 * the rules are testable without a database.
 */
export function planAdminGrants(
  emails: readonly string[],
  rows: readonly AccountRow[],
): { promote: AccountRow[]; unverified: string[]; missing: string[] } {
  const promote = rows.filter(
    (row) => row.role !== "admin" && row.emailVerified,
  );
  const unverified = rows
    .filter((row) => row.role !== "admin" && !row.emailVerified)
    .map((row) => row.email);
  const missing = emails.filter(
    (email) => !rows.some((row) => row.email.toLowerCase() === email),
  );
  return { promote, unverified, missing };
}

export async function reconcileAdmins(): Promise<string[]> {
  const emails = adminEmails();
  if (emails.length === 0) return [];

  // Compared case-insensitively on BOTH sides. `adminEmails()` lowercases the
  // allowlist, but `user.email` is whatever was typed at sign-up — so an
  // account created as "Operador@Gmail.com" would never match a lowercased
  // list, and the only symptom would be the "no account yet" warning about an
  // address that plainly does have one. Silent, and exactly the kind of thing
  // nobody thinks to check at 2am.
  const rows = await db
    .select({
      id: user.id,
      email: user.email,
      role: user.role,
      emailVerified: user.emailVerified,
    })
    .from(user)
    .where(inArray(sql`lower(${user.email})`, emails));

  const { promote, unverified, missing } = planAdminGrants(emails, rows);
  const promoted: string[] = [];
  for (const row of promote) {
    await db.update(user).set({ role: "admin" }).where(eq(user.id, row.id));
    promoted.push(row.email);
  }

  if (promoted.length > 0) {
    console.log(`[admin] promoted to admin: ${promoted.join(", ")}`);
  }
  if (unverified.length > 0) {
    console.warn(
      `[admin] ADMIN_EMAILS matches ${unverified.length} account(s) with an unverified email, not promoted: ` +
        `${unverified.join(", ")} — delete and recreate with apps/back/scripts/create-user.ts --admin.`,
    );
  }
  if (missing.length > 0) {
    // Worth saying out loud: a typo in the allowlist is otherwise completely
    // silent, and presents as "I set ADMIN_EMAILS and it still says I am not
    // an admin" — with nothing anywhere to suggest why.
    console.warn(
      `[admin] ADMIN_EMAILS names ${missing.length} address(es) with no account yet: ` +
        `${missing.join(", ")} — create them with apps/back/scripts/create-user.ts; the next restart promotes them.`,
    );
  }
  return promoted;
}
