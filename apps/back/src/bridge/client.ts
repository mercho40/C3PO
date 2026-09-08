/**
 * MCP client for the C3PO bridge.
 *
 * The bridge (`apps/bridge`) is a FastMCP server. Launched with
 * `BRIDGE_TRANSPORT=http` it serves the streamable-http transport at
 * `BRIDGE_URL` (see ./url for the default and why it has only one). This
 * module holds the
 * single MCP session the backend reuses across requests, turning the bridge's
 * ~20 tools (`get_state`, `walk_to`, `say`, …) into callable functions for the
 * route layer.
 *
 * The session is established lazily on first use and reused. If the bridge is
 * down or the connection drops, the session is discarded so the next call
 * reconnects from scratch.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { bridgeUrl, unreachableHint } from "./url";

// Read once at module load, as before — `client.test.ts` sets the env var and
// then dynamically imports this module to aim it at a dead port. The default
// itself now lives in ./url, which is the only copy of it.
const BRIDGE_URL = bridgeUrl();

/** The bridge could not be reached / the session could not be established. */
export class BridgeUnavailableError extends Error {
  constructor(cause?: unknown) {
    super("bridge_unavailable");
    this.name = "BridgeUnavailableError";
    this.cause = cause;
  }
}

/** A tool ran but reported failure (MCP `isError`). */
export class BridgeToolError extends Error {
  constructor(
    readonly tool: string,
    readonly detail: string,
  ) {
    super(`tool_error: ${tool}`);
    this.name = "BridgeToolError";
  }
}

let clientPromise: Promise<Client> | null = null;

async function connect(): Promise<Client> {
  const client = new Client({ name: "c3po-back", version: "1.0.0" });
  const transport = new StreamableHTTPClientTransport(new URL(BRIDGE_URL));
  await client.connect(transport);
  return client;
}

// The tunnel hint is printed ONCE per process, not per failed call. A console
// polling telemetry against a down bridge would otherwise write this line
// several times a second and bury everything else in the log.
let hintPrinted = false;

/**
 * Drop a session we are done with, without letting the teardown throw.
 *
 * NULLING `clientPromise` ALONE IS NOT A RECONNECT. It stops the next call
 * reusing this session, but the `Client` and its `StreamableHTTPClientTransport`
 * are still open — so the bridge keeps the session registered and this process
 * keeps the socket.
 *
 * That distinction only shows up on a TIMEOUT, which is why it survived: on a
 * refused or dropped connection the SDK's own `_onclose` already tears the
 * transport down, and that is the case `client.test.ts` covers. On a timeout it
 * does not — `Protocol`'s timeout path sends `notifications/cancelled` and
 * rejects the caller's promise, and only `_onclose` clears `_transport`
 * (verified in the installed SDK, dist/esm/shared/protocol.js). So a bridge
 * that goes slow rather than away — a stalled tunnel, a robot skill that runs
 * past the 60 s default — leaked one session and one socket per occurrence,
 * while every log line said "reconnecting".
 *
 * Best-effort by construction: this runs on a path that is already failing, and
 * a close() that rejects must not replace the caller's BridgeUnavailableError
 * with something less useful.
 */
function discard(client: Client): void {
  void Promise.resolve()
    .then(() => client.close())
    .catch(() => {});
}

function getClient(): Promise<Client> {
  if (!clientPromise) {
    clientPromise = connect().catch((err) => {
      clientPromise = null; // let the next call retry a fresh connection
      if (!hintPrinted) {
        const hint = unreachableHint(BRIDGE_URL);
        if (hint) {
          hintPrinted = true;
          console.error(`[bridge] ${hint}`);
        }
      }
      throw new BridgeUnavailableError(err);
    });
  }
  return clientPromise;
}

function textOf(content: unknown): string | undefined {
  if (!Array.isArray(content)) return undefined;
  const item = content.find(
    (c): c is { type: "text"; text: string } =>
      typeof c === "object" &&
      c !== null &&
      (c as { type?: unknown }).type === "text",
  );
  return item?.text;
}

/**
 * Invoke a bridge tool by name and return its decoded result.
 *
 * FastMCP returns the tool's dict as `structuredContent`; we fall back to
 * parsing the text content otherwise. Throws {@link BridgeUnavailableError} if
 * the bridge is unreachable, or {@link BridgeToolError} if the tool fails.
 */
export async function callTool(
  name: string,
  args: Record<string, unknown> = {},
): Promise<unknown> {
  const client = await getClient();

  let result: Awaited<ReturnType<Client["callTool"]>>;
  try {
    result = await client.callTool({ name, arguments: args });
  } catch (err) {
    clientPromise = null; // let the next call build a fresh session
    discard(client); // ...and actually close this one — see `discard`
    throw new BridgeUnavailableError(err);
  }

  if (result.isError) {
    throw new BridgeToolError(name, textOf(result.content) ?? "unknown error");
  }

  if (result.structuredContent !== undefined) return result.structuredContent;

  const text = textOf(result.content);
  if (text !== undefined) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return result.content ?? null;
}

/**
 * List the bridge's tools, with their JSON Schema and `_meta`.
 *
 * This is now how `apps/back` learns what the robot can do. It used to be a
 * hand-written TypeScript catalogue that duplicated the bridge's — and the two
 * drifted, repeatedly and silently, in ways that reached the LLM: a `voice`
 * parameter the bridge did not accept, defaulted parameters marked required so
 * the model had to invent timeouts, and a description telling the agent speech
 * was a stub after it had been implemented.
 */
export async function listTools(): Promise<
  Array<{
    name: string;
    description?: string;
    inputSchema: unknown;
    _meta?: unknown;
  }>
> {
  const client = await getClient();
  try {
    const result = await client.listTools();
    return result.tools.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
      _meta: (t as { _meta?: unknown })._meta,
    }));
  } catch (err) {
    clientPromise = null; // let the next call build a fresh session
    discard(client); // ...and actually close this one — see `discard`
    throw new BridgeUnavailableError(err);
  }
}
