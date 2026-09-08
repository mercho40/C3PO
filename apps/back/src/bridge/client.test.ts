/**
 * Tests for the bridge MCP client's reconnect-on-failure logic
 * (`getClient`/`callTool` in ./client) -- had zero coverage despite being
 * the sole channel to the robot, including the panic-button stop_everything
 * call.
 *
 * BRIDGE_URL is overridden to an unused local port so this test never depends
 * on whether a real bridge happens to be running -- the connection failure is
 * real (a genuine refused TCP connect), not mocked, just aimed at a port
 * nothing listens on.
 *
 * It is set in `beforeEach`, NOT once before a dynamic import. The module used
 * to read the address once at load time, so this test only worked if it was the
 * first thing to import `./client` — and it is not. `catalogue.test.ts` and
 * `skills.test.ts` both pull it in transitively, and bun loaded them first in
 * CI: the address was already fixed to the one from the seeded `.env`, the
 * override arrived too late, and the test failed there while passing locally,
 * where the file order happened to differ. `client.ts` now resolves the URL per
 * connect, which removes the ordering dependency from both sides.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { BridgeUnavailableError, callTool } from "./client";

const DEAD_PORT = "http://127.0.0.1:39217/mcp";

// Per test, so it holds no matter which file imported `./client` first.
beforeEach(() => {
  process.env.BRIDGE_URL = DEAD_PORT;
});

describe("callTool", () => {
  test("throws BridgeUnavailableError when the bridge is unreachable", async () => {
    await expect(callTool("get_state", {})).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
  });

  test("a failed connection doesn't wedge the client -- the next call retries rather than hanging on a cached rejection", async () => {
    await expect(callTool("get_state", {})).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
    // If `clientPromise` weren't reset to null after the first failure,
    // this second call would reuse the same rejected promise. It still
    // rejects the same way here (nothing is listening either way), but the
    // point is it completes promptly via a fresh connect() attempt rather
    // than any stuck/cached state -- proven by both calls resolving well
    // under the test timeout.
    await expect(callTool("get_state", {})).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
  });
});

describe("a discarded session is actually closed", () => {
  /**
   * Nulling `clientPromise` stops the NEXT call reusing the session. It does
   * not close the one we just gave up on, and those are different things.
   *
   * It only matters on a timeout, which is why it went unnoticed: on a refused
   * or dropped connection — the case the tests above cover with a real closed
   * port — the SDK's own `_onclose` tears the transport down for us. On a
   * timeout it does not. `Protocol`'s timeout path sends
   * `notifications/cancelled` and rejects the caller, and only `_onclose`
   * clears `_transport` (checked in the installed SDK,
   * dist/esm/shared/protocol.js). So a bridge that goes SLOW rather than away —
   * a stalled tunnel, a skill running past the 60 s default — leaked one live
   * session on the bridge and one socket here, every time, while the log said
   * "reconnecting".
   *
   * Asserted on the source because the leak is invisible from outside the
   * module: `getClient` is private, and a test cannot hold the abandoned
   * `Client` to ask whether it closed. Same reason `url.test.ts` scans source
   * for the hardcoded address.
   */
  // fileURLToPath, not `.pathname` — this repo lives under a directory with
  // spaces in its name, which `.pathname` hands back percent-encoded.
  const src = readFileSync(
    fileURLToPath(new URL("./client.ts", import.meta.url)),
    "utf8",
  );

  test("every failure that abandons a live session also closes it", () => {
    // STATEMENTS ONLY — anchored to the start of a line so a commented-out or
    // merely-mentioned call cannot satisfy this. The first version counted
    // bare occurrences and passed happily against a `// discard(client);`,
    // which is the same trap `url.test.ts` hit when its comment-stripper ate
    // the `//` inside `http://`.
    const statement = (name: string) =>
      (src.match(new RegExp(String.raw`^\s*${name};`, "gm")) ?? []).length;

    // Three resets exist. The one inside `connect().catch` is exempt and must
    // stay exempt: connect() FAILED, so there is no established session to
    // close, and the SDK already closes the half-built one itself.
    expect(statement(String.raw`clientPromise = null`)).toBe(3);

    // The other two run after `await getClient()` handed us a live client.
    expect(statement(String.raw`discard\(client\)`)).toBe(2);
  });

  test("the close cannot replace the caller's error with its own", () => {
    // `discard` runs on a path that is already failing. If a rejecting close()
    // escaped, it would surface instead of BridgeUnavailableError — turning a
    // diagnosable "the bridge is unreachable" into an unrelated transport
    // error, on the call an operator makes when something is already wrong.
    const body = src.slice(src.indexOf("function discard"));
    expect(body).toContain(".catch(");
    expect(body.slice(0, body.indexOf("}"))).toContain("void ");
  });
});
