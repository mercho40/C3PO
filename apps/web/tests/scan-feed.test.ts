/**
 * The headset radar's feed (`scan.svelte.ts`), run for real with `fetch` and
 * the interval faked: what each answer from GET /telemetry/scan does to the
 * ring and its reason, the 4 Hz period, and that a slow answer never stacks
 * a second request behind it.
 *
 * "No ring" has three causes and they must stay three: 503 (nothing is
 * publishing, with the bridge's hint), a transport failure (unreachable), and
 * a ring that arrived but cannot be drawn. And the last good ring is KEPT on
 * a 503 — an empty dial reads as "nothing around you".
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

const RING = {
  v: 1,
  frame: "base",
  a0_deg: -180,
  step_deg: 3,
  max_cm: 800,
  r_cm: [120, null, 340],
  age_s: 0.2,
  stale: false,
};

type Answer = () => Promise<Response> | Response;

async function harness() {
  const { ScanFeed } = await import("../src/lib/robot/scan.svelte");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  const saved = {
    fetch: g.fetch,
    window: g.window,
    document: g.document,
    setInterval: g.setInterval,
    clearInterval: g.clearInterval,
  };
  const urls: string[] = [];
  const periods: number[] = [];
  const ticks: (() => void)[] = [];
  let answer: Answer = () => Response.json(RING);
  g.window = new EventTarget();
  g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  g.fetch = async (input: unknown, init?: RequestInit) => {
    urls.push(String(input));
    // A request the feed has given up on must not land.
    const signal = init?.signal;
    const res = await answer();
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    return res;
  };
  g.setInterval = (fn: () => void, ms: number) => {
    periods.push(ms);
    ticks.push(fn);
    return ticks.length;
  };
  g.clearInterval = () => {
    ticks.length = 0;
  };
  const feed = new ScanFeed();
  return {
    feed,
    urls,
    periods,
    answer: (a: Answer) => (answer = a),
    /** One interval tick, then let the answer land. */
    async tick() {
      for (const t of [...ticks]) t();
      await Bun.sleep(5);
    },
    restore() {
      feed.stop();
      Object.assign(g, saved);
    },
  };
}

describe("ScanFeed", () => {
  test("polls /telemetry/scan at once, then every 250 ms", async () => {
    const h = await harness();
    try {
      h.feed.start();
      await Bun.sleep(5);
      expect(h.urls).toEqual(["http://localhost:3000/telemetry/scan"]);
      expect(h.periods).toEqual([250]);
      expect(h.feed.ring?.r_cm).toEqual([120, null, 340]);
      expect(h.feed.ageS).toBe(0.2);
      expect(h.feed.stale).toBe(false);
      expect(h.feed.reason).toBeNull();
      expect(h.feed.unreachable).toBe(false);
      await h.tick();
      expect(h.urls.length).toBe(2);
    } finally {
      h.restore();
    }
  });

  test("a 503 keeps the last ring and says the bridge's hint", async () => {
    const h = await harness();
    try {
      h.feed.start();
      await Bun.sleep(5);
      h.answer(() =>
        Response.json({ hint: "c3po perception up nav2" }, { status: 503 }),
      );
      await h.tick();
      expect(h.feed.ring?.r_cm).toEqual([120, null, 340]);
      expect(h.feed.reason).toBe("c3po perception up nav2");
      expect(h.feed.unreachable).toBe(false);
      h.answer(() => new Response("", { status: 503 }));
      await h.tick();
      expect(h.feed.reason).toBe("no llega /scan");
    } finally {
      h.restore();
    }
  });

  test("another status is unreachable, with the code", async () => {
    const h = await harness();
    try {
      h.answer(() => new Response("", { status: 502 }));
      h.feed.start();
      await Bun.sleep(5);
      expect(h.feed.ring).toBeNull();
      expect(h.feed.reason).toBe("lidar no disponible (502)");
      expect(h.feed.unreachable).toBe(true);
    } finally {
      h.restore();
    }
  });

  test("a ring that cannot be drawn is a third reason, not a 503", async () => {
    const h = await harness();
    try {
      h.answer(() => Response.json({ ...RING, max_cm: 0 }));
      h.feed.start();
      await Bun.sleep(5);
      expect(h.feed.ring).toBeNull();
      expect(h.feed.reason).toBe(
        "el anillo llegó con un formato que no se puede dibujar",
      );
      expect(h.feed.unreachable).toBe(false);
    } finally {
      h.restore();
    }
  });

  test("no answer at all is unreachable", async () => {
    const h = await harness();
    try {
      h.answer(() => Promise.reject(new TypeError("Failed to fetch")));
      h.feed.start();
      await Bun.sleep(5);
      expect(h.feed.reason).toBe("no se puede alcanzar la API de la consola");
      expect(h.feed.unreachable).toBe(true);
    } finally {
      h.restore();
    }
  });

  test("a slow answer is not stacked behind; start twice arms one timer", async () => {
    const h = await harness();
    try {
      let land!: (r: Response) => void;
      h.answer(() => new Promise<Response>((r) => (land = r)));
      h.feed.start();
      h.feed.start();
      await h.tick();
      await h.tick();
      expect(h.urls.length).toBe(1);
      expect(h.periods).toEqual([250]);
      land(Response.json(RING));
      await Bun.sleep(5);
      await h.tick();
      expect(h.urls.length).toBe(2);
    } finally {
      h.restore();
    }
  });

  test("stop ends the polling; start resumes it", async () => {
    const h = await harness();
    try {
      h.feed.start();
      await Bun.sleep(5);
      h.feed.stop();
      await h.tick();
      expect(h.urls.length).toBe(1);
      h.feed.start();
      await Bun.sleep(5);
      expect(h.urls.length).toBe(2);
      expect(h.periods).toEqual([250, 250]);
    } finally {
      h.restore();
    }
  });
});
