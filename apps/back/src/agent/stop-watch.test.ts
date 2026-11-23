import { beforeEach, describe, expect, test } from "bun:test";
import { createConfirmations } from "./confirmations";
import { createTurns } from "./turns";
import {
  readStoppedAt,
  startStopWatch,
  watchWhileTurnsRun,
} from "./stop-watch";
import type { BridgeHttp } from "../bridge/http";

/** A latch reader that returns the given stamps in order, then repeats the last. */
function stamps(...values: (number | null)[]) {
  let i = 0;
  return async () => values[Math.min(i++, values.length - 1)] ?? null;
}

let turns = createTurns(createConfirmations());
const beginTurn = () => turns.beginTurn();
beforeEach(() => {
  turns = createTurns(createConfirmations());
});

describe("a PARAR latched on the bridge directly ends agent turns", () => {
  test("a new stop stamp aborts every turn in flight", async () => {
    const turn = beginTurn();
    await watchWhileTurnsRun(turns, stamps(100, 100, 205), 1);
    expect(turn.signal.aborted).toBe(true);
    expect(String((turn.signal.reason as Error).message)).toContain("bridge");
  });

  test("a stop that was already standing when the turn began does not abort it", async () => {
    const turn = beginTurn();
    let reads = 0;
    const watch = watchWhileTurnsRun(
      turns,
      async () => {
        if (++reads === 4) turn.done();
        return 100;
      },
      1,
    );
    await watch;
    expect(turn.signal.aborted).toBe(false);
  });

  test("an unreachable bridge is not a stop, and the first good read is the baseline", async () => {
    const turn = beginTurn();
    let reads = 0;
    const values = [null, null, 300, 300];
    await watchWhileTurnsRun(
      turns,
      async () => {
        const v = values[reads] ?? 300;
        if (++reads === values.length) turn.done();
        return v;
      },
      1,
    );
    expect(turn.signal.aborted).toBe(false);
  });

  test("a stop during the outage is caught once the bridge answers again", async () => {
    const turn = beginTurn();
    await watchWhileTurnsRun(turns, stamps(100, null, 140), 1);
    expect(turn.signal.aborted).toBe(true);
  });

  test("the first turn of an idle server starts the watch", async () => {
    startStopWatch(turns, stamps(0, 0, 50), 1);
    const turn = beginTurn();
    for (let i = 0; i < 50 && !turn.signal.aborted; i++) {
      await new Promise((r) => setTimeout(r, 2));
    }
    expect(turn.signal.aborted).toBe(true);
  });
});

describe("readStoppedAt", () => {
  const status = (
    r: Awaited<ReturnType<BridgeHttp["safetyStatus"]>> | Error,
  ) => ({
    safetyStatus: async (timeoutMs: number) => {
      expect(timeoutMs).toBe(1000);
      if (r instanceof Error) throw r;
      return r;
    },
  });

  test("the stamp, 0 for a bridge that never stopped, null when unreadable", async () => {
    expect(
      await readStoppedAt(
        status({ ok: true, status: 200, body: { stopped_at: 42 } }),
      ),
    ).toBe(42);
    expect(
      await readStoppedAt(
        status({ ok: true, status: 200, body: { stopped_at: null } }),
      ),
    ).toBe(0);
    expect(
      await readStoppedAt(status({ ok: false, status: 500, body: {} })),
    ).toBeNull();
    expect(
      await readStoppedAt(status({ ok: true, status: 200, body: undefined })),
    ).toBeNull();
    expect(await readStoppedAt(status(new Error("down")))).toBeNull();
  });
});
