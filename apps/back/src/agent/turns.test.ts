import { describe, expect, test } from "bun:test";
import { createConfirmations } from "./confirmations";
import { createTurns } from "./turns";

const newTurns = () => createTurns(createConfirmations());

describe("agent turn registry (PARAR ends the planning)", () => {
  test("abortAll aborts every registered turn and clears them", () => {
    const turns = newTurns();
    const a = turns.beginTurn();
    const b = turns.beginTurn();
    expect(turns.activeCount()).toBe(2);
    expect(turns.abortAll("PARAR")).toBe(2);
    expect(a.signal.aborted && b.signal.aborted).toBe(true);
    expect(turns.activeCount()).toBe(0);
  });

  test("the browser's own abort still ends the turn, and done unregisters it", () => {
    const turns = newTurns();
    const outer = new AbortController();
    const t = turns.beginTurn(outer.signal);
    outer.abort();
    expect(t.signal.aborted).toBe(true);
    t.done();
    expect(turns.activeCount()).toBe(0);
  });

  test("PARAR drops every confirmation card of the store it was given", () => {
    const confirmations = createConfirmations();
    const turns = createTurns(confirmations);
    confirmations.note({
      id: "c1",
      tool: "pick",
      args: {},
      ownerId: "u1",
      chatId: "chat_1",
      loop: false,
    });
    turns.abortAll();
    expect(confirmations.list("u1", "chat_1")).toEqual([]);
  });

  test("first-turn listeners are a set: each hears none → one, until it unsubscribes", () => {
    const turns = newTurns();
    let a = 0;
    let b = 0;
    const offA = turns.onFirstTurn(() => a++);
    turns.onFirstTurn(() => b++);
    turns.beginTurn().done();
    offA();
    turns.beginTurn();
    turns.beginTurn(); // one → two: nobody hears it
    expect([a, b]).toEqual([1, 2]);
  });
});
