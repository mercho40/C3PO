/**
 * Tests for the bridge MCP client's reconnect-on-failure logic
 * (`getClient`/`callTool` in ./client) -- the sole channel to the robot,
 * including the panic-button stop_everything call.
 *
 * In process, against real sockets: each test makes its own client
 * (`createBridgeClient(url)`) aimed at a port nothing listens on, a socket
 * that never answers, or a fake bridge. These used to run in child processes,
 * because the client was a module singleton other test files replaced with
 * `mock.module`; an instance per test needs neither.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { ErrorCode, McpError } from "@modelcontextprotocol/sdk/types.js";

import {
  BridgeToolError,
  BridgeUnavailableError,
  createBridgeClient,
  failure,
  sessionLost,
} from "./client";

/** A loopback port nothing listens on: a genuine refused TCP connect. */
function deadUrl(): string {
  const probe = Bun.serve({ port: 0, fetch: () => new Response() });
  const port = probe.port;
  probe.stop(true);
  return `http://127.0.0.1:${port}/mcp`;
}

describe("callTool", () => {
  test("throws BridgeUnavailableError when the bridge is unreachable", async () => {
    const { callTool } = createBridgeClient(deadUrl());
    await expect(callTool("get_state", {})).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
  }, 30_000);

  test("a failed connection doesn't wedge the client -- the next call retries rather than hanging on a cached rejection", async () => {
    // If `clientPromise` were not reset after the first failure, the second
    // call would await the same rejected promise instead of attempting a fresh
    // connect. Both still reject here — nothing is listening either way — so
    // what this proves is that the second one gets there promptly and by its
    // own route.
    const { callTool } = createBridgeClient(deadUrl());
    await expect(callTool("get_state", {})).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
    await expect(callTool("get_state", {})).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
  }, 30_000);
});

describe("connecting", () => {
  test("a bridge that accepts the connection but never answers is given up on in seconds", async () => {
    // A stalled tunnel or a wedged bridge: TCP connects, nothing comes back.
    // The connect had no bound of its own, so the SDK's 60 s initialize
    // default applied, and every call meanwhile, PARAR included, waited on
    // that same pending session.
    const silent = Bun.listen({
      hostname: "127.0.0.1",
      port: 0,
      socket: { data() {}, open() {} },
    });
    try {
      const { callTool } = createBridgeClient(
        `http://127.0.0.1:${silent.port}/mcp`,
      );
      const t0 = Date.now();
      await expect(callTool("stop_everything", {})).rejects.toBeInstanceOf(
        BridgeUnavailableError,
      );
      // Under PARAR's own 8 s wait (bridge/stop.ts).
      expect(Date.now() - t0).toBeLessThan(8_000);
    } finally {
      silent.stop(true);
    }
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
    const statement = (pattern: string) =>
      (src.match(new RegExp(String.raw`^\s*${pattern}`, "gm")) ?? []).length;

    // Both live-session failure paths go through `abandon`, and only when the
    // session is actually lost — see the classification tests below.
    expect(
      statement(
        String.raw`if \(sessionLost\(err\)\) abandon\(pending, client\);`,
      ),
    ).toBe(2);
    // `abandon` closes what it gives up on, and clears the shared slot only
    // if it still holds THIS session (a late failure must not drop a newer one).
    const abandonBody = src.slice(src.indexOf("function abandon"));
    const end = abandonBody.indexOf("\n  }\n");
    expect(abandonBody.slice(0, end)).toContain(
      "if (clientPromise === pending) clientPromise = null;",
    );
    expect(abandonBody.slice(0, end)).toContain("discard(client);");
  });

  test("our own abort is told apart before the session is judged lost", () => {
    // The SDK rejects an aborted call with the signal's reason, a plain Error,
    // which sessionLost reads as a dead transport: PARAR in flight would go too.
    const body = src.slice(src.indexOf("async function callTool"));
    const guard = body.indexOf("if (signal?.aborted) throw");
    expect(guard).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(body.indexOf("if (sessionLost(err))"));
  });

  test("a tool call sets its own ceiling instead of inheriting the SDK's 60 s", () => {
    // Omitting `timeout` does not mean "none": the SDK falls back to 60 s and
    // cancels the call on the bridge. walk_to accepts up to 300 s.
    expect(src).toContain("timeout: CALL_TIMEOUT_MS");
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

describe("which failures end the session", () => {
  test("a lost connection does, and reads as the bridge being unavailable", () => {
    const closed = new McpError(
      ErrorCode.ConnectionClosed,
      "Connection closed",
    );
    expect(sessionLost(closed)).toBe(true);
    expect(failure("walk_to", closed)).toBeInstanceOf(BridgeUnavailableError);
    // A transport error (refused, reset, tunnel gone) is not an McpError.
    expect(sessionLost(new TypeError("fetch failed"))).toBe(true);
  });

  test("a timeout does not — other calls, PARAR included, ride the same session", () => {
    const timedOut = new McpError(
      ErrorCode.RequestTimeout,
      "Request timed out",
    );
    expect(sessionLost(timedOut)).toBe(false);
    expect(failure("list_tools", timedOut)).toBeInstanceOf(
      BridgeUnavailableError,
    );
  });

  test("the bridge refusing a request is about that request, not the bridge", () => {
    const refused = new McpError(ErrorCode.InvalidParams, "bad arguments");
    expect(sessionLost(refused)).toBe(false);
    const err = failure("walk_to", refused);
    expect(err).toBeInstanceOf(BridgeToolError);
    expect((err as InstanceType<typeof BridgeToolError>).detail).toContain(
      "bad arguments",
    );
  });
});

describe("a bridge that forgot our session", () => {
  /**
   * After `c3po restart` the bridge answers the old session id with 404
   * before running anything. The first call after it, PARAR included, used to
   * fail as "bridge_unavailable". A fake bridge, in process, forgets every
   * session on demand.
   */
  /** A fake bridge that forgets every session on demand, and a client of it. */
  function fakeBridge() {
    const live = new Set<string>();
    const state = { sessions: 0, always404: false, refused: 0 };
    const ran: string[] = [];
    const server = Bun.serve({
      port: 0,
      async fetch(req) {
        if (req.method !== "POST") return new Response(null, { status: 405 });
        const msg = (await req.json()) as {
          id?: number;
          method: string;
          params?: { name?: string; protocolVersion?: string };
        };
        if (msg.method === "initialize") {
          const sid = `s${++state.sessions}`;
          live.add(sid);
          return Response.json(
            {
              jsonrpc: "2.0",
              id: msg.id,
              result: {
                protocolVersion: msg.params?.protocolVersion,
                capabilities: { tools: {} },
                serverInfo: { name: "fake-bridge", version: "0" },
              },
            },
            { headers: { "mcp-session-id": sid } },
          );
        }
        const sid = req.headers.get("mcp-session-id") ?? "";
        if (!live.has(sid) || (state.always404 && msg.id !== undefined)) {
          state.refused++;
          return Response.json(
            {
              jsonrpc: "2.0",
              id: "server-error",
              error: { code: -32600, message: "Session not found" },
            },
            { status: 404 },
          );
        }
        if (msg.id === undefined) return new Response(null, { status: 202 });
        ran.push(msg.params?.name ?? msg.method);
        const result =
          msg.method === "tools/list"
            ? { tools: [] }
            : { content: [], structuredContent: { ran: msg.params?.name } };
        return Response.json({ jsonrpc: "2.0", id: msg.id, result });
      },
    });
    const client = createBridgeClient(`http://127.0.0.1:${server.port}/mcp`);
    return { ...client, live, state, ran, stop: () => server.stop(true) };
  }

  test("the first call after a restart, PARAR, goes out on a fresh session, once", async () => {
    const b = fakeBridge();
    try {
      await b.callTool("get_state", {});
      b.live.clear(); // c3po restart
      const stop = await b.callTool("stop_everything", {});
      b.live.clear();
      const tools = await b.listTools();
      expect({ stop, tools, ran: b.ran, ...b.state }).toEqual({
        stop: { ran: "stop_everything" },
        tools: [],
        ran: ["get_state", "stop_everything", "tools/list"],
        refused: 2,
        sessions: 3,
        always404: false,
      });
    } finally {
      b.stop();
    }
  }, 30_000);

  test("a bridge that keeps answering 404 is retried once, not forever", async () => {
    const b = fakeBridge();
    try {
      b.state.always404 = true;
      await expect(b.callTool("stop_everything", {})).rejects.toBeInstanceOf(
        BridgeUnavailableError,
      );
      expect({
        ran: b.ran,
        refused: b.state.refused,
        sessions: b.state.sessions,
      }).toEqual({
        ran: [],
        refused: 2,
        sessions: 2,
      });
    } finally {
      b.stop();
    }
  }, 30_000);
});
