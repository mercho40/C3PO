/**
 * The environment contract: what the code reads, and what `.env.example` says.
 *
 * WHY THIS MATTERS MORE THAN IT LOOKS
 *
 * `.env` is gitignored, so `.env.example` is the ONLY description of this
 * service's configuration that a fresh clone, a new person, or a CI runner
 * ever sees. As of 2026-08-27 CI seeds its `.env` directly from it (see
 * `.github/workflows/ci.yml`) — a deliberate choice, because a list of
 * variables written into the workflow would be a third place that has to agree
 * with `env.ts` and `.env.example`, and the last time two places had to agree
 * about configuration in this project one of them moved and the headset lost
 * its camera for a day.
 *
 * That choice puts weight on `.env.example` being complete. A required
 * variable added to `env.ts` and not documented there does not fail with
 * "you forgot to document a variable" — it fails as `bun test` refusing to
 * load any suite that transitively imports this module, which is what CI's
 * apps/back step looked like for its entire life before it was fixed.
 *
 * Parsed as TEXT, never imported: importing `env.ts` executes its `throw`, so
 * a test that imported it could only ever run in an environment that already
 * satisfies the thing being tested.
 */

import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// `fileURLToPath`, not `.pathname`: this repository lives under a directory
// with spaces in its name, which a URL percent-encodes and `readFileSync`
// then cannot open.
const BACK = fileURLToPath(new URL("../..", import.meta.url));
const ENV_TS = join(BACK, "src/lib/env.ts");
const EXAMPLE = join(BACK, ".env.example");

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sourceFiles(full, found);
    else if (/\.ts$/.test(entry) && !/\.test\.ts$/.test(entry))
      found.push(full);
  }
  return found;
}

/** Every `KEY=` declared in an env file. Commented-out lines do not count. */
function declaredKeys(path: string): Set<string> {
  const keys = new Set<string>();
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const match = /^\s*([A-Z][A-Z0-9_]*)\s*=/.exec(line);
    if (match) keys.add(match[1]);
  }
  return keys;
}

/**
 * Source with comments removed.
 *
 * `env.ts`'s own docstring talks ABOUT `process.env.X`, in prose, explaining
 * the bug this whole module exists to prevent. Scanning raw text finds that
 * and reports a variable called `X` — a test failing on the documentation of
 * the thing it is checking. Strings get mangled by this too (`http://` loses
 * its tail), which does not matter: the result is only ever fed to a
 * `process.env.NAME` matcher.
 */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

/** Every `process.env.NAME` read anywhere under src/, with its file. */
function envReads(): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const file of sourceFiles(join(BACK, "src"))) {
    const text = stripComments(readFileSync(file, "utf8"));
    for (const match of text.matchAll(/process\.env\.([A-Z][A-Z0-9_]*)/g)) {
      const name = match[1];
      const where = out.get(name) ?? [];
      where.push(file.slice(BACK.length));
      out.set(name, where);
    }
  }
  return out;
}

describe("apps/back environment contract", () => {
  test("the parser actually finds the reads it is checking", () => {
    // A regex that silently matches nothing makes every assertion below pass
    // while checking zero variables — the failure mode of any test built on
    // grepping source.
    const reads = envReads();
    expect(reads.size).toBeGreaterThan(0);
    expect(reads.has("DATABASE_URL")).toBe(true);
  });

  test("every variable the code reads is documented in .env.example", () => {
    const documented = declaredKeys(EXAMPLE);
    const missing: string[] = [];
    for (const [name, files] of envReads()) {
      if (!documented.has(name)) missing.push(`${name} (read in ${files[0]})`);
    }
    expect(missing).toEqual([]);
  });

  test("every REQUIRED variable is documented too", () => {
    // These are the ones whose absence THROWS at import. If one is missing
    // from `.env.example`, CI seeds an `.env` that cannot boot the service and
    // the error names the variable but not the reason it was never there.
    const source = readFileSync(ENV_TS, "utf8");
    const block = /const REQUIRED = \[([\s\S]*?)\]/.exec(source);
    expect(block).not.toBeNull();
    const required = [...block![1].matchAll(/"([A-Z][A-Z0-9_]*)"/g)].map(
      (m) => m[1],
    );
    expect(required.length).toBeGreaterThan(0);

    const documented = declaredKeys(EXAMPLE);
    expect(required.filter((name) => !documented.has(name))).toEqual([]);
  });

  test("nothing asserts a raw process.env value is present", () => {
    // `process.env.X!` and `process.env.X as string` are the two forms that
    // turn an unset variable into the STRING "undefined" rather than an error
    // — which is how CORS ended up pinned to the origin `"undefined"`, the
    // exact failure this module's docstring was written about.
    //
    // `?? "default"` is fine and is what the remaining direct reads use: a
    // stated fallback is a decision, not an accident.
    const offenders: string[] = [];
    for (const file of sourceFiles(join(BACK, "src"))) {
      if (file === ENV_TS) continue; // where the checking happens
      const text = stripComments(readFileSync(file, "utf8"));
      for (const match of text.matchAll(
        /process\.env\.([A-Z][A-Z0-9_]*)\s*(!|as\s+string)/g,
      )) {
        offenders.push(`${file.slice(BACK.length)}: process.env.${match[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
