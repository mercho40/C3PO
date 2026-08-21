/**
 * The voice loop's control, against a real HTTP server that behaves like `back`.
 *
 * This is the switch that turns overheard speech into robot motion, so the
 * properties worth pinning are the honesty ones: the UI must never show
 * "running" because a button was pressed, and must never let a silent
 * push-to-talk mic read as a working loop.
 */

import { describe, expect, test } from "bun:test";
import {
  canHear,
  isRunning,
  voiceCommand,
  voiceStatus,
} from "../src/lib/robot/voice-loop";

type Reply = { status: number; body: unknown };

function serve(replies: { status?: Reply; start?: Reply; stop?: Reply }) {
  const calls: string[] = [];
  const server = Bun.serve({
    port: 0,
    fetch(req) {
      const path = new URL(req.url).pathname;
      calls.push(`${req.method} ${path}`);
      const reply =
        path.endsWith("/start")
          ? replies.start
          : path.endsWith("/stop")
            ? replies.stop
            : replies.status;
      if (!reply) return new Response("no", { status: 500 });
      return new Response(JSON.stringify(reply.body), {
        status: reply.status,
        headers: { "content-type": "application/json" },
      });
    },
  });
  return { server, calls, url: `http://127.0.0.1:${server.port}` };
}

const STOPPED = {
  running: false,
  utterancesHeard: 0,
  agentRuns: 0,
  stopsTriggered: 0,
  micEverOpen: false,
  alwaysListening: false,
  lastError: null,
  lastHeard: null,
};

const RUNNING = { ...STOPPED, running: true, micEverOpen: true, utterancesHeard: 3 };

/** The base URL is an argument, so the fake needs no fetch patching. */

describe("voice loop client", () => {
  test("a successful start returns the server's state, not the button press", async () => {
    // Reporting "running" because somebody clicked, when the loop failed to
    // start, is the same lie as an <img> holding its last frame.
    const { server, url } = serve({ start: { status: 200, body: RUNNING } });
    const result = await voiceCommand(url, "start");
    expect(result.ok).toBe(true);
    expect(result.ok && isRunning(result.state)).toBe(true);
    expect(result.ok && result.state.utterancesHeard).toBe(3);
    server.stop(true);
  });

  test("a failed start yields no state at all", async () => {
    const { server, url } = serve({ start: { status: 500, body: {} } });
    const result = await voiceCommand(url, "start");
    expect(result.ok).toBe(false);
    expect(!result.ok && result.reason).toContain("could not start");
    server.stop(true);
  });

  test("a 401 says so rather than blaming the robot", async () => {
    const { server, url } = serve({ start: { status: 401, body: {} } });
    const result = await voiceCommand(url, "start");
    expect(!result.ok && result.reason).toBe("not signed in");
    server.stop(true);
  });

  test("running is not hearing", async () => {
    // With a push-to-talk mic, silence means nobody held the button — not that
    // nobody spoke. A loop running over a mic that never opened has done
    // nothing and will do nothing.
    const { server, url } = serve({
      status: { status: 200, body: { ...RUNNING, micEverOpen: false } },
    });
    const result = await voiceStatus(url);
    expect(result.ok && isRunning(result.state)).toBe(true);
    expect(result.ok && canHear(result.state)).toBe(false);
    server.stop(true);
  });

  test("stop reports the server's state too", async () => {
    const { server, url } = serve({ stop: { status: 200, body: STOPPED } });
    const result = await voiceCommand(url, "stop");
    expect(result.ok && isRunning(result.state)).toBe(false);
    server.stop(true);
  });

  test("an unreachable back is named as such", async () => {
    // A dead back and a refusing back are different problems with different
    // fixes, and the operator should not have to guess which they have.
    const { server, url } = serve({});
    server.stop(true);
    const result = await voiceCommand(url, "start");
    expect(!result.ok && result.reason).toBe("back is unreachable");
  });

  test("a 200 that is not JSON is a reason, not a crash", async () => {
    const server = Bun.serve({
      port: 0,
      fetch: () => new Response("<html>hello</html>", { status: 200 }),
    });
    const result = await voiceStatus(`http://127.0.0.1:${server.port}`);
    expect(!result.ok && result.reason).toContain("not a status");
    server.stop(true);
  });

  test("start and stop hit different endpoints", async () => {
    const { server, calls, url } = serve({
      start: { status: 200, body: RUNNING },
      stop: { status: 200, body: STOPPED },
    });
    await voiceCommand(url, "start");
    await voiceCommand(url, "stop");
    expect(calls).toEqual(["POST /voice/start", "POST /voice/stop"]);
    server.stop(true);
  });

  test("no state is not the same as not running", () => {
    // Before the first poll answers there is nothing to report, and the panel
    // must say "checking" rather than draw a confident "not running".
    expect(isRunning(null)).toBe(false);
    expect(canHear(null)).toBe(false);
  });
});
