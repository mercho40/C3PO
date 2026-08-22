import { describe, expect, test } from "bun:test";

import { DEFAULT_BRIDGE_URL, bridgeSiblingUrl, bridgeUrl } from "./url";

describe("bridge URL configuration", () => {
  test("defaults to the robot and local HTTP port", () => {
    expect(DEFAULT_BRIDGE_URL).toBe("http://127.0.0.1:8001/mcp");
    expect(bridgeUrl("")).toBe(DEFAULT_BRIDGE_URL);
  });

  test("honours an explicit endpoint", () => {
    expect(bridgeUrl("http://bridge.test:9000/mcp")).toBe(
      "http://bridge.test:9000/mcp",
    );
  });

  test("derives sibling telemetry routes without carrying MCP query state", () => {
    expect(
      bridgeSiblingUrl(
        "/telemetry/voice",
        "http://bridge.test:9000/mcp?session=stale",
      ),
    ).toBe("http://bridge.test:9000/telemetry/voice");
  });
});
