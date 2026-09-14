/**
 * Tests for the bridge MCP client's reconnect-on-failure logic
 * (`getClient`/`callTool` in ./client) -- had zero coverage despite being
 * the sole channel to the robot, including the panic-button stop_everything
 * call.
 *
 * THE REAL-CONNECT TESTS RUN IN A CHILD PROCESS, AND THEY HAVE TO.
 *
 * `bun test` shares ONE module registry across every file in the run, and
 * `routes/skills.test.ts` calls `mock.module("../bridge/client", ...)` — whose
 * `callTool` resolves. Same resolved file, so whichever file loads later gets
 * the other's version. In CI that mock won, and this file's "the bridge is
 * unreachable" test received a promise that RESOLVED: it was asserting against
 * a stub written for a different file's purposes. Locally the order differed
 * and it passed, which is why it survived.
 *
 * `skills.test.ts` already documents that the registry is shared. The
 * conclusion it did not draw is that a test needing the REAL module cannot get
 * it from inside the same process, at any point in the file, by any ordering.
 * So these two spawn `bun` and assert on its exit code.
 *
 * The value of the original is preserved: the child aims at a port nothing
 * listens on, so the failure is a genuine refused TCP connect rather than a
 * mock of one.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// fileURLToPath, not `.pathname` — this repo lives under a directory with
// spaces in its name, which `.pathname` hands back percent-encoded.
const CLIENT_TS = fileURLToPath(new URL("./client.ts", import.meta.url));
const DEAD_PORT = "http://127.0.0.1:39217/mcp";

/**
 * Run one assertion about the real client in a fresh process.
 *
 * Exit codes rather than stdout parsing: 0 is the expectation met, and every
 * other value is a distinct way of being wrong, so a failure says which.
 */
async function inFreshProcess(body: string): Promise<number> {
  const script = `
    process.env.BRIDGE_URL = ${JSON.stringify(DEAD_PORT)};
    const { callTool, BridgeUnavailableError } = await import(${JSON.stringify(CLIENT_TS)});
    ${body}
  `;
  const proc = Bun.spawn(["bun", "-e", script], {
    stdout: "pipe",
    stderr: "pipe",
    env: { ...process.env, BRIDGE_URL: DEAD_PORT },
  });
  return await proc.exited;
}

describe("callTool", () => {
  test("throws BridgeUnavailableError when the bridge is unreachable", async () => {
    const code = await inFreshProcess(`
      try {
        await callTool("get_state", {});
        process.exit(2);                       // resolved: no refusal happened
      } catch (err) {
        process.exit(err instanceof BridgeUnavailableError ? 0 : 3);
      }
    `);
    expect(code).toBe(0); // 2 = resolved, 3 = wrong error type
  }, 30_000);

  test("a failed connection doesn't wedge the client -- the next call retries rather than hanging on a cached rejection", async () => {
    // If `clientPromise` were not reset after the first failure, the second
    // call would await the same rejected promise instead of attempting a fresh
    // connect. Both still reject here — nothing is listening either way — so
    // what this proves is that the second one gets there promptly and by its
    // own route.
    const code = await inFreshProcess(`
      let first = false;
      try { await callTool("get_state", {}); } catch { first = true; }
      let second = false;
      try { await callTool("get_state", {}); } catch (err) {
        second = err instanceof BridgeUnavailableError;
      }
      process.exit(first && second ? 0 : 4);
    `);
    expect(code).toBe(0); // 4 = one of the two calls did not reject as expected
  }, 30_000);
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
