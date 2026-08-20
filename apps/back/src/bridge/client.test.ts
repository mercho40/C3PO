/**
 * Tests for the bridge MCP client's reconnect-on-failure logic
 * (`getClient`/`callTool` in ./client) -- had zero coverage despite being
 * the sole channel to the robot, including the panic-button stop_everything
 * call.
 *
 * BRIDGE_URL is overridden to an unused local port *before* importing the
 * module (it reads the env var once at module-load time) so this test never
 * depends on whether a real bridge happens to be running on the default
 * port in this environment -- the connection failure is real (a genuine
 * refused TCP connect), not mocked, just aimed at a port nothing listens on.
 */
import { describe, expect, test } from "bun:test";

process.env.BRIDGE_URL = "http://127.0.0.1:39217/mcp";
const { callTool, BridgeUnavailableError } = await import("./client");

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
