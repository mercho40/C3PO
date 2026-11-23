/**
 * The PARAR latch's poller and «Reanudar» (`stop-latch.svelte.ts`) and the
 * Nav2 gate's (`gate.svelte.ts`), now on Eden: run for real with `fetch`
 * faked, the rune modules compiled the way scan-feed.test.ts does.
 *
 * What moving off a hand-written `fetch` must not change: the session cookie
 * still rides along, a 401 still means "log in", a latch or a gate that cannot
 * be read keeps its last reading (unknown is not cleared, unknown is not
 * closed) — including back's new 502 `bridge_bad_payload` — and «Reanudar»
 * still sends the stamp it showed and says why it failed.
 */

import { describe, expect, mock, test } from "bun:test";
import { plugin } from "bun";
import { readFileSync } from "node:fs";
import { compileModule } from "svelte/compiler";

plugin({
  name: "svelte-runes",
  setup(build) {
    build.onLoad({ filter: /\.svelte\.ts$/ }, ({ path }) => {
      const js = new Bun.Transpiler({ loader: "ts" }).transformSync(
        readFileSync(path, "utf8"),
      );
      const out = compileModule(js, { filename: path, generate: "client" });
      return { contents: out.js.code, loader: "js" };
    });
  },
});
mock.module("$app/env", () => ({ browser: true, dev: false }));
// Same keys and values as auth-params.test.ts: bun keeps the first mock's export names.
mock.module("$app/env/public", () => ({
  PUBLIC_API_URL: "http://localhost:3000",
  PUBLIC_ROBOT_CAM_URL: "",
  PUBLIC_TELEOP_HOST: "",
  PUBLIC_TELEOP_PORT: "",
}));
mock.module("$app/navigation", () => ({ goto: async () => {} }));

type Answer = (url: string, init?: RequestInit) => Response | Promise<Response>;

const LATCH = {
  stop_standing: true,
  stopped_at: 1700000000,
  acknowledged_at: null,
};

/** Swap `fetch` (and what `Poller` touches) for one test. */
function fakeNet(answer: Answer) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  const saved = { fetch: g.fetch, window: g.window, document: g.document };
  const sent: { url: string; init?: RequestInit }[] = [];
  g.window = Object.assign(new EventTarget(), {
    location: { pathname: "/dashboard", search: "" },
  });
  g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  const net = { answer };
  g.fetch = async (input: unknown, init?: RequestInit) => {
    sent.push({ url: String(input), init });
    return net.answer(String(input), init);
  };
  return {
    sent,
    answer: (a: Answer) => (net.answer = a),
    restore: () => Object.assign(g, saved),
  };
}

describe("StopLatch", async () => {
  const { StopLatch } = await import("../src/lib/robot/stop-latch.svelte");

  test("reads /safety/status with the session cookie", async () => {
    const net = fakeNet(() => Response.json(LATCH));
    try {
      const latch = new StopLatch();
      await latch.tick();
      expect(net.sent[0]?.url).toBe("http://localhost:3000/safety/status");
      expect(net.sent[0]?.init?.credentials).toBe("include");
      expect(latch.standing).toBe(true);
      expect(latch.status?.stopped_at).toBe(1700000000);
    } finally {
      net.restore();
    }
  });

  test("a latch it cannot read is not cleared", async () => {
    const net = fakeNet(() => Response.json(LATCH));
    try {
      const latch = new StopLatch();
      await latch.tick();
      net.answer(() =>
        Response.json(
          { error: "bridge_bad_payload", path: "/stop_standing" },
          { status: 502 },
        ),
      );
      await latch.tick();
      expect(latch.standing).toBe(true);
      expect(latch.failures).toBe(1);
      net.answer(() => {
        throw new TypeError("fetch failed");
      });
      await latch.tick();
      expect(latch.standing).toBe(true);
      expect(latch.failures).toBe(2);
    } finally {
      net.restore();
    }
  });

  test("a 401 is a session problem", async () => {
    const net = fakeNet(() =>
      Response.json({ error: "unauthorized" }, { status: 401 }),
    );
    try {
      const latch = new StopLatch();
      await latch.tick();
      // Poller's own policy from here: stop and go to log in.
      expect(latch.unauthorized).toBe(true);
    } finally {
      net.restore();
    }
  });

  test("«Reanudar» sends the stamp it showed and says why it failed", async () => {
    const net = fakeNet(() => Response.json(LATCH));
    try {
      const latch = new StopLatch();
      await latch.tick();
      net.answer((url) =>
        url.endsWith("/safety/ack")
          ? Response.json({ error: "newer_stop" }, { status: 409 })
          : Response.json(LATCH),
      );
      await latch.resume();
      const ack = net.sent.find((s) => s.url.endsWith("/safety/ack"));
      expect(ack?.init?.method).toBe("POST");
      expect(ack?.init?.credentials).toBe("include");
      expect(JSON.parse(String(ack?.init?.body))).toEqual({
        stopped_at: 1700000000,
      });
      expect(latch.error).toContain("PARAR más nuevo");

      net.answer(() => {
        throw new TypeError("fetch failed");
      });
      await latch.resume();
      expect(latch.error).toContain("puente");
      expect(latch.resuming).toBe(false);
    } finally {
      net.restore();
    }
  });
});

describe("Gate", async () => {
  const { Gate } = await import("../src/lib/robot/gate.svelte");
  const ARMED = {
    enabled: true,
    disabled_reason: null,
    arm_expires_in_s: 60,
    cmd_vel_received: 3,
    dropped_while_disabled: 0,
    clamped: 0,
    last_sent: [0.1, 0, 0],
    last_sent_age_s: 0.2,
    cmd_vel_age_s: 0.2,
  };

  test("an armed gate that can no longer be vouched for stays armed, sin datos", async () => {
    const net = fakeNet(() => Response.json(ARMED));
    try {
      const gate = new Gate();
      await gate.tick();
      expect(net.sent[0]?.url).toBe("http://localhost:3000/telemetry/gate");
      expect(gate.armed).toBe(true);
      expect(gate.reason).toBeNull();
      net.answer(() =>
        Response.json(
          { error: "bridge_bad_payload", path: "/enabled" },
          { status: 502 },
        ),
      );
      await gate.tick();
      expect(gate.armed).toBe(true);
      expect(gate.reason).not.toBeNull();
      expect(gate.reasonFromBridge).toBe(false);
    } finally {
      net.restore();
    }
  });

  test("the bridge's 503 is definitive and its words are tagged as its own", async () => {
    const net = fakeNet(() => Response.json(ARMED));
    try {
      const gate = new Gate();
      await gate.tick();
      net.answer(() =>
        Response.json({ error: "no perception link" }, { status: 503 }),
      );
      await gate.tick();
      expect(gate.status).toBeNull();
      expect(gate.reason).toBe("no perception link");
      expect(gate.reasonFromBridge).toBe(true);
    } finally {
      net.restore();
    }
  });

  test("no answer keeps the reading and says back is unreachable", async () => {
    const net = fakeNet(() => Response.json(ARMED));
    try {
      const gate = new Gate();
      await gate.tick();
      net.answer(() => {
        throw new TypeError("fetch failed");
      });
      await gate.tick();
      expect(gate.armed).toBe(true);
      expect(gate.reason).toContain("apps/back");
    } finally {
      net.restore();
    }
  });
});
