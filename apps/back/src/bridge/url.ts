export const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8001/mcp";

/** Resolve the MCP endpoint without duplicating its deployment default. */
export function bridgeUrl(configured = process.env.BRIDGE_URL): string {
  return configured || DEFAULT_BRIDGE_URL;
}

/** Derive a read-only HTTP endpoint served beside `/mcp` on the bridge. */
export function bridgeSiblingUrl(
  path: string,
  configured = process.env.BRIDGE_URL,
): string {
  const url = new URL(bridgeUrl(configured));
  url.pathname = path;
  url.search = "";
  return url.toString();
}
