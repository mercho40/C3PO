/**
 * apps/web against the bridge's contract, `contracts/` at the repo root.
 *
 * The console dispatches bridge tools by name — `.skills({ name: "say" })`,
 * the headset's preset and bring-up lists, the gesture fallback. (It used to
 * restate `/safety/status` too; `SafetyStatus` is now the contract's, through
 * back's response schema.) A tool renamed on the bridge turned a
 * button in a headset into a silent 404, found while wearing it; a gesture
 * fallback that drifted from `g1_protocol.Gesture` offered a tile the robot
 * refuses.
 *
 * The bridge writes its side to contracts/ (apps/bridge/scripts/export_contracts.py)
 * and its pytest fails while those files are stale. This holds the console to
 * them:
 *
 *   - every tool name the source dispatches or lists exists in the manifest
 *   - GESTURE_CATALOGUE_FALLBACK is the manifest's gesture list, exactly
 *   - the danger-level fallback agrees with the catalogue's danger levels
 *   - the /safety/ack codes the console matches are codes something sends
 *   - `SafetyStatus` is still the bridge's body (at compile time, below)
 */
import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import type {
  Fits,
  NoUnknownKeys,
  UnknownKeys,
} from "../../../contracts/conforms";
import type { SafetyStatusBody } from "../../../contracts/bridge-payloads";
import type { SafetyStatus } from "../src/lib/robot/stop-latch";
import {
  BRING_UP_STEPS,
  GESTURE_CATALOGUE_FALLBACK,
  PRESETS,
  SAFE_STEP,
} from "../src/lib/robot/vr-logic";

// `fileURLToPath`, not `.pathname`: see env-contract.test.ts (spaces in the path).
const ROOT = fileURLToPath(new URL("..", import.meta.url));
const REPO = join(ROOT, "..", "..");
const json = (...parts: string[]) =>
  JSON.parse(readFileSync(join(REPO, "contracts", ...parts), "utf8"));

const MANIFEST = json("bridge-tools.json") as {
  tools: Record<string, { danger_level: string }>;
  gestures: string[];
};
const PAYLOADS = json("bridge-payloads.schema.json") as {
  $defs: { SafetyRouteError: { enum: string[] } };
};
const TOOLS = new Set(Object.keys(MANIFEST.tools));

function sources(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, found);
    else if (/\.(ts|svelte)$/.test(entry) && !entry.includes(".test."))
      found.push(full);
  }
  return found;
}
const SRC = join(ROOT, "src");
const FILES = sources(SRC).map((f) => ({
  file: relative(SRC, f),
  text: readFileSync(f, "utf8"),
}));
const text = (file: string) => FILES.find((f) => f.file === file)!.text;

/** Places the source names a bridge tool, anywhere under src. */
const CALLS: Record<string, RegExp> = {
  // Eden: createApi(fetch).skills({ name: "say" }).invoke.post(...)
  ".skills({ name })": /\.skills\(\{\s*name:\s*"([a-z_]+)"/g,
  // vr-control's runSkill(key, label, skill, body): the skill is the third.
  "runSkill(…, skill)": /\brunSkill\(\s*[^,()]+,\s*[^,()]+,\s*"([a-z_]+)"/g,
  // The routes in prose: "POST /skills/walk_velocity/invoke", "GET /skills/say".
  "/skills/<name>":
    /(?:(?:GET|POST)\s+\/skills\/([a-z_]+)|\/skills\/([a-z_]+)\/(?:invoke|confirm|dry-run))/g,
  // `s.name === "gesture"`; not `typeof x.name === "string"`.
  "name ===":
    /(?<!typeof\s+(?:\w+\.)?)\b(?:\w+\.)?name\s*[!=]==\s*"([a-z_]+)"/g,
};

describe("every bridge tool the console names is in the manifest", () => {
  for (const [kind, re] of Object.entries(CALLS)) {
    test(kind, () => {
      const named = FILES.flatMap(({ file, text }) =>
        [...text.matchAll(re)].map((m) => ({ file, tool: (m[1] ?? m[2])! })),
      );
      expect(named.length).toBeGreaterThan(0); // the pattern still finds them
      expect(named.filter(({ tool }) => !TOOLS.has(tool))).toEqual([]);
    });
  }

  test("the headset's presets, bring-up steps and safe step", () => {
    const names = [
      ...PRESETS.map((p) => p.name),
      ...BRING_UP_STEPS.map((s) => s.name),
      SAFE_STEP,
    ];
    expect(names.filter((n) => !TOOLS.has(n))).toEqual([]);
  });
});

test("GESTURE_CATALOGUE_FALLBACK is the bridge's gesture list", () => {
  // It replaces the catalogue when the `gesture` schema carries no enum, so a
  // drifted copy offers a tile the robot refuses, or hides one it accepts.
  expect([...GESTURE_CATALOGUE_FALLBACK].sort()).toEqual(
    [...MANIFEST.gestures].sort(),
  );
});

test("the hold-to-confirm danger fallback agrees with the catalogue", () => {
  // FALLBACK_DANGER (vr-logic.ts) stands in for the catalogue's danger level
  // when GET /skills could not be read. Private, so read from the source.
  const block = text("lib/robot/vr-logic.ts").match(
    /const FALLBACK_DANGER[^=]*=\s*\{([^}]*)\}/,
  );
  expect(block).not.toBeNull();
  const entries = [...block![1]!.matchAll(/(\w+):\s*"(\w+)"/g)].map((m) => [
    m[1]!,
    m[2]!,
  ]);
  expect(entries.length).toBeGreaterThan(0);
  for (const [tool, level] of entries) {
    expect([tool, MANIFEST.tools[tool!]?.danger_level]).toEqual([tool, level]);
  }
});

test("every /safety/ack code the console matches is one something sends", () => {
  // resumeError matches `error` by value. Codes come from the bridge
  // (SafetyRouteError, bridge/safety.py) or from back's relay itself.
  const matched = [
    ...text("lib/robot/stop-latch.ts").matchAll(/code === "([a-z_]+)"/g),
  ].map((m) => m[1]!);
  expect(matched.length).toBeGreaterThan(0);
  const backSafety = readFileSync(
    join(REPO, "apps", "back", "src", "routes", "safety.ts"),
    "utf8",
  );
  const sent = new Set([
    ...PAYLOADS.$defs.SafetyRouteError.enum,
    ...[...backSafety.matchAll(/error: "([a-z_]+)"/g)].map((m) => m[1]!),
  ]);
  expect(matched.filter((c) => !sent.has(c))).toEqual([]);
});

// `SafetyStatus` (stop-latch.ts) against GET /safety/status. It is the
// contract's type now (imported from @repo/back, which derives its response
// schema from contracts/), so this holds by construction — and fails the day
// someone restates it by hand again. Checked by svelte-check, not `bun test`.
export type SafetyStatusFits = Fits<SafetyStatus, SafetyStatusBody>;
export type SafetyStatusNamesNothingExtra = NoUnknownKeys<
  UnknownKeys<SafetyStatus, SafetyStatusBody>
>;
