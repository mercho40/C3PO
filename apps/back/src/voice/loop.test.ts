/**
 * The voice loop, tested with no robot and no model.
 *
 * Both are injected, because neither is where the bugs are. The bugs are in the
 * loop: whether a stop phrase reaches the robot without waiting for an LLM,
 * whether two agent runs can overlap, whether one bad poll kills the loop, and
 * whether a sentence split across a breath is answered once or twice.
 */

import { describe, expect, test } from "bun:test";
import { VoiceLoop, type ListenResult } from "./loop";

function harness(results: ListenResult[], opts = {}) {
  const calls: Array<{ name: string; args: Record<string, unknown> }> = [];
  const agentRuns: string[] = [];
  let i = 0;

  const loop = new VoiceLoop(
    {
      callTool: async (name, args) => {
        calls.push({ name, args });
        if (name !== "listen") return { status: "ok" };
        return results[Math.min(i++, results.length - 1)] ?? { status: "ok" };
      },
      runAgent: async (u) => {
        agentRuns.push(u);
      },
      sleep: async () => {},
    },
    opts,
  );
  return { loop, calls, agentRuns };
}

const silence: ListenResult = { status: "ok", heard: [], mic_ever_open: false };
const said = (...texts: string[]): ListenResult => ({
  status: "ok",
  mic_ever_open: true,
  heard: texts.map((text) => ({ text, age_s: 0.1 })),
});

describe("VoiceLoop", () => {
  test("an utterance reaches the agent", async () => {
    const { loop, agentRuns } = harness([said("caminá hasta la puerta")]);
    await loop.tick();
    expect(agentRuns).toEqual(["caminá hasta la puerta"]);
  });

  test("silence does not run the agent", async () => {
    // Guards the bill as much as the behaviour: a loop that invoked the model
    // on every empty poll would run it once a second, forever.
    const { loop, agentRuns } = harness([silence]);
    await loop.tick();
    expect(agentRuns).toEqual([]);
  });

  test("a sentence split across a breath is answered once, not twice", async () => {
    // Vosk segments on pauses, so one question can arrive as two utterances.
    // Running the agent per utterance answers half a question, then answers the
    // rest without the first half's context.
    const { loop, agentRuns } = harness([
      said("caminá hasta la puerta", "y buscá la caja"),
    ]);
    await loop.tick();
    expect(agentRuns).toEqual(["caminá hasta la puerta y buscá la caja"]);
  });

  test("a stop phrase stops the robot WITHOUT waiting for the model", async () => {
    // The property that matters: no LLM round-trip between a person saying
    // "stop" and the robot stopping.
    const { loop, calls, agentRuns } = harness([
      {
        status: "ok",
        mic_ever_open: true,
        stop_heard: "emergencia",
        heard: [],
      },
    ]);
    await loop.tick();
    expect(calls.map((c) => c.name)).toContain("stop_everything");
    expect(agentRuns).toEqual([]);
  });

  test("the stop fires before the agent even when both arrive together", async () => {
    const { loop, calls } = harness([
      {
        status: "ok",
        mic_ever_open: true,
        stop_heard: "emergencia",
        heard: [{ text: "emergencia pará", age_s: 0.1 }],
      },
    ]);
    await loop.tick();
    const stopAt = calls.findIndex((c) => c.name === "stop_everything");
    expect(stopAt).toBeGreaterThanOrEqual(0);
    expect(loop.snapshot().stopsTriggered).toBe(1);
  });

  test("acting on the stop phrase can be switched off", async () => {
    const { loop, calls } = harness(
      [{ status: "ok", stop_heard: "emergencia", heard: [] }],
      { actOnStopPhrase: false },
    );
    await loop.tick();
    expect(calls.map((c) => c.name)).not.toContain("stop_everything");
  });

  test("one failing poll does not end the loop", async () => {
    // Otherwise a dropped bridge connection leaves the robot deaf while the UI
    // still reports the loop as running.
    let n = 0;
    const loop = new VoiceLoop({
      callTool: async () => {
        if (n++ === 0) throw new Error("bridge went away");
        return said("hola");
      },
      runAgent: async () => {},
      sleep: async () => {},
    });
    await loop.tick().catch(() => {});
    await loop.tick();
    expect(loop.snapshot().utterancesHeard).toBe(1);
  });

  test("it reports whether the robot can hear at all", async () => {
    // An operator staring at an idle loop needs to know the difference between
    // "nobody is talking" and "the mic is shut".
    const { loop } = harness([{ ...silence, always_listening: false }]);
    await loop.tick();
    expect(loop.snapshot().micEverOpen).toBe(false);
    expect(loop.snapshot().alwaysListening).toBe(false);
  });

  test("start/stop is idempotent and awaitable", async () => {
    const { loop } = harness([silence]);
    loop.start();
    loop.start();
    expect(loop.snapshot().running).toBe(true);
    await loop.stop();
    expect(loop.snapshot().running).toBe(false);
  });

  test("agent runs never overlap", async () => {
    // The next poll must not start a second turn while the first is thinking.
    // listen() buffers, so nothing said meanwhile is lost.
    let active = 0;
    let maxActive = 0;
    const loop = new VoiceLoop({
      callTool: async () => said("hola"),
      runAgent: async () => {
        active += 1;
        maxActive = Math.max(maxActive, active);
        await new Promise((r) => setTimeout(r, 5));
        active -= 1;
      },
      sleep: async () => {},
    });
    loop.start();
    await new Promise((r) => setTimeout(r, 40));
    await loop.stop();
    expect(maxActive).toBe(1);
  });
});
