/**
 * The VR controller run for real: rune modules compiled by Svelte, a fake
 * teleop socket (the FakeSocket of stream.test.ts) and a fake XR session whose
 * callbacks the test drives. What reaches the socket is what reaches the robot.
 *
 * Pinned here: PARAR goes down the socket, every connect asks back for a fresh
 * (single-use) token, the Meta menu zeroes steering at once (C5), and head yaw
 * is held through the headset's own voice turn and until the head is centred
 * again (C8), the grips chord is PARAR only after 300 ms, observe-only sends
 * nothing but PARAR, and PARAR drops the arms.
 */

import { describe, expect, mock, test } from "bun:test";
import { plugin } from "bun";
import { readFileSync } from "node:fs";
import { compileModule } from "svelte/compiler";
import * as realXr from "../src/lib/webxr/xr-teleop";
import type {
  XrButtons,
  XrSample,
  XrTeleopCallbacks,
} from "../src/lib/webxr/xr-teleop";

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

class FakeXr {
  static last: FakeXr | null = null;
  active = true;
  mode = "immersive-vr";
  constructor(public cb: XrTeleopCallbacks) {
    FakeXr.last = this;
    // Every setter the controller pushes into the headset is a no-op here.
    return new Proxy(this, {
      get: (t, k) => (k in t ? t[k as keyof FakeXr] : () => {}),
    });
  }
  async start() {}
  stop() {
    this.active = false;
    this.cb.onEnd?.();
  }
}
mock.module("#lib/webxr/xr-teleop.js", () => ({
  ...realXr,
  XrTeleopSession: FakeXr,
}));

class FakeSocket {
  static OPEN = 1;
  static made: FakeSocket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  sent: string[] = [];
  #listeners: Record<string, ((e: unknown) => void)[]> = {};
  constructor(public url: string) {
    FakeSocket.made.push(this);
  }
  addEventListener(type: string, fn: (e: unknown) => void) {
    (this.#listeners[type] ??= []).push(fn);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  emit(type: string, event: unknown = {}) {
    for (const fn of this.#listeners[type] ?? []) fn(event);
  }
}

type Frame = {
  enabled: boolean;
  head: { yaw: number };
  arms?: boolean;
  estop?: boolean;
};

async function harness() {
  // Imported before any global is replaced, so a failed import leaks nothing.
  const { VrControl } = await import("../src/lib/robot/vr-control.svelte");
  const { estop } = await import("../src/lib/robot/estop.svelte");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  const saved = {
    WebSocket: g.WebSocket,
    fetch: g.fetch,
    window: g.window,
    document: g.document,
    location: g.location,
  };
  let tokens = 0;
  const fetched: string[] = [];
  g.WebSocket = FakeSocket;
  g.window = new EventTarget();
  g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  g.location = { hostname: "page", protocol: "http:" };
  g.fetch = async (input: unknown) => {
    const url = String(input instanceof Request ? input.url : input);
    fetched.push(url);
    if (url.includes("/teleop/token"))
      return Response.json({ token: `tok${++tokens}` });
    return Response.json({});
  };
  FakeSocket.made = [];
  FakeXr.last = null;

  const voice = { busy: false, cancelled: 0, talks: 0 };
  const ctl = new VrControl({
    posture: () => null,
    voice: {
      onItem: () => false,
      onTalk: () => voice.talks++,
      cancelTalk: () => voice.cancelled++,
      busy: () => voice.busy,
    },
  });
  const unmount = ctl.mount();

  const sample = (
    yaw: number,
    buttons: Partial<XrButtons> = {},
    walkAxis: XrSample["walkAxis"] = 0,
  ): XrSample => ({
    yawRadians: yaw,
    yawErrorRadians: yaw,
    headPosition: [0, 1.6, 0],
    left: null,
    right: null,
    walkAxis,
    buttons: {
      trigger: false,
      primary: false,
      secondary: false,
      grips: 0,
      ...buttons,
    },
  });

  return {
    ctl,
    estop,
    voice,
    socket: () => FakeSocket.made.at(-1)!,
    /** Connect the stream and enter VR, the normal operating mode. */
    async drive() {
      await ctl.connectStream();
      FakeSocket.made.at(-1)!.emit("open");
      await ctl.enterVr({} as HTMLElement);
    },
    look: (yaw: number) => FakeXr.last!.cb.onSample?.(sample(yaw)),
    /** One XR sample with these buttons (and stick) held, head centred. */
    hold: (buttons: Partial<XrButtons>, walkAxis: XrSample["walkAxis"] = 0) =>
      FakeXr.last!.cb.onSample?.(sample(0, buttons, walkAxis)),
    /** Every request the page made, by URL. */
    fetched,
    /** stop_everything requests: one per PARAR. */
    stops: () => fetched.filter((u) => u.includes("/stop_everything/")).length,
    xr: () => FakeXr.last!,
    /** The newest frame the bridge got, after at least one send tick. */
    async frame(): Promise<Frame> {
      await Bun.sleep(50);
      return JSON.parse(FakeSocket.made.at(-1)!.sent.at(-1)!) as Frame;
    },
    tokens: () => tokens,
    restore() {
      unmount();
      ctl.destroy();
      estop.phase = "idle";
      Object.assign(g, saved);
    },
  };
}

describe("PARAR and the teleop socket", () => {
  test("PARAR sends an estop frame down the open socket at once", async () => {
    const h = await harness();
    try {
      await h.ctl.connectStream();
      h.socket().emit("open");
      h.socket().bufferedAmount = 10_000; // the 30 Hz tick skips; a stop must not
      h.estop.phase = "sending";
      await Bun.sleep(0);
      const frames = h.socket().sent.map((m) => JSON.parse(m) as Frame);
      expect(frames.length).toBe(1);
      expect(frames[0]!.estop).toBe(true);
      expect(frames[0]!.enabled).toBe(false);
    } finally {
      h.restore();
    }
  });

  test("every connect asks apps/back for a fresh token (single-use on the bridge)", async () => {
    const h = await harness();
    try {
      await h.ctl.connectStream();
      h.socket().emit("close", { code: 1006 });
      await h.ctl.connectStream(); // the reconnect
      expect(h.tokens()).toBe(2);
      expect(FakeSocket.made.map((s) => s.url)).toEqual([
        "ws://page:8767/?token=tok1",
        "ws://page:8767/?token=tok2",
      ]);
    } finally {
      h.restore();
    }
  });
});

describe("the Meta menu (visible-blurred) stops steering at once (C5)", () => {
  test("yaw goes to zero on the next frame and the talk is dropped", async () => {
    const h = await harness();
    try {
      await h.drive();
      h.look(0.5);
      expect((await h.frame()).enabled).toBe(true);
      h.xr().cb.onVisibility?.("visible-blurred");
      const f = await h.frame(); // well inside VR_STALE_MS (800 ms)
      expect(f.enabled).toBe(false);
      expect(f.head.yaw).toBe(0);
      expect(h.voice.cancelled).toBeGreaterThan(0);
    } finally {
      h.restore();
    }
  });
});

describe("coming back from the Meta menu or the headset off (C5)", () => {
  for (const state of ["visible-blurred", "hidden"] as const) {
    test(`after ${state}, yaw steers only once the head is back at centre`, async () => {
      const h = await harness();
      try {
        await h.drive();
        h.look(0); // the last pose before the overlay is centred: it must not release the hold
        h.xr().cb.onVisibility?.(state);
        await h.frame(); // the 30 Hz pull reads that stale centred yaw meanwhile
        h.xr().cb.onVisibility?.("visible");
        h.look(0.5); // back, with the head turned
        expect((await h.frame()).enabled).toBe(false);
        expect(h.ctl.yawPaused).toBe(true);
        h.look(0);
        await h.frame();
        expect(h.ctl.yawPaused).toBe(false);
        h.look(0.5);
        expect((await h.frame()).enabled).toBe(true);
      } finally {
        h.restore();
      }
    });
  }
});

describe("head yaw during the headset's own voice turn (C8)", () => {
  test("held while busy, and after it until the head is centred", async () => {
    const h = await harness();
    try {
      await h.drive();
      h.look(0.5);
      expect((await h.frame()).head.yaw).toBe(0.5);

      h.look(0); // centred when the turn starts: the 30 Hz pull must not release on it
      h.voice.busy = true; // «andá hasta la silla»: looking around must not steer
      await h.frame();
      h.look(0.5);
      let f = await h.frame();
      expect(f.head.yaw).toBe(0);
      expect(f.enabled).toBe(false);

      h.voice.busy = false; // the turn ends with the head still 0.5 rad left
      h.look(0.5);
      expect((await h.frame()).enabled).toBe(false);

      h.look(0); // back to centre: the deliberate act
      await h.frame();
      h.look(0.5);
      f = await h.frame();
      expect(f.head.yaw).toBe(0.5);
      expect(f.enabled).toBe(true);
    } finally {
      h.restore();
    }
  });
});

/** Date.now, under the test's control: the chord's hold is timed by it. */
function clock() {
  const real = Date.now;
  let now = real();
  Date.now = () => now;
  return {
    advance: (ms: number) => void (now += ms),
    restore: () => void (Date.now = real),
  };
}

describe("the in-headset PARAR chord (both grips)", () => {
  test("299 ms is not PARAR, 301 ms is, once per hold; one grip never is", async () => {
    const h = await harness();
    const t = clock();
    try {
      await h.drive();
      h.hold({ grips: 1 });
      t.advance(1000);
      h.hold({ grips: 1 });
      h.hold({ grips: 2 }); // the chord starts here
      t.advance(299);
      h.hold({ grips: 2 });
      await Bun.sleep(0);
      expect(h.stops()).toBe(0);
      t.advance(2);
      h.hold({ grips: 2 });
      await Bun.sleep(20); // the press settles, so a second one would go out
      expect(h.stops()).toBe(1);
      t.advance(1000);
      h.hold({ grips: 2 }); // still the same hold
      await Bun.sleep(0);
      expect(h.stops()).toBe(1);
    } finally {
      t.restore();
      h.restore();
    }
  });
});

describe("observe-only, run for real", () => {
  test("the stick, the trigger, B/Y and the head command nothing; PARAR still does", async () => {
    const h = await harness();
    const t = clock();
    try {
      await h.ctl.connectStream();
      h.socket().emit("open");
      await h.ctl.enterVr({} as HTMLElement, true);
      expect(h.socket().readyState).toBe(3); // closed on the way in
      const sent = h.socket().sent.length;
      h.hold({}); // everything released and at neutral first, so the next is a press
      h.hold({ trigger: true, secondary: true }, 1);
      h.look(0); // centred first, so a turned head would steer a commander
      h.look(0.5);
      await Bun.sleep(1000); // a local loop (900 ms) would have ticked
      expect(h.fetched.filter((u) => u.includes("/skills/"))).toEqual([]);
      expect(h.socket().sent.length).toBe(sent);
      expect(h.ctl.walking).toBeNull();
      expect(h.voice.talks).toBe(0);
      await h.ctl.connectStream();
      expect(FakeSocket.made.length).toBe(1);
      // PARAR is never anyone's to withhold.
      h.hold({ grips: 2 });
      t.advance(301);
      h.hold({ grips: 2 });
      await Bun.sleep(0);
      expect(h.stops()).toBe(1);
    } finally {
      t.restore();
      h.restore();
    }
  });
});

describe("PARAR drops the arms", () => {
  test("arms requested go false, and stay false once the latch is lifted", async () => {
    const h = await harness();
    try {
      await h.drive();
      h.ctl.toggleArms();
      h.look(0);
      expect((await h.frame()).arms).toBe(true);
      h.estop.phase = "sending";
      await Bun.sleep(0);
      expect(h.ctl.armsRequested).toBe(false);
      h.look(0);
      expect((await h.frame()).arms).toBe(false);
      h.estop.phase = "idle"; // the stop is over: nobody asked for the arms again
      h.look(0);
      expect((await h.frame()).arms).toBe(false);
    } finally {
      h.restore();
    }
  });
});
