/**
 * The camera paths line up across all THREE hops that serve them.
 *
 * Same reason as `telemetry.test.ts`, one hop longer. The picture goes
 * browser -> apps/back -> bridge, and each hop names the path itself:
 *
 *   apps/web     `endpoint(base, "status")`, `endpoint(base, "stream.mjpg")`
 *                with base = PUBLIC_ROBOT_CAM_URL (`http://localhost:3000/camera`,
 *                or the bridge's `:8001/camera` under dev:robot)
 *   apps/back    `.get("/camera/…")` exposed, `proxyOnce(…, "/camera/…")` /
 *                `http.request("/camera/…")` asked
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
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const APPS = join(import.meta.dir, "..", "..", "..");
const read = (...parts: string[]) => readFileSync(join(APPS, ...parts), "utf8");

const CAMERA_TS = read("back", "src", "routes", "camera.ts");
const LOOK_TS = read("back", "src", "agent", "look.ts");
/** Where the bridge declares its HTTP routes: one module per concern in bridge/routes/. */
const BRIDGE_ROUTES_PY = readdirSync(
  join(APPS, "bridge", "src", "bridge", "routes"),
)
  .filter((f) => f.endsWith(".py"))
  .map((f) => read("bridge", "src", "bridge", "routes", f))
  .join("\n");
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
  ...all(CAMERA_TS, /\.request\(\s*"([^"]+)"/g),
  ...all(CAMERA_TS, /proxyOnce\(\s*deps\.http,\s*"([^"]+)"/g),
];
/** What back exposes to the browser. */
const exposed = all(CAMERA_TS, /\.get\(\s*"(\/camera\/[^"]+)"/g);
/** What the bridge serves. */
const served = all(BRIDGE_ROUTES_PY, /@mcp\.custom_route\(\s*"([^"]+)"/g);
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

  test("look uploads only the face-blurred frame, and the bridge serves it", () => {
    const fetched = all(LOOK_TS, /\.request\(\s*"([^"]+)"/g);
    expect(fetched).toContain("/camera/cloud_frame.jpg");
    expect(fetched).not.toContain("/camera/frame.jpg");
    for (const p of fetched) expect(served).toContain(p);
  });

  test("the vision smoke test uploads the face-blurred frame too", () => {
    const fetched = all(
      read("back", "scripts", "smoke-vision.ts"),
      /pathname = "([^"]+)"/g,
    );
    expect(fetched).toEqual(["/camera/cloud_frame.jpg"]);
    for (const p of fetched) expect(served).toContain(p);
  });

  test("back proxies exactly what it exposes", () => {
    expect([...new Set(exposed)].sort()).toEqual([...new Set(proxied)].sort());
  });

  test("every path the browser asks for is exposed by back", () => {
    for (const leaf of asked)
      expect(exposed).toContain(`${camBasePath}/${leaf}`);
  });

  test("dev:robot's camera default serves every leaf the browser asks for", () => {
    // `bun run dev:robot` overrides .env.example with its own default, the
    // bridge's 8001 (aa05697). Same /camera path, so the same leaves — and the
    // server it names has to answer them, whichever of the two it is.
    const pkg = JSON.parse(read("web", "package.json")) as {
      scripts: Record<string, string>;
    };
    const m = /PUBLIC_ROBOT_CAM_URL=\$\{PUBLIC_ROBOT_CAM_URL:-([^}]+)\}/.exec(
      pkg.scripts["dev:robot"] ?? "",
    );
    expect(m).not.toBeNull();
    const url = new URL(m![1]!);
    expect(url.hostname).toBe("localhost");
    expect(url.pathname.replace(/\/+$/, "")).toBe(camBasePath);
    const answers = url.port === "8001" ? served : exposed;
    expect(["3000", "8001"]).toContain(url.port);
    for (const leaf of asked)
      expect(answers).toContain(`${camBasePath}/${leaf}`);
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
