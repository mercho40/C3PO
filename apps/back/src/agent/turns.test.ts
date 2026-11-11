import { describe, expect, test } from "bun:test";
import { abortAllTurns, activeTurnCount, beginTurn } from "./turns";

describe("agent turn registry (PARAR ends the planning)", () => {
  test("abortAllTurns aborts every registered turn and clears them", () => {
    const a = beginTurn();
    const b = beginTurn();
    expect(activeTurnCount()).toBe(2);
    expect(abortAllTurns("PARAR")).toBe(2);
    expect(a.signal.aborted && b.signal.aborted).toBe(true);
    expect(activeTurnCount()).toBe(0);
  });

  test("the browser's own abort still ends the turn, and done unregisters it", () => {
    const outer = new AbortController();
    const t = beginTurn(outer.signal);
    outer.abort();
    expect(t.signal.aborted).toBe(true);
    t.done();
    expect(activeTurnCount()).toBe(0);
  });
});
