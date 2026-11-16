import { describe, expect, test } from "bun:test";

import { validateArgs } from "./validate";

const FINGER = { type: "number", minimum: 0, maximum: 1 };
// set_hand's parameters as the bridge publishes them (2026-11-13).
const SET_HAND = {
  type: "object",
  properties: {
    side: { type: "string", enum: ["left", "right", "both"], default: "right" },
    closure: { type: "number", minimum: 0, maximum: 1, default: 0 },
    fingers: {
      anyOf: [
        {
          type: "object",
          properties: { thumb: FINGER, index: FINGER, pinky: FINGER },
          additionalProperties: false,
        },
        { type: "null" },
      ],
      default: null,
    },
  },
};

describe("validateArgs: numbers sent as strings", () => {
  test('{"fingers":{"index":"0"}} passes as 0 (what ia-tic sent live)', () => {
    const r = validateArgs(SET_HAND, {
      side: "right",
      closure: 1,
      fingers: { index: "0" },
    });
    expect(r.ok).toBe(true);
    expect(r.value.fingers).toEqual({ index: 0 });
  });

  test("a top-level numeric string becomes a number", () => {
    const r = validateArgs(SET_HAND, { closure: "0.3" });
    expect(r.ok).toBe(true);
    expect(r.value.closure).toBe(0.3);
  });

  test('a string that is not a number still fails: "abc"', () => {
    const r = validateArgs(SET_HAND, { fingers: { index: "abc" } });
    expect(r.ok).toBe(false);
  });

  test("null for a plain number field still fails (no null -> 0)", () => {
    const r = validateArgs(SET_HAND, { closure: null });
    expect(r.ok).toBe(false);
  });

  test("a numeric string for a string-typed field stays a string", () => {
    const schema = {
      type: "object",
      properties: { label: { type: "string" } },
    };
    const r = validateArgs(schema, { label: "7" });
    expect(r.ok).toBe(true);
    expect(r.value.label).toBe("7");
  });

  test("range is still checked after the conversion", () => {
    expect(validateArgs(SET_HAND, { fingers: { index: "1.5" } }).ok).toBe(
      false,
    );
  });
});
