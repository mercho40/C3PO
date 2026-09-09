/**
 * Tests for the frame the headset transmits.
 *
 * These exist because of one bug, and it is worth stating plainly. The sender
 * used to hold the last payload it was handed and resend it every tick. So
 * anything that stopped updating kept going out as though it were current:
 * end the XR session — or merely lose hand tracking — with the operator's head
 * turned, and the last large yaw was retransmitted 30 times a second while the
 * robot kept rotating. The bridge's 8-second continuous-motion latch bounded
 * it, but eight seconds of rotation nobody asked for is not a defence.
 *
 * The fix was to make the sender PULL, and to prove freshness here on every
 * send. Everything below is a case where stale state must read as zero.
 *
 * Lives outside `src/` deliberately. svelte-check type-checks everything under
 * `src/` against the app's tsconfig, which does not know `bun:test` — and the
 * obvious fix, adding `bun-types`, globally redefines `fetch` and breaks
 * `chat/+page.svelte`. Keeping tests out of the app's source tree costs one
 * relative import and nothing else.
 */

import { describe, expect, test } from "bun:test";
import {
  buildFrame,
  connectTeleop,
  idlePayload,
  type TeleopInput,
} from "../src/lib/teleop/stream";

const STALE_AFTER = 800;
const DEADZONE = (8 * Math.PI) / 180;
const BIG_YAW = 0.7; // well past the deadzone

function input(overrides: Partial<TeleopInput> = {}): TeleopInput {
  return {
    now: 10_000,
    vrActive: true,
    lastSampleAt: 10_000,
    staleAfterMs: STALE_AFTER,
    yawErrorRadians: 0,
    yawDeadzoneRadians: DEADZONE,
    headPosition: [0, 1.62, 0],
    left: null,
    right: null,
    walking: null,
    armsRequested: false,
    ...overrides,
  };
}

const hand = {
  position: [0.2, 1.3, -0.3] as [number, number, number],
  orientation: [0, 0, 0, 1] as [number, number, number, number],
  grip: 0.5,
};

describe("dead-man", () => {
  test("idle commands nothing", () => {
    const f = buildFrame(input());
    expect(f.enabled).toBe(false);
    expect(f.walk).toBe(0);
    expect(f.head.yaw).toBe(0);
  });

  test("a held walk button is a held dead-man", () => {
    expect(buildFrame(input({ walking: "forward" })).enabled).toBe(true);
    expect(buildFrame(input({ walking: "forward" })).walk).toBe(1);
    expect(buildFrame(input({ walking: "back" })).walk).toBe(-1);
  });

  test("a head turned past the deadzone is a held dead-man", () => {
    expect(buildFrame(input({ yawErrorRadians: BIG_YAW })).enabled).toBe(true);
  });

  test("a head inside the deadzone is not", () => {
    const f = buildFrame(input({ yawErrorRadians: DEADZONE * 0.9 }));
    expect(f.enabled).toBe(false);
  });

  test("asking for the arms alone does not hold the dead-man open forever", () => {
    // It does enable the frame -- the operator's hands are busy being tracked
    // and cannot also press something -- but it must not survive staleness.
    expect(buildFrame(input({ armsRequested: true })).enabled).toBe(true);
    expect(
      buildFrame(input({ armsRequested: true, vrActive: false })).enabled,
    ).toBe(false);
  });
});

describe("staleness — the bug this file exists for", () => {
  test("ending the XR session zeroes a yaw that was large", () => {
    const turned = input({ yawErrorRadians: BIG_YAW });
    expect(buildFrame(turned).head.yaw).toBeCloseTo(BIG_YAW);

    const ended = buildFrame({ ...turned, vrActive: false });
    expect(ended.head.yaw).toBe(0);
    expect(ended.enabled).toBe(false);
  });

  test("losing tracking mid-session zeroes a yaw that was large", () => {
    // The likelier failure: the session is still live, samples just stopped.
    const stale = buildFrame(
      input({
        yawErrorRadians: BIG_YAW,
        lastSampleAt: 10_000 - STALE_AFTER - 1,
      }),
    );
    expect(stale.head.yaw).toBe(0);
    expect(stale.enabled).toBe(false);
  });

  test("a sample just inside the window is still trusted", () => {
    const fresh = buildFrame(
      input({
        yawErrorRadians: BIG_YAW,
        lastSampleAt: 10_000 - STALE_AFTER + 1,
      }),
    );
    expect(fresh.head.yaw).toBeCloseTo(BIG_YAW);
  });

  test("stale hands report untracked, not their last pose", () => {
    const live = buildFrame(input({ left: hand, right: hand }));
    expect(live.hands.left?.tracked).toBe(true);
    expect(live.hands.right?.grip).toBe(0.5);

    const stale = buildFrame(
      input({ left: hand, right: hand, vrActive: false }),
    );
    expect(stale.hands.left?.tracked).toBe(false);
    expect(stale.hands.right?.tracked).toBe(false);
    expect(stale.hands.left?.pos).toBeUndefined();
  });

  test("stale head position falls back rather than persisting", () => {
    const stale = buildFrame(
      input({ headPosition: [5, 9, -5], vrActive: false }),
    );
    expect(stale.head.pos).toEqual([0, 1.6, 0]);
  });

  test("the arms are dropped the moment tracking is not fresh", () => {
    expect(buildFrame(input({ armsRequested: true })).arms).toBe(true);
    expect(
      buildFrame(input({ armsRequested: true, lastSampleAt: 0 })).arms,
    ).toBe(false);
  });

  test("walking still works with no headset at all", () => {
    // The buttons are not headset-derived, so staleness must not disable them
    // -- an operator holding a phone or a laptop can still drive.
    const f = buildFrame(input({ vrActive: false, walking: "forward" }));
    expect(f.enabled).toBe(true);
    expect(f.walk).toBe(1);
    expect(f.head.yaw).toBe(0);
  });
});

/**
 * A socket in readyState OPEN is not a working link.
 *
 * TCP holds a connection open through a process that has stopped reading, a
 * laptop that slept, a Wi-Fi handover. The page went on saying "Conectado"
 * through all of it while every send was skipped for backpressure — a green
 * label and a robot that will not move, which is the worst pairing available
 * because it points the operator at the robot.
 */
describe("stall detection — 'Conectado' on a dead link", () => {
  class FakeSocket {
    static OPEN = 1;
    readyState = 1;
    bufferedAmount = 0;
    sent: string[] = [];
    #listeners: Record<string, ((e: unknown) => void)[]> = {};
    constructor(public url: string) {}
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

  function harness() {
    const states: { state: string; detail?: string }[] = [];
    const realWs = globalThis.WebSocket;
    const realNow = Date.now;
    let clock = 1_000_000;
    let made: FakeSocket | null = null;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).WebSocket = class extends FakeSocket {
      constructor(url: string) {
        super(url);
        made = this as unknown as FakeSocket;
      }
    };
    (globalThis as any).WebSocket.OPEN = 1;
    Date.now = () => clock;

    const handle = connectTeleop("ws://x/teleop", {
      getFrame: () => idleFrame(),
      onState: (state, detail) => states.push({ state, detail }),
    });

    return {
      states,
      socket: () => made!,
      advance: (ms: number) => {
        clock += ms;
      },
      restore: () => {
        handle.close();
        globalThis.WebSocket = realWs;
        Date.now = realNow;
      },
    };
  }

  function idleFrame() {
    return buildFrame(input({ lastSampleAt: 0, now: 10_000 }));
  }

  test("silence past the timeout reports stalled, not open", async () => {
    const h = harness();
    try {
      h.socket().emit("open");
      expect(h.states.map((s) => s.state)).toEqual(["connecting", "open"]);

      // Six missed statuses. The bridge sends about two a second.
      h.advance(3100);
      await new Promise((r) => setTimeout(r, 60));

      expect(h.states.map((s) => s.state)).toContain("stalled");
    } finally {
      h.restore();
    }
  });

  test("a status arriving clears the stall", async () => {
    const h = harness();
    try {
      h.socket().emit("open");
      h.advance(3100);
      await new Promise((r) => setTimeout(r, 60));
      expect(h.states.map((s) => s.state)).toContain("stalled");

      h.socket().emit("message", {
        data: JSON.stringify({ type: "status", frames_received: 1 }),
      });
      await new Promise((r) => setTimeout(r, 60));

      expect(h.states[h.states.length - 1]?.state).toBe("open");
    } finally {
      h.restore();
    }
  });

  test("a talking bridge never reports stalled", async () => {
    const h = harness();
    try {
      h.socket().emit("open");
      for (let i = 0; i < 6; i++) {
        h.advance(500);
        h.socket().emit("message", {
          data: JSON.stringify({ type: "status", frames_received: i }),
        });
        await new Promise((r) => setTimeout(r, 30));
      }
      expect(h.states.map((s) => s.state)).not.toContain("stalled");
    } finally {
      h.restore();
    }
  });
});

describe("observe-only — a headset that watches and commands nothing", () => {
  /**
   * The failure this guards against is not a wrong movement, it is a WRONG
   * BELIEF: someone puts on a headset having been told it is passive, turns
   * their head to look around, and the robot turns. Every assertion below is
   * a pose that WOULD command something in a normal session.
   */

  test("a large head yaw commands nothing", () => {
    const f = buildFrame(
      input({ observeOnly: true, yawErrorRadians: BIG_YAW }),
    );
    expect(f.enabled).toBe(false);
    expect(f.head.yaw).toBe(0);
  });

  test("a held walk button commands nothing", () => {
    for (const dir of ["forward", "back"] as const) {
      const f = buildFrame(input({ observeOnly: true, walking: dir }));
      expect(f.enabled).toBe(false);
      expect(f.walk).toBe(0);
    }
  });

  test("requested arms are not mirrored", () => {
    const f = buildFrame(
      input({
        observeOnly: true,
        armsRequested: true,
        left: {
          position: [0.2, 1.3, -0.3],
          orientation: [0, 0, 0, 1],
          grip: 0.9,
        },
      }),
    );
    expect(f.arms).toBe(false);
    expect(f.hands.left?.tracked).not.toBe(true);
  });

  test("everything at once still yields exactly the idle payload", () => {
    // The worst case: an observer walking, turning and gesturing at once.
    const f = buildFrame(
      input({
        observeOnly: true,
        yawErrorRadians: BIG_YAW,
        walking: "forward",
        armsRequested: true,
        headPosition: [1, 2, 3],
      }),
    );
    expect(f).toEqual(idlePayload());
  });

  test("omitting the flag is unchanged behaviour, not silent passivity", () => {
    // Default-off matters as much as the flag working: every existing caller
    // omits it, and a frame that quietly stopped commanding would be a robot
    // that stopped responding with nothing to point at.
    const normal = buildFrame(input({ yawErrorRadians: BIG_YAW }));
    expect(normal.enabled).toBe(true);
    expect(normal.head.yaw).toBeCloseTo(BIG_YAW, 6);
    expect(
      buildFrame(input({ observeOnly: false, walking: "forward" })).walk,
    ).toBe(1);
  });
});
