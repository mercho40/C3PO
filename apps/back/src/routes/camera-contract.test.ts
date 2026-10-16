/**
 * The camera paths line up across all THREE hops that serve them.
 *
 * Same reason as `telemetry.test.ts`, one hop longer. The picture goes
 * browser -> apps/back -> bridge, and each hop names the path itself:
 *
 *   apps/web     `endpoint(base, "status")`, `endpoint(base, "stream.mjpg")`
 *                with base = PUBLIC_ROBOT_CAM_URL (`http://localhost:3000/camera`)
 *   apps/back    `.get("/camera/…")` exposed, `bridgeSiblingUrl("/camera/…")` asked
 *   apps/bridge  `@mcp.custom_route("/camera/…")`
 *
 * When any two drift the headset is black and every symptom points at the
 * robot: back answers 502 `bridge_error`, exactly what it answers when the
 * bridge is off. On 2026-08-27 a black headset cost a session for a cause of
 * this shape (the port moved and one list did not).
 *
 * Structural on purpose: reads the three sources, needs no bridge, database
 * or network.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const APPS = join(import.meta.dir, "..", "..", "..");
const read = (...parts: string[]) => readFileSync(join(APPS, ...parts), "utf8");

const CAMERA_TS = read("back", "src", "routes", "camera.ts");
const MCP_SERVER_PY = read("bridge", "src", "bridge", "mcp_server.py");
const MJPEG_TS = read("web", "src", "lib", "robot", "mjpeg-camera.ts");
const LIVE_CAMERA = read(
  "web",
  "src",
  "routes",
  "(protected)",
  "live-camera",
  "+page.svelte",
);
const WEB_ENV = read("web", ".env.example");

const all = (src: string, re: RegExp) =>
  [...src.matchAll(re)].map((m) => m[1]!);

/** What back asks the bridge for. */
const proxied = [
  ...all(CAMERA_TS, /bridgeSiblingUrl\(\s*"([^"]+)"/g),
  ...all(CAMERA_TS, /proxyOnce\(\s*"([^"]+)"/g),
];
/** What back exposes to the browser. */
const exposed = all(CAMERA_TS, /\.get\(\s*"(\/camera\/[^"]+)"/g);
/** What the bridge serves. */
const served = all(MCP_SERVER_PY, /@mcp\.custom_route\(\s*"([^"]+)"/g);
/** The leaf names the browser appends to PUBLIC_ROBOT_CAM_URL. */
const asked = [
  ...all(MJPEG_TS, /endpoint\(\s*baseUrl,\s*"([^"]+)"/g),
  // /live-camera's snapshot button asks for its own leaf.
  ...all(LIVE_CAMERA, /endpoint\(\s*robotBase,\s*"([^"]+)"/g),
];

/** The path part of the documented PUBLIC_ROBOT_CAM_URL. */
const camBasePath = (() => {
  const line = WEB_ENV.split("\n").find((l) =>
    /^\s*PUBLIC_ROBOT_CAM_URL\s*=/.test(l),
  );
  const value = line?.split("=").slice(1).join("=").trim() ?? "";
  return value ? new URL(value).pathname.replace(/\/+$/, "") : "";
})();

describe("camera route contract", () => {
  test("every scan matched something — guards the regexes themselves", () => {
    expect(proxied).toContain("/camera/status");
    expect(exposed).toContain("/camera/status");
    expect(served).toContain("/camera/status");
    expect(asked).toContain("status");
    expect(asked).toContain("frame.jpg");
    expect(camBasePath).toBe("/camera");
  });

  test("the bridge serves every path back proxies", () => {
    for (const p of proxied) expect(served).toContain(p);
  });

  test("back proxies exactly what it exposes", () => {
    expect([...new Set(exposed)].sort()).toEqual([...new Set(proxied)].sort());
  });

  test("every path the browser asks for is exposed by back", () => {
    for (const leaf of asked)
      expect(exposed).toContain(`${camBasePath}/${leaf}`);
  });

  test("the documented camera URL points at back, not the bridge", () => {
    // 8001 also serves the unauthenticated /mcp; the browser must not be sent
    // there (see camera.ts). A debug override is fine — the default is not.
    const line = WEB_ENV.split("\n").find((l) =>
      /^\s*PUBLIC_ROBOT_CAM_URL\s*=/.test(l),
    )!;
    expect(line).toContain(":3000/");
    expect(line).not.toContain(":8001");
  });
});
