/**
 * `.env.example` must declare every variable the code statically imports.
 *
 * WHY THIS IS A TEST AND NOT A CONVENTION
 *
 * `$app/env/public` is not a free-form lookup. Since SvelteKit 3 every name it
 * exports is declared in `src/env.ts` (`defineEnvVars`), and the static ones
 * are inlined at build time, so importing a name that is not declared, or not
 * set, is a TYPE ERROR or a build failure — not `undefined` at runtime.
 * (Before SvelteKit 3 the same was true of `$env/static/public`.)
 *
 * `.env` is gitignored. That means the only environment a fresh clone or a CI
 * runner has is whatever `.env.example` describes — and until 2026-08-27 CI had
 * none at all, so `bun run check-types` reported 106 errors across 9 files on
 * every single run and the job had never once been green.
 *
 * CI now seeds `.env` FROM `.env.example` (see `.github/workflows/ci.yml`),
 * which fixes that and creates this obligation: a new `PUBLIC_*` import that
 * nobody adds to `.env.example` breaks the build for everyone who is not the
 * author. The failure surfaces as a svelte-check type error in an unrelated
 * file, which is a long way from "you forgot to document a variable".
 *
 * So: check it here, where the message can say so.
 *
 * Text parsing rather than importing anything, on purpose — importing
 * `$app/env/*` outside a SvelteKit build is exactly the thing that does not
 * work, and this test has to run in plain `bun test`.
 */

import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// `fileURLToPath`, not `.pathname`: this repository lives under a directory
// with spaces in its name ("5th year - TIC"), which a URL percent-encodes and
// `readFileSync` then cannot open.
const ROOT = fileURLToPath(new URL("..", import.meta.url));

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      sourceFiles(full, found);
    } else if (/\.(ts|svelte|js)$/.test(entry)) {
      found.push(full);
    }
  }
  return found;
}

/** The names imported from `$app/env/<which>` anywhere under src/. */
function importedNames(which: "public" | "private"): Map<string, string> {
  // `import { A, B } from "$app/env/public"` — the only form in use, and the
  // one that keeps static variables inlinable (a namespace import would
  // defeat the tree-shaking that makes them static).
  const pattern = new RegExp(
    String.raw`import\s*\{([^}]*)\}\s*from\s*["']\$app/env/${which}["']`,
    "g",
  );
  const out = new Map<string, string>();
  for (const file of sourceFiles(join(ROOT, "src"))) {
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(pattern)) {
      for (const raw of match[1].split(",")) {
        const name = raw
          .trim()
          .split(/\s+as\s+/)[0]
          .trim();
        if (name) out.set(name, file.slice(ROOT.length));
      }
    }
  }
  return out;
}

/** Every `KEY=` declared in an env file, commented-out lines excluded. */
function declaredKeys(path: string): Set<string> {
  const keys = new Set<string>();
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const match = /^\s*([A-Z][A-Z0-9_]*)\s*=/.exec(line);
    if (match) keys.add(match[1]);
  }
  return keys;
}

describe("apps/web env contract", () => {
  const example = declaredKeys(join(ROOT, ".env.example"));

  test("the parser finds the imports it is supposed to be checking", () => {
    // A regex that silently matches nothing would make every assertion below
    // pass while checking exactly zero variables — the failure mode of any
    // test built on grepping source.
    const found = importedNames("public");
    expect(found.size).toBeGreaterThan(0);
    expect(found.has("PUBLIC_API_URL")).toBe(true);
  });

  test("every PUBLIC_* the code imports is declared in .env.example", () => {
    const missing: string[] = [];
    for (const [name, file] of importedNames("public")) {
      if (!example.has(name)) missing.push(`${name} (imported by ${file})`);
    }
    expect(missing).toEqual([]);
  });

  test("every name the code imports is declared in src/env.ts", () => {
    // SvelteKit 3 only exports what `defineEnvVars` declares. Text match on
    // the key, which is how every entry in that file is written.
    const declared = readFileSync(join(ROOT, "src", "env.ts"), "utf8");
    const missing: string[] = [];
    for (const which of ["public", "private"] as const) {
      for (const [name, file] of importedNames(which)) {
        if (!new RegExp(String.raw`^\s*${name}\s*:`, "m").test(declared))
          missing.push(`${name} (imported by ${file})`);
      }
    }
    expect(missing).toEqual([]);
  });

  test("every private env var the code imports is declared too", () => {
    // `hooks.server.ts` imports BETTER_AUTH_SECRET this way. Same build-time
    // resolution, same failure, and it takes the whole server hook with it
    // rather than one page.
    const missing: string[] = [];
    for (const [name, file] of importedNames("private")) {
      if (!example.has(name)) missing.push(`${name} (imported by ${file})`);
    }
    expect(missing).toEqual([]);
  });

  test("PUBLIC_ROBOT_CAM_URL ships SET, not blank", () => {
    // Not style. The console skips the camera entirely when this is empty, and
    // `scripts/quest_setup.sh` now PARSES THE PORT OUT OF IT to decide what to
    // forward to the headset — so a blank value here means no camera in the
    // headset and no forward, which is the exact 2026-08-27 failure arriving
    // by a different route. `.env.example`'s own comment says the same thing.
    const text = readFileSync(join(ROOT, ".env.example"), "utf8");
    const line = text
      .split("\n")
      .find((l) => /^\s*PUBLIC_ROBOT_CAM_URL\s*=/.test(l));
    expect(line).toBeDefined();
    const value = line!.split("=").slice(1).join("=").trim();
    expect(value.length).toBeGreaterThan(0);
    // And it must carry a port, because that is what gets forwarded.
    expect(/:\d+/.test(value)).toBe(true);
  });
});
