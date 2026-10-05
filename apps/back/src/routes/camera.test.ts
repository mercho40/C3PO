/**
 * The camera proxy against a real (fake) bridge on a real socket.
 *
 * The stream half is the part worth testing: it is the first proxy in this app
 * that never finishes, so the two properties that matter are ones a mocked
 * `fetch` could not show — the picture keeps flowing past the connect timeout,
 * and closing the viewer closes the upstream.
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";

import { cameraRoutes } from "./camera";

let bridge: ReturnType<typeof Bun.serve>;
let statusReply: () => Response = () => Response.json({ live: true });
let streamClosed = 0;
/**
 * A port nothing listens on. Picked by binding and releasing rather than
 * hardcoded: a fixed "dead" port is only dead until something else on the
 * machine happens to use it, and then the bridge answers and the test fails
 * for a reason that has nothing to do with the proxy.
 */
let deadPort = 0;

beforeAll(() => {
  const probe = Bun.serve({ port: 0, fetch: () => new Response() });
  deadPort = probe.port!;
  probe.stop(true);

  bridge = Bun.serve({
    port: 0,
    fetch(req) {
      const path = new URL(req.url).pathname;
      if (path === "/camera/status") return statusReply();
      if (path === "/camera/stream.mjpg") {
        let timer: ReturnType<typeof setInterval>;
        const body = new ReadableStream({
          start(controller) {
            const frame = new TextEncoder().encode(
              "--c3poframe\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n",
            );
            controller.enqueue(frame);
            timer = setInterval(() => controller.enqueue(frame), 20);
          },
          cancel() {
            clearInterval(timer);
            streamClosed += 1;
          },
        });
        return new Response(body, {
          headers: {
            "Content-Type": "multipart/x-mixed-replace; boundary=c3poframe",
          },
        });
      }
      return new Response("not found", { status: 404 });
    },
  });
  process.env.BRIDGE_URL = `http://127.0.0.1:${bridge.port}/mcp`;
});

afterAll(() => {
  bridge.stop(true);
});

const get = (path: string, init?: RequestInit) =>
  cameraRoutes.handle(new Request(`http://back${path}`, init));

describe("/camera/status", () => {
  test("relays the bridge's JSON, uncached", async () => {
    statusReply = () => Response.json({ live: true, source: "videohub" });
    const res = await get("/camera/status");
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(await res.json()).toEqual({ live: true, source: "videohub" });
  });

  test("a 503 keeps its body — the reason is the point", async () => {
    statusReply = () =>
      Response.json({ hint: "neither camera feed is live" }, { status: 503 });
    const res = await get("/camera/status");
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ hint: "neither camera feed is live" });
  });

  test("any other upstream error is a 502", async () => {
    statusReply = () => new Response("boom", { status: 500 });
    const res = await get("/camera/status");
    expect(res.status).toBe(502);
  });

  test("a dead bridge is bridge_unavailable, not a hang", async () => {
    const saved = process.env.BRIDGE_URL;
    process.env.BRIDGE_URL = `http://127.0.0.1:${deadPort}/mcp`;
    try {
      const res = await get("/camera/status");
      expect(res.status).toBe(502);
      expect(await res.json()).toEqual({ error: "bridge_unavailable" });
    } finally {
      process.env.BRIDGE_URL = saved;
    }
  });
});

describe("/camera/stream.mjpg", () => {
  test("streams frames with the multipart boundary intact", async () => {
    const res = await get("/camera/stream.mjpg");
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe(
      "multipart/x-mixed-replace; boundary=c3poframe",
    );
    const reader = res.body!.getReader();
    const { value } = await reader.read();
    expect(new TextDecoder().decode(value)).toContain("--c3poframe");
    await reader.cancel();
  });

  test("closing the viewer closes the upstream stream", async () => {
    const closedBefore = streamClosed;
    const viewer = new AbortController();
    const res = await get("/camera/stream.mjpg", { signal: viewer.signal });
    const reader = res.body!.getReader();
    await reader.read();
    viewer.abort();
    await reader.cancel().catch(() => {});
    // The fake bridge counts cancellations; give the socket a moment to close.
    for (let i = 0; i < 50 && streamClosed === closedBefore; i++) {
      await Bun.sleep(20);
    }
    expect(streamClosed).toBeGreaterThan(closedBefore);
  });

  test("a dead bridge is a 502, not a hang", async () => {
    const saved = process.env.BRIDGE_URL;
    process.env.BRIDGE_URL = `http://127.0.0.1:${deadPort}/mcp`;
    try {
      const res = await get("/camera/stream.mjpg");
      expect(res.status).toBe(502);
    } finally {
      process.env.BRIDGE_URL = saved;
    }
  });
});
