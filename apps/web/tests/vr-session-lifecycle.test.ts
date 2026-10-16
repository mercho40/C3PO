/**
 * The window between "start the VR session" and "the operator granted it".
 *
 * `XrTeleopSession.start()` blocks on the browser's WebXR consent prompt, which
 * can sit on screen for seconds. The session object exists for that whole time;
 * the page's handle to it did not.
 *
 * The handle was assigned AFTER `await session.start(...)`, so it was null for
 * the entire prompt, and leaving the page mid-prompt stopped nothing. The
 * session then resolved, and the code after the await armed the control loop
 * — the dead-man, `onSample` → walk_velocity — on a page the operator had
 * already left, with no PARAR button on screen to stop it.
 *
 * The controller (`$lib/robot/vr-control.svelte.ts`) holds the session in
 * `#pendingVr` for that window, `destroy()` stops it, and nothing is adopted
 * into `#vr` until the session is known to be live on a page that still
 * exists. These tests pin those three orderings.
 *
 * ASSERTED ON SOURCE, and it has to be: `start()` needs `navigator.xr`, a WebGL
 * context and a real consent prompt, none of which exist in this harness.
 * `env-contract.test.ts` reads source in the same way and for the same kind of
 * reason. What is pinned here is an ORDERING, which is exactly what source can
 * show and a mock would only re-state.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// `fileURLToPath`, not `.pathname` — this repository lives under a directory
// with spaces in its name, which `.pathname` returns percent-encoded and
// `readFileSync` then cannot open. Same note as env-contract.test.ts.
const controller = readFileSync(
  fileURLToPath(
    new URL("../src/lib/robot/vr-control.svelte.ts", import.meta.url),
  ),
  "utf8",
);

// Line-anchored statements only. Every explanation of this bug contains the
// words `session.start` and `#pendingVr`, so a substring search would match the
// prose that describes the fix rather than the code that is the fix.
const statement = (source: string, pattern: string, from = 0): number => {
  const i = source
    .slice(from)
    .search(new RegExp(String.raw`^\s*${pattern}`, "m"));
  return i < 0 ? -1 : from + i;
};

const awaited = statement(controller, String.raw`await session\.start\(`);

describe("the VR session handle during the consent prompt", () => {
  test("the controller holds the session BEFORE awaiting start()", () => {
    const held = statement(controller, String.raw`this\.#pendingVr = session;`);
    expect(held).toBeGreaterThan(-1);
    expect(awaited).toBeGreaterThan(-1);
    expect(held).toBeLessThan(awaited);
  });

  test("destroy() stops a session that is still on the consent prompt", () => {
    const destroy = statement(controller, String.raw`destroy\(\): void \{`);
    expect(destroy).toBeGreaterThan(-1);
    const stop = statement(
      controller,
      String.raw`this\.#pendingVr\?\.stop\(\);`,
      destroy,
    );
    expect(stop).toBeGreaterThan(destroy);
  });

  test("an abandoned or failed start is never adopted", () => {
    // The abandon guard ENDS the session and returns — it does not throw — so
    // a start stopped mid-flight resolves looking like a success. The gate
    // must sit between the await and the adoption, and must return.
    const gate = statement(
      controller,
      String.raw`if \(this\.#destroyed \|\| !session\.active\) \{`,
      awaited,
    );
    const adopted = statement(controller, String.raw`this\.#vr = session;`);
    expect(gate).toBeGreaterThan(awaited);
    expect(adopted).toBeGreaterThan(gate);
    expect(controller.slice(gate, adopted)).toContain("return;");

    // A start that THROWS returns from its catch, before the adoption too.
    const catchIndex = controller.indexOf("} catch (err) {", awaited);
    expect(catchIndex).toBeGreaterThan(awaited);
    expect(catchIndex).toBeLessThan(adopted);
    expect(controller.slice(catchIndex, gate)).toContain("return;");
  });
});
