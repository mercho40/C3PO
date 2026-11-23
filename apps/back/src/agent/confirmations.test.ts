/**
 * Spoken confirmation is a safety decision taken from the operator's own
 * transcript, never by the model: only a whole short phrase counts, only with
 * exactly one fresh refused call on screen.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import {
  PENDING_TTL_MS,
  createConfirmations,
  decideSpoken,
  normalizeUtterance,
  type ConfirmationOutcome,
  type PendingConfirmation,
} from "./confirmations";

let store = createConfirmations();

const call = (id: string, tool = "pick"): PendingConfirmation => ({
  id,
  tool,
  args: { label: "Dixie cup" },
  ownerId: "u1",
  chatId: "chat_1",
  loop: false,
  at: Date.now(),
});

describe("spoken «confirmo»", () => {
  test("accents, case and punctuation do not matter", () => {
    expect(normalizeUtterance("¡Sí, CONFIRMO!")).toBe("si confirmo");
    for (const said of [
      "Confirmo.",
      "Sí, confirmo",
      "confirmado",
      " sí confirmo… ",
    ])
      expect(decideSpoken(said, [call("c1")], "c1")).toMatchObject({
        kind: "confirm",
        pending: { id: "c1" },
      });
  });

  test("a sentence that merely contains it is not a confirmation", () => {
    for (const said of [
      "confirmo que el vaso está a la izquierda",
      "no sé si confirmo",
      "¿qué confirmo?",
      "confirmo confirmo",
      "agarrá el vaso, confirmo",
    ])
      expect(decideSpoken(said, [call("c1")], "c1")).toEqual({ kind: "none" });
  });

  test("with nothing pending it falls through to the model", () => {
    expect(decideSpoken("confirmo", [], "c1")).toEqual({ kind: "none" });
  });

  test("with two pending it asks which, and runs neither", () => {
    expect(
      decideSpoken("confirmo", [call("c1"), call("c2", "place")], "c2"),
    ).toEqual({
      kind: "ambiguous",
      count: 2,
    });
  });

  test("a pending call the screen does not show is never confirmed by voice", () => {
    expect(decideSpoken("confirmo", [call("c1")], undefined)).toEqual({
      kind: "none",
    });
    expect(decideSpoken("confirmo", [call("c1")], "c0")).toEqual({
      kind: "none",
    });
  });

  test("«cancelar» and «no» cancel it", () => {
    for (const said of ["Cancelar.", "no", "No confirmo"])
      expect(decideSpoken(said, [call("c1")], "c1")).toMatchObject({
        kind: "cancel",
      });
  });
});

describe("the pending list", () => {
  beforeEach(() => {
    store = createConfirmations();
  });

  test("is per owner and per chat, and forgets refusals past the window", () => {
    const { at: _, ...p } = call("c1");
    store.note(p);
    store.note({ ...p, id: "c2", ownerId: "u2" });
    store.note({ ...p, id: "c3", chatId: "chat_2", args: { label: "x" } });
    expect(store.list("u1", "chat_1").map((q) => q.id)).toEqual(["c1"]);
    expect(store.list("u1", "chat_1", Date.now() + PENDING_TTL_MS)).toEqual([]);
  });
});

describe("two admins' cards", () => {
  beforeEach(() => {
    store = createConfirmations();
  });

  // The call id is the model's: two turns can mint the same one.
  test("the same call id from two owners stays two cards, each its owner's", () => {
    const { at: _, ...p } = call("call_1");
    store.note(p);
    store.note({ ...p, ownerId: "u2" });
    expect(store.list("u1", "chat_1").map((q) => q.ownerId)).toEqual(["u1"]);
    expect(store.list("u2", "chat_1").map((q) => q.ownerId)).toEqual(["u2"]);
    // u2 takes and cancels only their own; u1's card is still there.
    expect(store.take("call_1", "u2", "pick", p.args)).toMatchObject({
      ownerId: "u2",
    });
    expect(store.take("call_1", "u2", "pick", p.args)).toBe("not_pending");
    expect(store.drop("call_1", "u2", "pick")).toBeNull();
    expect(store.list("u1", "chat_1").length).toBe(1);
    expect(store.take("call_1", "u1", "pick", p.args)).toMatchObject({
      ownerId: "u1",
    });
  });
});

describe("a failed grant puts the card back", () => {
  beforeEach(() => {
    store = createConfirmations();
  });

  test("but not after a PARAR cleared the cards while it was in flight", () => {
    const { at: _, ...p } = call("c1");
    store.note(p);
    const taken = store.take("c1", "u1", "pick", p.args);
    if (typeof taken === "string") throw new Error(taken);
    store.restore(taken);
    expect(store.list("u1", "chat_1").map((q) => q.id)).toEqual(["c1"]);

    const again = store.take("c1", "u1", "pick", p.args);
    if (typeof again === "string") throw new Error(again);
    store.clear();
    store.restore(again);
    expect(store.list("u1", "chat_1")).toEqual([]);
  });
});

describe("outcome subscribers", () => {
  test("every subscriber hears every outcome, until it unsubscribes", () => {
    const store = createConfirmations();
    const a: ConfirmationOutcome[] = [];
    const b: ConfirmationOutcome[] = [];
    const offA = store.subscribe((o) => a.push(o));
    store.subscribe((o) => b.push(o));
    const loop = { kind: "loop", message: "m", spoken: "s" } as const;
    store.publish(loop);
    offA();
    const chat = { kind: "chat", chatId: "chat_1", message: "m" } as const;
    store.publish(chat);
    expect(a).toEqual([loop]);
    expect(b).toEqual([loop, chat]);
  });

  test("two stores share nothing", () => {
    const one = createConfirmations();
    const two = createConfirmations();
    one.note({ ...call("c1"), at: undefined } as never);
    expect(two.list("u1", "chat_1")).toEqual([]);
    expect(one.list("u1", "chat_1").map((p) => p.id)).toEqual(["c1"]);
  });
});
