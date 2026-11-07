import { afterEach, describe, expect, test } from "bun:test";
import { abortAllTurns, beginTurn, setOnFirstTurn } from "./turns";
import { startStopWatch, watchWhileTurnsRun } from "./stop-watch";

/** A latch reader that returns the given stamps in order, then repeats the last. */
function stamps(...values: (number | null)[]) {
  let i = 0;
  return async () => values[Math.min(i++, values.length - 1)] ?? null;
}

afterEach(() => {
  abortAllTurns("test cleanup");
  setOnFirstTurn(null);
});

describe("a PARAR latched on the bridge directly ends agent turns", () => {
  test("a new stop stamp aborts every turn in flight", async () => {
    const turn = beginTurn();
    await watchWhileTurnsRun(stamps(100, 100, 205), 1);
    expect(turn.signal.aborted).toBe(true);
    expect(String((turn.signal.reason as Error).message)).toContain("bridge");
  });

  test("a stop that was already standing when the turn began does not abort it", async () => {
    const turn = beginTurn();
    let reads = 0;
    const watch = watchWhileTurnsRun(async () => {
      if (++reads === 4) turn.done();
      return 100;
    }, 1);
    await watch;
    expect(turn.signal.aborted).toBe(false);
  });

  test("an unreachable bridge is not a stop, and the first good read is the baseline", async () => {
    const turn = beginTurn();
    let reads = 0;
    const values = [null, null, 300, 300];
    await watchWhileTurnsRun(async () => {
      const v = values[reads] ?? 300;
      if (++reads === values.length) turn.done();
      return v;
    }, 1);
    expect(turn.signal.aborted).toBe(false);
  });

  test("a stop during the outage is caught once the bridge answers again", async () => {
    const turn = beginTurn();
    await watchWhileTurnsRun(stamps(100, null, 140), 1);
    expect(turn.signal.aborted).toBe(true);
  });

  test("the first turn of an idle server starts the watch", async () => {
    startStopWatch(stamps(0, 0, 50), 1);
    const turn = beginTurn();
    for (let i = 0; i < 50 && !turn.signal.aborted; i++) {
      await new Promise((r) => setTimeout(r, 2));
    }
    expect(turn.signal.aborted).toBe(true);
  });
});
