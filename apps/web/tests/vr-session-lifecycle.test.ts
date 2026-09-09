/**
 * The window between "start the VR session" and "the operator granted it".
 *
 * `XrTeleopSession.start()` blocks on the browser's WebXR consent prompt, which
 * can sit on screen for seconds. The session object exists for that whole time;
 * the page's handle to it did not.
 *
 * `vr = session` was assigned AFTER `await session.start(...)`, so `vr` was null
 * for the entire prompt. `onDestroy` and `exitVr` both reach the session only
 * through `vr`, so navigating away mid-prompt ran `vr?.stop()` against nothing.
 * The session then resolved, and the code after the await armed the control loop
 * — `ensureLoopRunning()`, the dead-man, `onSample` → `sendVelocity` → POST
 * /skills/walk_velocity/invoke — on a page the operator had already left, with
 * no PARAR button on screen to stop it.
 *
 * `XrTeleopSession.stop()` already had an `#abandonOnStart` guard written for
 * exactly this. The bug was that the caller could not reach it.
 *
 * ASSERTED ON SOURCE, and it has to be: `start()` needs `navigator.xr`, a WebGL
 * context and a real consent prompt, none of which exist in this harness, and
 * `+page.svelte` is a component this suite cannot mount. `env-contract.test.ts`
 * reads source in the same way and for the same kind of reason. What is pinned
 * here is an ORDERING, which is exactly what source can show and a mock would
 * only re-state.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// `fileURLToPath`, not `.pathname` — this repository lives under a directory
// with spaces in its name, which `.pathname` returns percent-encoded and
// `readFileSync` then cannot open. Same note as env-contract.test.ts.
const page = readFileSync(
  fileURLToPath(
    new URL(
      "../src/routes/(protected)/vr-control/+page.svelte",
      import.meta.url,
    ),
  ),
  "utf8",
);

// Line-anchored statements only. Every explanation of this bug — including the
// one above — contains the words `vr = session` and `session.start`, so a
// substring search would match the prose that describes the fix rather than the
// code that is the fix.
const statement = (source: string, pattern: string): number =>
  source.search(new RegExp(String.raw`^\s*${pattern}`, "m"));

describe("the VR session handle during the consent prompt", () => {
  test("the page holds the session BEFORE awaiting start()", () => {
    const assigned = statement(page, String.raw`vr = session;`);
    const awaited = statement(page, String.raw`await session\.start\(`);

    expect(assigned).toBeGreaterThan(-1);
    expect(awaited).toBeGreaterThan(-1);
    expect(assigned).toBeLessThan(awaited);
  });

  test("an abandoned start does not fall through into arming motion", () => {
    // The abandon guard ENDS the session and `return`s — it does not throw — so
    // a start that was stopped mid-flight resolves looking like a success.
    // Without a gate, everything after the await runs anyway: vrActive = true,
    // ensureLoopRunning(), and a live dead-man on a destroyed page.
    const awaited = statement(page, String.raw`await session\.start\(`);
    const gate = statement(page, String.raw`if \(!session\.active\)`);

    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeGreaterThan(awaited);

    // And the gate must actually stop, not merely note the fact.
    const after = page.slice(gate, gate + 200);
    expect(after).toContain("return");
  });

  test("a failed start clears the handle it now sets early", () => {
    // `vr` is assigned before the await, so the catch has to undo it — the page
    // must not be left holding a session that never came up.
    const catchIndex = page.indexOf(
      "} catch (err) {",
      statement(page, String.raw`await session\.start\(`),
    );
    expect(catchIndex).toBeGreaterThan(-1);
    expect(page.slice(catchIndex, catchIndex + 400)).toContain("vr = null");
  });
});
