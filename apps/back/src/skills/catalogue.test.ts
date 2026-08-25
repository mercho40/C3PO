/**
 * Tests that one unreadable tool cannot take the whole catalogue with it.
 *
 * `parseSkillMeta` throws on malformed `_meta`, deliberately: a tool whose
 * danger level we cannot read must not be served with a guessed one. But that
 * rejection used to run inside a single `tools.map()`, so ONE bad tool aborted
 * the batch, the catch discarded every skill, and with nothing cached
 * `getSkill("stop_everything")` returned undefined — the dashboard's PARAR
 * button answering 404 while the bridge and the e-stop were both healthy.
 * Adding a tool (or a rolling restart with version skew) is enough to trigger
 * it, which is exactly when you least want the stop button gone.
 */
import { describe, expect, test, mock, beforeEach } from "bun:test";

let toolsToReturn: unknown[] = [];

mock.module("@back/bridge/client", () => ({
  listTools: async () => toolsToReturn,
  callTool: async () => ({}),
  BridgeUnavailableError: class extends Error {},
  BridgeToolError: class extends Error {},
}));

const goodMeta = (name: string, classification = "gesture") => ({
  name,
  description: `${name} description`,
  inputSchema: { type: "object", properties: {} },
  _meta: {
    c3po: {
      classification,
      danger_level: "low",
      status: "real",
      cancellable: false,
      expected_duration_s: 1,
      works: { sim: false, real: false },
      preconditions: [],
      typical_failure_modes: [],
    },
  },
});

describe("catalogue resilience", () => {
  beforeEach(() => {
    toolsToReturn = [];
  });

  test("a tool with malformed _meta is dropped, not fatal", async () => {
    const { getCatalogue } = await import("./catalogue");
    toolsToReturn = [
      goodMeta("wave"),
      { name: "broken", description: "no meta at all", inputSchema: {} },
      goodMeta("stop_everything", "safety"),
    ];

    const snap = await getCatalogue();

    expect(snap.source).toBe("bridge");
    expect(snap.rejected).toEqual(["broken"]);
    const names = snap.skills.map((s) => s.name);
    expect(names).toContain("wave");
    expect(names).toContain("stop_everything");
    expect(names).not.toContain("broken");
  });

  test("the e-stop stays resolvable when a sibling tool is malformed", async () => {
    const { getSkill } = await import("./catalogue");
    toolsToReturn = [
      {
        name: "broken",
        description: "malformed",
        inputSchema: {},
        _meta: { c3po: {} },
      },
      goodMeta("stop_everything", "safety"),
    ];

    // This is the exact lookup POST /skills/stop_everything/invoke makes.
    const estop = await getSkill("stop_everything");
    expect(estop).toBeDefined();
    expect(estop?.classification).toBe("safety");
  });

  test("a malformed tool is excluded rather than given a guessed danger level", async () => {
    const { getCatalogue } = await import("./catalogue");
    toolsToReturn = [
      {
        name: "sketchy",
        description: "bad meta",
        inputSchema: {},
        _meta: { c3po: { classification: "gesture" } },
      },
    ];

    const snap = await getCatalogue();

    // The point of throwing in parseSkillMeta is to never invent "low" for
    // something that can move a humanoid. Dropping it preserves that.
    expect(snap.skills.map((s) => s.name)).not.toContain("sketchy");
    expect(snap.rejected).toEqual(["sketchy"]);
  });
});
