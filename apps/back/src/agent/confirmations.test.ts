/**
 * Spoken confirmation is a safety decision taken from the operator's own
 * transcript, never by the model: only a whole short phrase counts, only with
 * exactly one fresh refused call on screen.
 */
import { beforeEach, describe, expect, test } from "bun:test";
import {
  PENDING_TTL_MS,
  clearPending,
  decideSpoken,
  dropPending,
  listPending,
  normalizeUtterance,
  notePending,
  takePending,
  type PendingConfirmation,
} from "./confirmations";

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
  beforeEach(() => clearPending());

  test("is per owner and per chat, and forgets refusals past the window", () => {
    const { at: _, ...p } = call("c1");
    notePending(p);
    notePending({ ...p, id: "c2", ownerId: "u2" });
    notePending({ ...p, id: "c3", chatId: "chat_2", args: { label: "x" } });
    expect(listPending("u1", "chat_1").map((q) => q.id)).toEqual(["c1"]);
    expect(listPending("u1", "chat_1", Date.now() + PENDING_TTL_MS)).toEqual(
      [],
    );
  });
});

describe("two admins' cards", () => {
  beforeEach(() => clearPending());

  // The call id is the model's: two turns can mint the same one.
  test("the same call id from two owners stays two cards, each its owner's", () => {
    const { at: _, ...p } = call("call_1");
    notePending(p);
    notePending({ ...p, ownerId: "u2" });
    expect(listPending("u1", "chat_1").map((q) => q.ownerId)).toEqual(["u1"]);
    expect(listPending("u2", "chat_1").map((q) => q.ownerId)).toEqual(["u2"]);
    // u2 takes and cancels only their own; u1's card is still there.
    expect(takePending("call_1", "u2", "pick", p.args)).toMatchObject({
      ownerId: "u2",
    });
    expect(takePending("call_1", "u2", "pick", p.args)).toBe("not_pending");
    expect(dropPending("call_1", "u2", "pick")).toBeNull();
    expect(listPending("u1", "chat_1").length).toBe(1);
    expect(takePending("call_1", "u1", "pick", p.args)).toMatchObject({
      ownerId: "u1",
    });
  });
});
