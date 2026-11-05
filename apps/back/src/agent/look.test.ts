import { describe, expect, test } from "bun:test";
import { LOOK_TOOL, look, type LookDeps } from "./look";

const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);

function deps(overrides: Partial<LookDeps> = {}): LookDeps & {
  asked: string[];
} {
  const asked: string[] = [];
  return {
    asked,
    model: "tic-chat",
    cameraStatus: async () => ({
      ok: true,
      status: 200,
      body: { live: true, frame_age_s: 0.03, source: "vision" },
    }),
    cameraFrame: async () => JPEG,
    describe: async (question) => {
      asked.push(question);
      return "Una mochila negra sobre el piso.";
    },
    ...overrides,
  };
}

describe("look", () => {
  test("a live frame returns the model's answer with where it came from", async () => {
    const d = deps();
    const result = await look({ question: "¿Qué hay en el piso?" }, d);
    expect(result).toMatchObject({
      status: "ok",
      answer: "Una mochila negra sobre el piso.",
      question: "¿Qué hay en el piso?",
      frame_age_s: 0.03,
      source: "vision",
      model: "tic-chat",
    });
    expect(d.asked).toEqual(["¿Qué hay en el piso?"]);
  });

  test("no question asks for a general description", async () => {
    const d = deps();
    await look({}, d);
    await look({ question: "   " }, d);
    expect(d.asked).toHaveLength(2);
    expect(d.asked[0]).toContain("Describí");
    expect(d.asked[1]).toBe(d.asked[0]);
  });

  test("absent is not empty: no live picture never reaches the model", async () => {
    // A description of an empty room from a dead camera is how a robot walks
    // into something it never saw (D7).
    const d = deps({
      cameraStatus: async () => ({
        ok: true,
        status: 200,
        body: { live: false, hint: "somebody took the device" },
      }),
    });
    const result = await look({ question: "¿Hay alguien?" }, d);
    expect(result).toEqual({
      status: "unavailable",
      reason: "the camera has no live picture",
      hint: "somebody took the device",
    });
    expect(d.asked).toHaveLength(0);
  });

  test("the sim's 503 is passed through in the bridge's own words", async () => {
    const result = await look(
      {},
      deps({
        cameraStatus: async () => ({
          ok: false,
          status: 503,
          body: { error: "the head camera is real-hardware only" },
        }),
      }),
    );
    expect(result).toMatchObject({
      status: "unavailable",
      reason: "the head camera is real-hardware only",
    });
  });

  test("an unreachable bridge is unavailable, not a crash", async () => {
    const result = await look(
      {},
      deps({
        cameraStatus: async () => {
          throw new Error("ECONNREFUSED");
        },
      }),
    );
    expect(result.status).toBe("unavailable");
  });

  test("live but frameless is unavailable", async () => {
    const d = deps({ cameraFrame: async () => null });
    expect((await look({}, d)).status).toBe("unavailable");
    expect(d.asked).toHaveLength(0);
  });

  test("a failing or silent model is an error the agent can report", async () => {
    const failed = await look(
      {},
      deps({
        describe: async () => {
          throw new Error("Bad Gateway");
        },
      }),
    );
    expect(failed).toEqual({
      status: "error",
      error: "vision model failed: Bad Gateway",
    });
    const silent = await look({}, deps({ describe: async () => "  " }));
    expect(silent.status).toBe("error");
  });

  test("the tool tells the agent the answer is a reading, not a measurement", () => {
    expect(LOOK_TOOL.description).toContain("describe_surroundings");
    expect(LOOK_TOOL.description).toContain("unavailable");
  });
});
