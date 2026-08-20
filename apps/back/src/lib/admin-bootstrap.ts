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
 *   list that has never signed up is a no-op until it does, and then the next
 *   boot promotes it.
 * - **Never fatal.** A database that is not up yet must not stop the server
 *   from starting; the next restart reconciles.
 */

import { eq, inArray } from "drizzle-orm";
import { db } from "@back/db/drizzle";
import { user } from "@back/db/schema";
import { env } from "@back/lib/env";

export function adminEmails(): string[] {
  return env.ADMIN_EMAILS.split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);
}

export async function reconcileAdmins(): Promise<string[]> {
  const emails = adminEmails();
  if (emails.length === 0) return [];

  const rows = await db
    .select({ id: user.id, email: user.email, role: user.role })
    .from(user)
    .where(inArray(user.email, emails));

  const promoted: string[] = [];
  for (const row of rows) {
    if (row.role === "admin") continue;
    await db.update(user).set({ role: "admin" }).where(eq(user.id, row.id));
    promoted.push(row.email);
  }

  const missing = emails.filter(
    (email) => !rows.some((row) => row.email.toLowerCase() === email),
  );

  if (promoted.length > 0) {
    console.log(`[admin] promoted to admin: ${promoted.join(", ")}`);
  }
  if (missing.length > 0) {
    // Worth saying out loud: a typo in the allowlist is otherwise completely
    // silent, and presents as "I set ADMIN_EMAILS and it still says I am not
    // an admin" — with nothing anywhere to suggest why.
    console.warn(
      `[admin] ADMIN_EMAILS names ${missing.length} address(es) with no account yet: ` +
        `${missing.join(", ")} — they will be promoted once they sign up and the server restarts.`,
    );
  }
  return promoted;
}
