/**
 * MCP client for the C3PO bridge.
 *
 * The bridge (`apps/bridge`) is a FastMCP server. Launched with
 * `BRIDGE_TRANSPORT=http` it serves the streamable-http transport at
 * `BRIDGE_URL` (default `http://127.0.0.1:8001/mcp`). This module holds the
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

import { bridgeUrl } from "./url";

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

function getClient(): Promise<Client> {
  if (!clientPromise) {
    clientPromise = connect().catch((err) => {
      clientPromise = null; // let the next call retry a fresh connection
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
  // No per-call timeout on purpose. The SDK's `timeout` option CANCELS the
  // call on the bridge (`notifications/cancelled`), which for a skill that is
  // halfway through moving the robot — or stopping it — is the wrong thing to
  // do. Callers that need to stop waiting race the promise instead (see
  // routes/skills.ts) and let the call finish.
  const client = await getClient();

  let result: Awaited<ReturnType<Client["callTool"]>>;
  try {
    result = await client.callTool({ name, arguments: args });
  } catch (err) {
    clientPromise = null; // connection likely broke — force reconnect next time
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
const LIST_TOOLS_TIMEOUT_MS = 3000;

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
    // Bounded, unlike callTool: listing tools moves nothing, and every
    // non-safety /invoke and the catalogue wait on it. A bridge that accepts
    // connections but never answers must fall through to the cached catalogue
    // in seconds, not after the SDK's 60 s default.
    const result = await client.listTools(undefined, {
      timeout: LIST_TOOLS_TIMEOUT_MS,
    });
    return result.tools.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
      _meta: (t as { _meta?: unknown })._meta,
    }));
  } catch (err) {
    clientPromise = null; // connection likely broke — force reconnect next time
    throw new BridgeUnavailableError(err);
  }
}
