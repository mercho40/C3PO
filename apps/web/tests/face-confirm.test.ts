/** The face instead of «Confirmar»: the shown card asks back until a face runs it. */
import { describe, expect, test } from "bun:test";
import { watchFace } from "../src/lib/chat/confirm";

const args = { seconds: 5 };

/** Back answering each poll with the next of `answers` (the last one repeats). */
function back(answers: [number, unknown][]) {
  const sent: string[] = [];
  const impl = (async (url: string) => {
    sent.push(url);
    const [status, body] = answers[Math.min(sent.length, answers.length) - 1]!;
    return Response.json(body, { status });
  }) as unknown as typeof fetch;
  return { sent, impl };
}

function watch(answers: [number, unknown][]) {
  const b = back(answers);
  const states: string[] = [];
  let resolve!: (r: unknown) => void;
  const done = new Promise((r) => (resolve = r));
  const stop = watchFace(
    "http://back",
    "start_walking_waist",
    "c1",
    args,
    { state: (s) => states.push(s), done: resolve },
    b.impl,
    1,
  );
  return { ...b, states, done, stop };
}

describe("watchFace", () => {
  test("waits while nobody is there, then relays the run a face confirmed", async () => {
    const w = watch([
      [200, { passed: false, reason: "operator_face_not_seen" }],
      [200, { passed: false, reason: "operator_face_not_seen" }],
      [
        200,
        {
          passed: true,
          person: "Oliver",
          result: { status: "completed" },
          message: "El operador confirmó y se ejecutó start_walking_waist.",
        },
      ],
    ]);
    const r = (await w.done) as { message: string; final: boolean };
    expect(w.sent.length).toBe(3);
    expect(w.sent[0]).toBe(
      "http://back/skills/start_walking_waist/confirm-face",
    );
    expect(w.states).toEqual(["waiting", "waiting"]);
    expect(r.final).toBe(true);
    expect(r.message.startsWith("El operador confirmó")).toBe(true);
  });

  test("a call the face cannot confirm (a T2) stops at once and never shows the hint", async () => {
    const w = watch([[400, { error: "face_not_for_t2" }]]);
    await Bun.sleep(20);
    expect(w.states).toEqual(["unavailable"]);
    expect(w.sent.length).toBe(1);
  });

  test("the stop ends the polling: a hidden card asks nothing more", async () => {
    const w = watch([[200, { passed: false }]]);
    await Bun.sleep(10);
    w.stop();
    const n = w.sent.length;
    await Bun.sleep(20);
    expect(w.sent.length).toBe(n);
  });
});
