import { describe, expect, test } from "bun:test";
import { LOOK_TOOL, look, verdict, verify, type LookDeps } from "./look";

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

  test("a frame the bridge could not blur is unavailable with its reason", async () => {
    const d = deps({
      cameraFrame: async () => {
        throw new Error("OpenCV face cascades did not load");
      },
    });
    expect(await look({}, d)).toMatchObject({
      status: "unavailable",
      reason: "OpenCV face cascades did not load",
    });
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

describe("verify", () => {
  test("the model's first word is the verdict; the rest is the reason", () => {
    expect(verdict("Sí, la mano derecha sostiene la taza.")).toEqual({
      answer: "yes",
      reason: "la mano derecha sostiene la taza.",
    });
    expect(verdict("No. La mano está vacía.").answer).toBe("no");
    expect(verdict("«No sé»: la mano queda fuera de la imagen.").answer).toBe(
      "unsure",
    );
    expect(verdict("No se ve la mano.").answer).toBe("unsure");
    // Not readable as yes or no: never a guessed yes.
    expect(verdict("Parece que quizás sí").answer).toBe("unsure");
    expect(verdict("Nosotros vemos una taza").answer).toBe("unsure");
  });

  test("it asks the vision model in a fixed shape and returns the verdict", async () => {
    const d = deps({
      describe: async (question) => {
        d.asked.push(question);
        return "No — la taza sigue sobre la mesa.";
      },
    });
    const r = await verify({ question: "¿La mano tiene la taza?" }, d);
    expect(r.status).toBe("ok");
    if (r.status !== "ok") return;
    expect(r.answer).toBe("no");
    expect(r.reason).toBe("la taza sigue sobre la mesa.");
    expect(r.question).toBe("¿La mano tiene la taza?");
    expect(d.asked[0].startsWith("¿La mano tiene la taza?")).toBe(true);
    expect(d.asked[0]).toContain("«no sé»");
  });

  test("no picture is unavailable, not a no; no question is an error", async () => {
    const dark = deps({
      cameraStatus: async () => ({
        ok: true,
        status: 200,
        body: { live: false },
      }),
    });
    expect((await verify({ question: "¿Hay una taza?" }, dark)).status).toBe(
      "unavailable",
    );
    expect((await verify({}, deps())).status).toBe("error");
  });
});
