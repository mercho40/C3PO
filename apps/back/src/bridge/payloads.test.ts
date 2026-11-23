/**
 * contracts/ → TypeBox (bridge/payloads.ts `toTypeBox` / `fromContract`).
 *
 * The derivation is only worth having if it is faithful: every definition the
 * bridge exports converts, a keyword the converter does not understand fails
 * loudly instead of being ignored (an ignored keyword is a constraint silently
 * dropped), required stays required, and a field the bridge adds is neither
 * refused nor stripped.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Value } from "@sinclair/typebox/value";

import { SAFETY_STATUS, STATE } from "../testing/payloads";
import {
  RobotStateSchema,
  SafetyStatusSchema,
  fromContract,
  payloadCheck,
  toTypeBox,
} from "./payloads";

const CONTRACT = JSON.parse(
  readFileSync(
    join(
      import.meta.dir,
      "..",
      "..",
      "..",
      "..",
      "contracts",
      "bridge-payloads.schema.json",
    ),
    "utf8",
  ),
) as {
  $defs: Record<string, { required?: string[]; properties?: object }>;
};

describe("contracts → TypeBox", () => {
  test("every definition the bridge exports converts", () => {
    const names = Object.keys(CONTRACT.$defs);
    expect(names.length).toBeGreaterThan(0);
    for (const name of names)
      expect(() =>
        fromContract(name as keyof typeof CONTRACT.$defs & "RobotState"),
      ).not.toThrow();
  });

  test("a keyword it does not understand fails loudly", () => {
    expect(() => toTypeBox({ type: "string", minLength: 1 } as never)).toThrow(
      /unsupported keyword "minLength"/,
    );
    expect(() => toTypeBox({ $ref: "#/$defs/Nope" })).toThrow(/unresolved/);
  });

  test("required and optional are the contract's", () => {
    const schema = SafetyStatusSchema as unknown as {
      required?: string[];
      properties: object;
    };
    const def = CONTRACT.$defs.SafetyStatusBody!;
    expect([...(schema.required ?? [])].sort()).toEqual(
      [...def.required!].sort(),
    );
    expect(Object.keys(schema.properties).sort()).toEqual(
      Object.keys(def.properties!).sort(),
    );
  });

  test("the bridge's own answers pass", () => {
    expect(Value.Check(RobotStateSchema, STATE)).toBe(true);
    expect(Value.Check(SafetyStatusSchema, SAFETY_STATUS)).toBe(true);
    // `raw` is optional, nested refs are inlined.
    const withRaw = {
      ...STATE,
      raw: {
        tick: 1,
        mode_machine: 5,
        motor_count: 29,
        lowstate_messages_received: 10,
        pose_source: "sport",
        pose_messages_received: 3,
        fsm_id: null,
      },
    };
    expect(Value.Check(RobotStateSchema, withRaw)).toBe(true);
  });

  test("a field the bridge adds passes, at any depth", () => {
    const check = payloadCheck(SafetyStatusSchema);
    const r = check({
      ...SAFETY_STATUS,
      new_flag: true,
      caps: { ...SAFETY_STATUS.caps, jerk: 1 },
    });
    expect(r.ok).toBe(true);
  });

  test("a missing or retyped field names its path", () => {
    const check = payloadCheck(RobotStateSchema);
    const { posture: _, ...noPosture } = STATE;
    expect(check(noPosture)).toMatchObject({ ok: false, path: "/posture" });
    expect(
      check({ ...STATE, pose: { ...STATE.pose, x_meters_world: "1" } }),
    ).toMatchObject({ ok: false, path: "/pose/x_meters_world" });
    expect(check({ ...STATE, pose: null }).ok).toBe(true); // null until odometry
  });
});
