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
 *
 * And the page's own walk loop, with no stream: which intervals run at which
 * period, the 900 ms re-send, the 8 s dead-man, the 0.5 s stop pulses on
 * release, blur, a hidden page, session end and page leave, the 4 s refusal
 * hold-off, head-yaw scaling and staleness, and how PARAR latches it. Those
 * were pinned before the controller was split into units, so the split could
 * not move a period or the order of a stop unnoticed.
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

type WalkBody = { vx: number; vy: number; vyaw: number; duration_s: number };

type Frame = {
  enabled: boolean;
  head: { yaw: number };
  walk?: number;
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
    setInterval: g.setInterval,
    clearInterval: g.clearInterval,
  };
  let tokens = 0;
  const fetched: string[] = [];
  /** walk_velocity bodies, in the order they were sent. */
  const walks: WalkBody[] = [];
  const answers = {
    walk: (): Promise<Response> | Response => Response.json({}),
  };
  g.WebSocket = FakeSocket;
  g.window = new EventTarget();
  g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  g.location = { hostname: "page", protocol: "http:" };
  g.fetch = async (input: unknown, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    fetched.push(url);
    if (url.includes("/teleop/token"))
      return Response.json({ token: `tok${++tokens}` });
    if (url.includes("/skills/walk_velocity/")) {
      const raw =
        input instanceof Request ? await input.clone().text() : init?.body;
      walks.push(JSON.parse(String(raw)) as WalkBody);
      return answers.walk();
    }
    return Response.json({});
  };
  // Every interval the controller arms, by period, so a test can see which
  // loops run and fire one tick by hand instead of waiting it out.
  const intervals = new Map<unknown, { ms: number; fn: () => void }>();
  g.setInterval = (fn: () => void, ms: number) => {
    const id = saved.setInterval(fn, ms);
    intervals.set(id, { ms, fn });
    return id;
  };
  g.clearInterval = (id: unknown) => {
    intervals.delete(id);
    saved.clearInterval(id);
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
    walks,
    answers,
    /** The periods of every interval running now, sorted. */
    periods: () =>
      [...intervals.values()].map((t) => t.ms).sort((a, b) => a - b),
    /** Run one tick of every interval with this period, then let fetches land. */
    async fire(ms: number) {
      for (const t of [...intervals.values()]) if (t.ms === ms) t.fn();
      await Bun.sleep(5);
    },
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

describe("a stick walk ends when the head pose goes stale (headset off)", () => {
  test("walk goes to 0 once samples stop, and a still-deflected stick restarts nothing", async () => {
    const h = await harness();
    const t = clock();
    try {
      await h.drive();
      h.hold({}); // neutral first, so the next sample is a push
      h.hold({}, 1);
      expect(h.ctl.walking).toBe("forward");
      expect((await h.frame()).walk).toBe(1);
      t.advance(801); // past VR_STALE_MS with no sample: the headset came off
      await Bun.sleep(250); // one 200 ms readout tick
      expect(h.ctl.walking).toBeNull();
      expect((await h.frame()).walk).toBe(0);
      h.hold({}, 1); // back on, thumb still forward: not a fresh push
      expect(h.ctl.walking).toBeNull();
      expect((await h.frame()).walk).toBe(0);
      h.hold({}); // seen at neutral, then pushed again
      h.hold({}, 1);
      expect(h.ctl.walking).toBe("forward");
    } finally {
      t.restore();
      h.restore();
    }
  });
});

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

// ---------------------------------------------------------------------------
// The page's OWN walk loop: no teleop stream, so walk_velocity over /invoke is
// the commander. Pinned by behaviour so the controller can be split without
// moving a period, a threshold or the order of a stop.
// ---------------------------------------------------------------------------

const STOP_PULSE: WalkBody = { vx: 0, vy: 0, vyaw: 0, duration_s: 0.5 };
const FORWARD: WalkBody = { vx: 0.2, vy: 0, vyaw: 0, duration_s: 1 };

describe("the loops the controller runs, by period", () => {
  test("mount: the 1 s clock and the 5 s link probe; VR adds the 200 ms readouts, the 250 ms radar and the 900 ms loop", async () => {
    const h = await harness();
    try {
      expect(h.periods()).toEqual([1000, 5000]);
      await h.ctl.enterVr({} as HTMLElement);
      expect(h.periods()).toEqual([200, 250, 900, 1000, 5000]);
      h.xr().stop();
      expect(h.periods()).toEqual([1000, 5000]);
    } finally {
      h.restore();
    }
  });
});

describe("hold-to-walk over /invoke", () => {
  test("one call at once, re-sent every 900 ms tick, one 0.5 s stop pulse on release", async () => {
    const h = await harness();
    try {
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      expect(h.walks).toEqual([FORWARD]);
      expect(h.periods()).toContain(900);
      await h.fire(900);
      expect(h.walks).toEqual([FORWARD, FORWARD]);
      h.ctl.stopWalking();
      await Bun.sleep(5);
      expect(h.walks.at(-1)).toEqual(STOP_PULSE);
      expect(h.walks.length).toBe(3);
      expect(h.periods()).not.toContain(900);
    } finally {
      h.restore();
    }
  });

  test("the dead-man: 8 s of motion is allowed, past 8 s it latches, sends one stop and the loop ends", async () => {
    const h = await harness();
    const t = clock();
    try {
      h.ctl.startWalking("forward");
      t.advance(8000);
      await h.fire(900);
      expect(h.ctl.deadManTripped).toBe(false);
      expect(h.walks.at(-1)).toEqual(FORWARD);
      t.advance(1);
      await h.fire(900);
      expect(h.ctl.deadManTripped).toBe(true);
      expect(h.ctl.latchCause).toBe("deadman");
      expect(h.ctl.walking).toBeNull();
      expect(h.walks.at(-1)).toEqual(STOP_PULSE);
      const sent = h.walks.length;
      await h.fire(900); // nothing held and no VR: the loop stops itself
      expect(h.walks.length).toBe(sent);
      expect(h.periods()).not.toContain(900);
      // A fresh press is the re-arm.
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      expect(h.ctl.deadManTripped).toBe(false);
      expect(h.walks.at(-1)).toEqual(FORWARD);
    } finally {
      t.restore();
      h.restore();
    }
  });

  for (const [label, emit] of [
    ["pointerup", () => window.dispatchEvent(new Event("pointerup"))],
    ["pointercancel", () => window.dispatchEvent(new Event("pointercancel"))],
    ["blur", () => window.dispatchEvent(new Event("blur"))],
    [
      "a hidden page",
      () => {
        Object.assign(document, { visibilityState: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
      },
    ],
  ] as const) {
    test(`${label} releases a held button with one stop pulse`, async () => {
      const h = await harness();
      try {
        h.ctl.startWalking("back");
        await Bun.sleep(5);
        emit();
        await Bun.sleep(5);
        expect(h.ctl.walking).toBeNull();
        expect(h.walks).toEqual([{ ...FORWARD, vx: -0.2 }, STOP_PULSE]);
        expect(h.periods()).not.toContain(900);
      } finally {
        h.restore();
      }
    });
  }

  test("a page made visible again releases nothing", async () => {
    const h = await harness();
    try {
      h.ctl.startWalking("forward");
      document.dispatchEvent(new Event("visibilitychange")); // still "visible"
      await Bun.sleep(5);
      expect(h.ctl.walking).toBe("forward");
      expect(h.walks).toEqual([FORWARD]);
    } finally {
      h.restore();
    }
  });

  test("a refused walk clears the button, says why and ends the loop", async () => {
    const h = await harness();
    try {
      h.answers.walk = () =>
        Response.json({ status: "failed", phase: "rpc_error" });
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      expect(h.ctl.walking).toBeNull();
      expect(h.ctl.walkNotice?.stopFailed).toBe(false);
      expect(h.ctl.announcement).toContain("No camina:");
      expect(h.periods()).not.toContain(900);
    } finally {
      h.restore();
    }
  });
});

describe("head yaw over /invoke (VR, no stream)", () => {
  test("6° deadzone, 0.3 rad/s at 30°, and a pose older than 800 ms steers nothing", async () => {
    const h = await harness();
    const t = clock();
    try {
      await h.ctl.enterVr({} as HTMLElement);
      h.look(0.05); // inside the 6° deadzone
      await h.fire(900);
      expect(h.walks).toEqual([]);
      h.look(0.5);
      await h.fire(900);
      const dead = (6 * Math.PI) / 180;
      const full = (30 * Math.PI) / 180;
      expect(h.walks.at(-1)!.vx).toBe(0);
      expect(h.walks.at(-1)!.duration_s).toBe(1);
      expect(h.walks.at(-1)!.vyaw).toBeCloseTo(
        ((0.5 - dead) / (full - dead)) * 0.3,
        6,
      );
      h.look(-1);
      await h.fire(900);
      expect(h.walks.at(-1)!.vyaw).toBe(-0.3);
      t.advance(801); // no sample since: tracking is stale
      await h.fire(900);
      expect(h.walks.at(-1)).toEqual(STOP_PULSE);
    } finally {
      t.restore();
      h.restore();
    }
  });

  test("a refusal holds steering off for 4 s, then it resumes", async () => {
    const h = await harness();
    const t = clock();
    try {
      await h.ctl.enterVr({} as HTMLElement);
      h.answers.walk = () =>
        Response.json({ status: "failed", phase: "rpc_error" });
      h.look(0.5);
      await h.fire(900);
      expect(h.walks.length).toBe(1);
      h.answers.walk = () => Response.json({});
      t.advance(3999);
      h.look(0.5);
      await h.fire(900);
      expect(h.walks.length).toBe(1); // held off
      t.advance(2);
      h.look(0.5);
      await h.fire(900);
      expect(h.walks.length).toBe(2);
      expect(h.walks.at(-1)!.vyaw).toBeGreaterThan(0);
    } finally {
      t.restore();
      h.restore();
    }
  });

  test("ending the session mid-walk stops the walk and both VR loops", async () => {
    const h = await harness();
    try {
      await h.ctl.enterVr({} as HTMLElement);
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      h.xr().stop();
      await Bun.sleep(5);
      expect(h.ctl.vrActive).toBe(false);
      expect(h.ctl.walking).toBeNull();
      // releaseAllControls's pulse, then the session end's own.
      expect(h.walks).toEqual([FORWARD, STOP_PULSE, STOP_PULSE]);
      expect(h.periods()).toEqual([1000, 5000]);
    } finally {
      h.restore();
    }
  });
});

describe("PARAR and the page's own walk loop", () => {
  test("no stop pulse of its own, the loop ends, and it stays latched until a fresh press", async () => {
    const h = await harness();
    try {
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      h.estop.phase = "sending";
      await Bun.sleep(0);
      expect(h.ctl.walking).toBeNull();
      expect(h.ctl.deadManTripped).toBe(true);
      expect(h.ctl.latchCause).toBe("estop");
      expect(h.periods()).not.toContain(900);
      h.ctl.startWalking("forward"); // refused while the stop is going out
      expect(h.ctl.walking).toBeNull();
      expect(h.ctl.announcement).toContain("PARAR en curso");
      h.estop.phase = "idle";
      await Bun.sleep(5);
      expect(h.walks).toEqual([FORWARD]);
      expect(h.ctl.deadManTripped).toBe(true);
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      expect(h.ctl.deadManTripped).toBe(false);
      expect(h.walks).toEqual([FORWARD, FORWARD]);
    } finally {
      h.restore();
    }
  });

  test("an answer to a walk sent before PARAR is not news", async () => {
    const h = await harness();
    try {
      let answer!: (r: Response) => void;
      h.answers.walk = () => new Promise<Response>((r) => (answer = r));
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      h.estop.phase = "sending";
      await Bun.sleep(0);
      answer(Response.json({ status: "cancelled" }));
      await Bun.sleep(5);
      expect(h.ctl.walkNotice).toBeNull();
    } finally {
      h.restore();
    }
  });

  test("in VR, the loop comes back latched once the stop is over, and a centred head re-arms it", async () => {
    const h = await harness();
    try {
      await h.ctl.enterVr({} as HTMLElement);
      h.estop.phase = "sending";
      await Bun.sleep(0);
      expect(h.periods()).not.toContain(900);
      h.estop.phase = "idle";
      await Bun.sleep(0);
      expect(h.periods()).toContain(900);
      expect(h.ctl.deadManTripped).toBe(true);
      h.look(0);
      await h.fire(900);
      expect(h.ctl.deadManTripped).toBe(false);
      expect(h.walks).toEqual([]);
    } finally {
      h.restore();
    }
  });
});

describe("leaving the page", () => {
  test("an idle page sends no walk at all", async () => {
    const h = await harness();
    try {
      h.ctl.destroy();
      await Bun.sleep(5);
      expect(h.walks).toEqual([]);
    } finally {
      h.restore();
    }
  });

  test("a walking page sends exactly one stop pulse and stops every loop it ran", async () => {
    const h = await harness();
    try {
      await h.ctl.enterVr({} as HTMLElement);
      h.ctl.startWalking("forward");
      await Bun.sleep(5);
      h.ctl.destroy();
      await Bun.sleep(5);
      expect(h.walks).toEqual([FORWARD, STOP_PULSE]);
      expect(h.ctl.walking).toBeNull();
      // The clock and the link probe belong to mount()'s teardown.
      expect(h.periods()).toEqual([1000, 5000]);
    } finally {
      h.restore();
    }
  });

  test("with the stream open, PARAR still goes down it after the session ended", async () => {
    const h = await harness();
    try {
      await h.drive();
      h.xr().stop();
      h.estop.phase = "sending";
      await Bun.sleep(0);
      const last = JSON.parse(h.socket().sent.at(-1)!) as Frame;
      expect(last.estop).toBe(true);
    } finally {
      h.restore();
    }
  });
});
