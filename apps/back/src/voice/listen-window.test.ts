import { describe, expect, test } from "bun:test";
import { ListenWindow, type Greet } from "./listen-window";

const WINDOW = 20_000;

function harness(owner: string | null = "admin-1") {
  const s = {
    greet: { seq: 0, name: null, listen: true } as Greet | null,
    stoppedAt: 0 as number | null,
    running: false,
    startedFor: [] as string[],
    stopped: 0,
    activity: 0,
    stops: 0,
    busy: false,
    t: 0,
    events: [] as string[],
  };
  const w = new ListenWindow(
    {
      readGreet: async () => s.greet && { ...s.greet },
      readStoppedAt: async () => s.stoppedAt,
      loopRunning: () => s.running,
      startLoop: (o) => {
        s.running = true;
        s.startedFor.push(o);
      },
      stopLoop: async () => {
        s.running = false;
        s.stopped += 1;
      },
      activity: () => s.activity,
      stops: () => s.stops,
      busy: () => s.busy,
      now: () => s.t,
      log: (e, d) => s.events.push(`${e}:${String(d?.reason ?? "")}`),
    },
    WINDOW,
  );
  w.setOwner(owner);
  const greet = (name = "Oliver") => {
    s.greet = {
      seq: (s.greet?.seq ?? 0) + 1,
      name,
      listen: s.greet?.listen ?? true,
    };
  };
  return { s, w, greet };
}

describe("listen after greet", () => {
  test("an enrolled greeting opens the loop for the switch's owner", async () => {
    const { s, w, greet } = harness();
    await w.tick(); // baseline
    greet();
    await w.tick();
    expect(w.isOpen()).toBe(true);
    expect(s.startedFor).toEqual(["admin-1"]);
  });

  test("a greeting from before the first read opens nothing", async () => {
    const { s, w } = harness();
    s.greet = { seq: 7, name: "Oliver", listen: true };
    await w.tick();
    expect(w.isOpen()).toBe(false);
    expect(s.startedFor).toEqual([]);
  });

  test("no new seq (strangers never bump it) opens nothing; nor does the switch off", async () => {
    const { s, w, greet } = harness();
    await w.tick();
    await w.tick();
    expect(w.isOpen()).toBe(false);
    s.greet = { seq: 0, name: null, listen: false };
    greet();
    await w.tick();
    expect(w.isOpen()).toBe(false);
  });

  test("owner unknown (back restarted): no window, and it says why", async () => {
    const { s, w, greet } = harness(null);
    await w.tick();
    greet();
    await w.tick();
    expect(s.startedFor).toEqual([]);
    expect(s.events.some((e) => e.includes("owner_unknown"))).toBe(true);
  });

  test("a session someone else started is neither joined nor stopped", async () => {
    const { s, w, greet } = harness();
    s.running = true;
    await w.tick();
    greet();
    await w.tick();
    s.t += WINDOW * 3;
    await w.tick();
    expect(w.isOpen()).toBe(false);
    expect(s.startedFor).toEqual([]);
    expect(s.stopped).toBe(0);
    expect(s.running).toBe(true);
  });

  test("closes after the idle window, extended by each utterance and by a running turn", async () => {
    const { s, w, greet } = harness();
    await w.tick();
    greet();
    await w.tick();
    s.t += WINDOW - 1000;
    s.activity += 1; // heard something
    await w.tick();
    s.t += WINDOW - 1000;
    s.busy = true; // the agent is still answering
    await w.tick();
    s.busy = false;
    s.t += WINDOW - 1000;
    await w.tick();
    expect(w.isOpen()).toBe(true);
    s.t += 1000;
    await w.tick();
    expect(w.isOpen()).toBe(false);
    expect(s.stopped).toBe(1);
    expect(s.events[s.events.length - 1]).toBe(
      "voice.listen_window.close:idle",
    );
  });

  test("a PARAR closes it at once: the bridge latch, or the loop's own stop phrase", async () => {
    for (const stop of ["latch", "phrase"] as const) {
      const { s, w, greet } = harness();
      await w.tick();
      greet();
      await w.tick();
      if (stop === "latch") s.stoppedAt = 123;
      else s.stops += 1;
      await w.tick();
      expect(w.isOpen()).toBe(false);
      expect(s.stopped).toBe(1);
      expect(s.events[s.events.length - 1]).toBe(
        "voice.listen_window.close:stop",
      );
    }
  });

  test("turning the switch off closes an open window", async () => {
    const { s, w, greet } = harness();
    await w.tick();
    greet();
    await w.tick();
    w.setOwner(null);
    await w.tick();
    expect(w.isOpen()).toBe(false);
    expect(s.stopped).toBe(1);
  });

  test("a loop ended elsewhere (/voice/stop) is not stopped again", async () => {
    const { s, w, greet } = harness();
    await w.tick();
    greet();
    await w.tick();
    s.running = false;
    await w.tick();
    expect(w.isOpen()).toBe(false);
    expect(s.stopped).toBe(0);
  });
});
