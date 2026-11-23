/**
 * apps/back against the bridge's contract, `contracts/` at the repo root.
 *
 * back names bridge tools as string literals — `callTool("get_state")`, the
 * voice loop's `stop_everything`, the agent's exclusion lists — and restates
 * `get_state`'s payload as `RobotState`. None of that can import Python, so a
 * renamed tool or field used to surface on the robot: a 502 `bridge_error`, a
 * stop that never dispatched, a dashboard reading `undefined`.
 *
 * The bridge writes its side to contracts/ (apps/bridge/scripts/export_contracts.py)
 * and its pytest fails while those files are stale. This holds back to them:
 *
 *   - every tool name back's source dispatches or lists exists in the manifest
 *   - SAFETY_SKILLS is the manifest's safety set (the admin-gate bypass)
 *   - `RobotState` matches the bridge's RobotState (at compile time, below)
 *
 * Structural on purpose, like camera-contract.test.ts: reads source text, so it
 * needs no bridge, database or network and imports nothing with side effects.
 */
import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import type {
  Fits,
  NoUnknownKeys,
  UnknownKeys,
} from "../../../contracts/conforms";
import type { RobotState as BridgeRobotState } from "../../../contracts/bridge-payloads";
import type { RobotState } from "./routes/state";

const SRC = import.meta.dir;
const MANIFEST = JSON.parse(
  readFileSync(
    join(SRC, "..", "..", "..", "contracts", "bridge-tools.json"),
    "utf8",
  ),
) as {
  tools: Record<string, { classification: string; tier: string }>;
  safety_tools: string[];
};
const TOOLS = new Set(Object.keys(MANIFEST.tools));

function sources(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, found);
    else if (entry.endsWith(".ts") && !entry.endsWith(".test.ts"))
      found.push(full);
  }
  return found;
}
const FILES = sources(SRC).map((f) => ({
  file: relative(SRC, f),
  text: readFileSync(f, "utf8"),
}));
const text = (file: string) => FILES.find((f) => f.file === file)!.text;

/** Every `"literal"` inside the first match of `block` in `file`. */
function listed(file: string, block: RegExp): string[] {
  const m = text(file).match(block);
  if (!m)
    throw new Error(`${file}: ${block} no longer matches; update this test`);
  return [...m[1]!.matchAll(/"([a-z_]+)"/g)].map((x) => x[1]!);
}

/** Calls that name a bridge tool directly, anywhere in src. */
const CALLS: Record<string, RegExp> = {
  callTool: /\bcallTool\(\s*"([a-z_]+)"/g,
  auditedCall: /\bauditedCall\(\s*"([a-z_]+)"/g,
  // `name === "listen"`, `skill.name !== "cancel_navigation"`; not `typeof x.name === "string"`.
  "name ===":
    /(?<!typeof\s+(?:\w+\.)?)\b(?:\w+\.)?name\s*[!=]==\s*"([a-z_]+)"/g,
};

/** Lists of tool names, by where they live. */
const LISTS: Record<string, [file: string, block: RegExp]> = {
  SAFETY_SKILLS: ["skills/invoke.ts", /SAFETY_SKILLS = new Set\(\[([^\]]*)\]/],
  "agentToolExclusions (always)": [
    "agent/policy.ts",
    /const always = \[([^\]]*)\]/,
  ],
  "agentToolExclusions (voice)": [
    "agent/policy.ts",
    /\[\.\.\.always,([^\]]*)\]/,
  ],
  ALWAYS_OFFERED: [
    "agent/policy.ts",
    /ALWAYS_OFFERED[^=]*= new Set\(\[([^\]]*)\]/,
  ],
};

describe("every bridge tool back names is in the manifest", () => {
  for (const [kind, re] of Object.entries(CALLS)) {
    test(kind, () => {
      const named = FILES.flatMap(({ file, text }) =>
        [...text.matchAll(re)].map((m) => ({ file, tool: m[1]! })),
      );
      expect(named.length).toBeGreaterThan(0); // the pattern still finds the calls
      const missing = named.filter(({ tool }) => !TOOLS.has(tool));
      expect(missing).toEqual([]);
    });
  }
  for (const [name, [file, block]] of Object.entries(LISTS)) {
    test(name, () => {
      const tools = listed(file, block);
      expect(tools.length).toBeGreaterThan(0);
      expect(tools.filter((t) => !TOOLS.has(t))).toEqual([]);
    });
  }
});

test("SAFETY_SKILLS is the bridge's safety set", () => {
  // classification "safety" is what lets a non-admin invoke a skill directly
  // (skills/invoke.ts), and SAFETY_SKILLS is what keeps the e-stop invocable
  // when the catalogue cannot be read. They must be the same tools.
  const [file, block] = LISTS.SAFETY_SKILLS!;
  expect(listed(file, block).sort()).toEqual([...MANIFEST.safety_tools].sort());
});

// `RobotState` (routes/state.ts) against the bridge's, checked by `tsc`
// (bun run check-types), not by `bun test`: a field renamed, retyped or made
// optional on the bridge fails the type check here, naming it. Both directions
// of UnknownKeys because back's type claims to be the whole payload.
export type RobotStateFits = Fits<RobotState, BridgeRobotState>;
export type RobotStateNamesNothingExtra = NoUnknownKeys<
  UnknownKeys<RobotState, BridgeRobotState>
>;
export type RobotStateMissesNothing = NoUnknownKeys<
  UnknownKeys<BridgeRobotState, RobotState>
>;
