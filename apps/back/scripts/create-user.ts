/**
 * Make, re-key or remove a console account from the terminal.
 *
 * Public sign-up is closed (auth.ts: `disableSignUp` on email and on every
 * OAuth provider), so this is how accounts come to exist. Run it from
 * apps/back, so Bun loads apps/back/.env (DATABASE_URL and the auth secrets):
 *
 *   cd apps/back
 *   bun scripts/create-user.ts <email> <name> [--admin]
 *   bun scripts/create-user.ts --reset-password <email>
 *   bun scripts/create-user.ts --delete <email>
 *
 * The password is read from `C3PO_PASSWORD` if set, otherwise from stdin: at a
 * terminal it is asked twice with echo off; piped, the first line is used.
 * Creating goes through Better Auth's own `auth.api.createUser`, which a
 * server-side call without headers may make with no session, and which
 * `disableSignUp` does not block. Nothing needs a restart: `--admin` writes the
 * role directly. Accounts made here are marked email-verified, since a person
 * at the terminal vouched for the address, so ADMIN_EMAILS can promote them.
 *
 * Re-keying and removal use Better Auth's internal adapter (its admin routes
 * want an admin session, which a terminal does not have). Removal deletes the
 * user with its sessions and linked accounts, and cannot be undone.
 */

import { createInterface } from "node:readline";

/** Better Auth's own floor (emailAndPassword.minPasswordLength default). */
export const MIN_PASSWORD_LENGTH = 8;

export const USAGE = [
  "usage:",
  "  bun scripts/create-user.ts <email> <name> [--admin]",
  "  bun scripts/create-user.ts --reset-password <email>",
  "  bun scripts/create-user.ts --delete <email>",
  "Run from apps/back. The password comes from C3PO_PASSWORD or stdin.",
].join("\n");

export type Command =
  | { kind: "create"; email: string; name: string; admin: boolean }
  | { kind: "reset-password"; email: string }
  | { kind: "delete"; email: string };

/** argv after the script path, as a command or a message saying what is wrong. */
export function parseArgs(
  argv: readonly string[],
): Command | { error: string } {
  const flags = argv.filter((arg) => arg.startsWith("--"));
  const words = argv.filter((arg) => !arg.startsWith("--"));
  const unknown = flags.filter(
    (flag) => !["--admin", "--reset-password", "--delete"].includes(flag),
  );
  if (unknown.length > 0) return { error: `unknown option ${unknown[0]}` };

  const reset = flags.includes("--reset-password");
  const remove = flags.includes("--delete");
  const admin = flags.includes("--admin");
  if (reset && remove) {
    return { error: "--reset-password and --delete cannot be combined" };
  }
  if (reset || remove) {
    if (admin)
      return { error: "--admin only applies when creating an account" };
    if (words.length !== 1) return { error: "expected exactly one email" };
    const email = words[0].trim().toLowerCase();
    if (!email.includes("@")) return { error: `not an email: ${words[0]}` };
    return { kind: reset ? "reset-password" : "delete", email };
  }

  if (words.length < 2) return { error: "expected an email and a name" };
  const [rawEmail, ...nameParts] = words;
  const email = rawEmail.trim().toLowerCase();
  const name = nameParts.join(" ").trim();
  if (!email.includes("@")) return { error: `not an email: ${rawEmail}` };
  if (!name) return { error: "the name is empty" };
  return { kind: "create", email, name, admin };
}

/**
 * Why a password is refused, or null. Admin createUser only checks the upper
 * bound, so without this the terminal could make an admin with password "a".
 */
export function passwordProblem(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `the password must be at least ${MIN_PASSWORD_LENGTH} characters`;
  }
  if (password.trim() !== password) {
    return "the password starts or ends with whitespace (a stray newline or space?)";
  }
  return null;
}

/** A Better Auth failure in words a person at a terminal can act on. */
export function describeError(error: unknown): string {
  const body = (error as { body?: { code?: string; message?: string } })?.body;
  const code = body?.code ?? "";
  if (
    code === "USER_ALREADY_EXISTS" ||
    code === "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL"
  ) {
    return "an account with that email already exists (use --reset-password, or --delete and create it again)";
  }
  if (code) return `${code}: ${body?.message ?? ""}`.trim();
  return error instanceof Error ? error.message : String(error);
}

async function readPassword(): Promise<string> {
  const fromEnv = process.env.C3PO_PASSWORD;
  if (fromEnv !== undefined) return fromEnv;

  const tty = Boolean(process.stdin.isTTY);
  const lines = createInterface({ input: process.stdin, terminal: false })[
    Symbol.asyncIterator
  ]();
  const next = async (label: string): Promise<string> => {
    if (tty) process.stderr.write(label);
    const { value, done } = await lines.next();
    if (tty) process.stderr.write("\n");
    if (done) throw new Error("no password on stdin");
    return String(value);
  };

  if (!tty) return next("");
  // Echo off for the duration, and back on whatever happens, including ^C.
  const echo = (on: boolean) =>
    Bun.spawnSync(["stty", on ? "echo" : "-echo"], {
      stdin: "inherit",
      stdout: "ignore",
      stderr: "ignore",
    });
  const restore = () => {
    echo(true);
    process.exit(130);
  };
  process.once("SIGINT", restore);
  echo(false);
  try {
    const first = await next("Password: ");
    const second = await next("Repeat it: ");
    if (first !== second) throw new Error("the two passwords differ");
    return first;
  } finally {
    echo(true);
    process.off("SIGINT", restore);
  }
}

async function run(command: Command): Promise<string> {
  // Imported here, not at the top: auth.ts validates the environment and opens
  // the database, which the tests of the helpers above must not need.
  const { auth } = await import("#back/lib/auth.js");
  const ctx = await auth.$context;
  const found = await ctx.internalAdapter.findUserByEmail(command.email);

  if (command.kind === "delete") {
    if (!found) throw new Error(`no account for ${command.email}`);
    await ctx.internalAdapter.deleteUser(found.user.id);
    return `deleted ${command.email} (its sessions and linked accounts too)`;
  }

  if (command.kind === "create" && found) {
    throw new Error(describeError({ body: { code: "USER_ALREADY_EXISTS" } }));
  }
  if (command.kind === "reset-password" && !found) {
    throw new Error(`no account for ${command.email}`);
  }

  const password = await readPassword();
  const problem = passwordProblem(password);
  if (problem) throw new Error(problem);

  if (command.kind === "reset-password" && found) {
    const userId = found.user.id;
    const hash = await ctx.password.hash(password);
    if (await ctx.internalAdapter.findCredentialAccount(userId)) {
      await ctx.internalAdapter.updatePassword(userId, hash);
    } else {
      // An OAuth-only account: give it a password login as well.
      await ctx.internalAdapter.createAccount({
        userId,
        providerId: "credential",
        accountId: userId,
        password: hash,
      });
    }
    // A rotated password should not leave the old holder signed in.
    await ctx.internalAdapter.deleteUserSessions(userId);
    return `password changed for ${command.email}; its sessions were signed out`;
  }

  if (command.kind !== "create") throw new Error("unreachable");
  const { user } = await auth.api.createUser({
    body: {
      email: command.email,
      password,
      name: command.name,
      role: command.admin ? "admin" : "user",
      data: { emailVerified: true },
    },
  });
  return `created ${user.email} (${command.admin ? "admin" : "user"}); it can log in now`;
}

if (import.meta.main) {
  const command = parseArgs(process.argv.slice(2));
  if ("error" in command) {
    console.error(`${command.error}\n\n${USAGE}`);
    process.exit(2);
  }
  try {
    console.log(await run(command));
    // The database pool would otherwise hold the process open until it idles.
    process.exit(0);
  } catch (error) {
    console.error(`create-user: ${describeError(error)}`);
    process.exit(1);
  }
}
