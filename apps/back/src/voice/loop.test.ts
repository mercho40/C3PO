/**
 * The voice loop, tested with no robot and no model.
 *
 * Both are injected, because neither is where the bugs are. The bugs are in the
 * loop: whether a stop phrase reaches the robot without waiting for an LLM,
 * whether two agent runs can overlap, whether one bad poll kills the loop, and
 * whether a sentence split across a breath is answered once or twice.
 */

import { describe, expect, test } from "bun:test";
import { abortAllTurns } from "../agent/turns";
import { VoiceConversation } from "./conversation";
import {
  VoiceLoop,
  type ListenResult,
  type VoiceInputEvent,
  type VoiceLoopDeps,
} from "./loop";

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

  test("event input reaches the agent without polling listen", async () => {
    const calls: string[] = [];
    const heard: string[] = [];
    const loop = new VoiceLoop({
      callTool: async (name) => {
        calls.push(name);
        return { status: "ok" };
      },
      runAgent: async (text) => {
        heard.push(text);
      },
      inputStatus: async () => ({
        mic_ever_open: true,
        always_listening: false,
      }),
      events: async function* (signal) {
        yield { seq: 7, kind: "speech", text: "hola C3PO" };
        await new Promise<void>((resolve) =>
          signal.addEventListener("abort", () => resolve(), { once: true }),
        );
      },
      sleep: async () => {},
    });
    loop.start();
    await new Promise((resolve) => setTimeout(resolve, 5));
    await loop.stop();
    expect(heard).toEqual(["hola C3PO"]);
    expect(calls).not.toContain("listen");
    expect(loop.snapshot().micEverOpen).toBe(true);
  });

  test("stop events bypass the agent on the event-driven path", async () => {
    const calls: string[] = [];
    const heard: string[] = [];
    const loop = new VoiceLoop({
      callTool: async (name) => {
        calls.push(name);
        return { status: "ok" };
      },
      runAgent: async (text) => {
        heard.push(text);
      },
      events: async function* (signal) {
        yield { seq: 8, kind: "stop", text: "emergencia" };
        await new Promise<void>((resolve) =>
          signal.addEventListener("abort", () => resolve(), { once: true }),
        );
      },
      sleep: async () => {},
    });
    loop.start();
    await new Promise((resolve) => setTimeout(resolve, 5));
    await loop.stop();
    expect(calls).toContain("stop_everything");
    expect(heard).toEqual([]);
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

async function until(cond: () => boolean, ms = 500): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, 1));
  }
}

/** An event stream the test feeds, and a runAgent that blocks until released. */
function eventHarness(
  opts: {
    stopFails?: boolean;
    agentFails?: boolean;
    runAgent?: VoiceLoopDeps["runAgent"];
    transcribeAudio?: VoiceLoopDeps["transcribeAudio"];
    staleAudioMs?: number;
  } = {},
) {
  const queue: VoiceInputEvent[] = [];
  let wake: (() => void) | null = null;
  let seq = 0;
  const calls: string[] = [];
  const logs: string[] = [];
  const runs: Array<{
    text: string;
    signal?: AbortSignal;
    release: () => void;
  }> = [];
  let active = 0;
  let maxActive = 0;
  let connects = 0;

  const loop = new VoiceLoop(
    {
      callTool: async (name) => {
        calls.push(name);
        if (name === "stop_everything" && opts.stopFails)
          throw new Error("bridge went away");
        return { status: "ok" };
      },
      runAgent:
        opts.runAgent ??
        (async (text, signal) => {
          active += 1;
          maxActive = Math.max(maxActive, active);
          try {
            await new Promise<void>((release) => {
              runs.push({ text, signal, release });
            });
            if (opts.agentFails) throw new Error("model error");
          } finally {
            active -= 1;
          }
        }),
      events: async function* (signal) {
        connects += 1;
        while (!signal.aborted) {
          const next = queue.shift();
          if (next) {
            yield next;
            continue;
          }
          await new Promise<void>((r) => {
            wake = r;
            signal.addEventListener("abort", () => r(), { once: true });
          });
        }
      },
      transcribeAudio: opts.transcribeAudio,
      log: (event) => logs.push(event),
      sleep: async () => {},
    },
    { staleAudioMs: opts.staleAudioMs },
  );

  const push = (kind: VoiceInputEvent["kind"], text: string) => {
    queue.push(
      kind === "audio"
        ? { seq: ++seq, kind, text: "", audio_id: text }
        : { seq: ++seq, kind, text },
    );
    wake?.();
  };
  return {
    loop,
    calls,
    logs,
    runs,
    push,
    texts: () => runs.map((r) => r.text),
    maxActive: () => maxActive,
    connects: () => connects,
  };
}

describe("VoiceLoop event path", () => {
  test("a stop during a turn stops the robot and aborts the turn, with no wait", async () => {
    // runAgent never resolves by itself: before, the pump awaited it and the
    // stop event behind it was never read.
    const h = eventHarness();
    h.loop.start();
    h.push("speech", "caminá hasta la puerta");
    await until(() => h.runs.length === 1);
    h.push("stop", "emergencia");
    await until(() => h.calls.includes("stop_everything"));
    expect(h.runs[0]!.signal?.aborted).toBe(true);
    expect(h.loop.snapshot().stopsTriggered).toBe(1);
    h.runs[0]!.release();
    await h.loop.stop();
  });

  test("PARAR from anywhere aborts the signal the voice turn runs with", async () => {
    // The console's and headset's PARAR reach abortAllTurns, not this loop.
    const h = eventHarness();
    h.loop.start();
    h.push("speech", "contame un cuento");
    await until(() => h.runs.length === 1);
    abortAllTurns("PARAR (console)");
    expect(h.runs[0]!.signal?.aborted).toBe(true);
    h.runs[0]!.release();
    await h.loop.stop();
  });

  test("speech heard before a stop is not run after it", async () => {
    const h = eventHarness();
    h.loop.start();
    h.push("speech", "caminá");
    await until(() => h.runs.length === 1);
    h.push("speech", "y después girá");
    h.push("stop", "emergencia");
    await until(() => h.calls.includes("stop_everything"));
    h.runs[0]!.release();
    await new Promise((r) => setTimeout(r, 10));
    expect(h.texts()).toEqual(["caminá"]);
    await h.loop.stop();
  });

  test("turns never overlap, and speech during a turn is answered after it", async () => {
    const h = eventHarness();
    h.loop.start();
    h.push("speech", "hola");
    await until(() => h.runs.length === 1);
    h.push("speech", "¿cómo estás?");
    await until(() => h.loop.snapshot().utterancesHeard === 2);
    await new Promise((r) => setTimeout(r, 10));
    expect(h.runs.length).toBe(1);
    h.runs[0]!.release();
    await until(() => h.runs.length === 2);
    h.runs[1]!.release();
    expect(h.texts()).toEqual(["hola", "¿cómo estás?"]);
    expect(h.maxActive()).toBe(1);
    await h.loop.stop();
  });

  test("speech queued during a turn is joined into one turn", async () => {
    // Same reason as the polling path: Vosk splits one sentence on a breath.
    const h = eventHarness();
    h.loop.start();
    h.push("speech", "hola");
    await until(() => h.runs.length === 1);
    h.push("speech", "caminá hasta la puerta");
    h.push("speech", "y buscá la caja");
    await until(() => h.loop.snapshot().utterancesHeard === 3);
    h.runs[0]!.release();
    await until(() => h.runs.length === 2);
    h.runs[1]!.release();
    expect(h.texts()).toEqual([
      "hola",
      "caminá hasta la puerta y buscá la caja",
    ]);
    await h.loop.stop();
  });

  test("the stop phrase transcribed as speech does not start a turn", async () => {
    const h = eventHarness();
    h.loop.start();
    h.push("stop", "para para");
    h.push("speech", "¡Pará, para!");
    h.push("speech", "hola");
    await until(() => h.runs.length === 1);
    expect(h.texts()).toEqual(["hola"]);
    expect(h.logs).toContain("voice.stop_echo_dropped");
    h.runs[0]!.release();
    await h.loop.stop();
  });

  test("stop() resolves while a turn is still in flight", async () => {
    const h = eventHarness();
    h.loop.start();
    h.push("speech", "caminá");
    await until(() => h.runs.length === 1);
    const stopped = await Promise.race([
      h.loop.stop().then(() => true),
      new Promise((r) => setTimeout(() => r(false), 200)),
    ]);
    expect(stopped).toBe(true);
    expect(h.runs[0]!.signal?.aborted).toBe(true);
    expect(h.loop.snapshot().running).toBe(false);
    h.runs[0]!.release();
  });

  test("a restarted loop does not run a turn beside the aborted one's walk_to", async () => {
    // The real runAgent: an abort ends the model's text at once (ai 7's
    // streamText), while the walk_to it sent keeps driving until the bridge answers.
    let walkDone!: () => void;
    const walk = new Promise<void>((r) => (walkDone = r));
    let models = 0;
    const conversation = new VoiceConversation({
      complete: (_messages, signal) => {
        if (++models > 1) return "Hola.";
        const textStream = (async function* () {
          await new Promise((r) =>
            signal?.addEventListener("abort", r, { once: true }),
          );
        })();
        return { textStream, toolsSettled: () => walk };
      },
      speak: async () => {},
      newId: () => crypto.randomUUID(),
    });
    const h = eventHarness({
      runAgent: async (u, s) => void (await conversation.turn(u, s)),
    });
    h.loop.start();
    h.push("speech", "caminá");
    await until(() => models === 1);
    await h.loop.stop();
    h.loop.start();
    h.push("speech", "hola");
    await until(() => h.loop.snapshot().utterancesHeard === 2);
    await new Promise((r) => setTimeout(r, 20));
    expect(models).toBe(1);
    walkDone();
    await until(() => models === 2);
    expect(h.logs).toContain("voice.turn_aborted");
    await h.loop.stop();
  });

  test("an aborted turn is not an error and does not hide a failed stop", async () => {
    // Still inside runAgentChat's bridge calls, which take no signal: the
    // turn's rejection lands after stop_everything has already failed.
    const h = eventHarness({
      stopFails: true,
      runAgent: (_u, signal) =>
        new Promise((_, reject) =>
          signal?.addEventListener(
            "abort",
            () => setTimeout(() => reject(signal.reason), 5),
            { once: true },
          ),
        ),
    });
    h.loop.start();
    h.push("speech", "caminá");
    await until(() => h.loop.snapshot().agentRuns === 1);
    h.push("stop", "emergencia");
    await until(() => h.logs.includes("voice.turn_aborted"));
    expect(h.logs).not.toContain("voice.turn_failed");
    expect(h.loop.snapshot().lastError).toBe("bridge went away");
    await h.loop.stop();
  });

  test("a failing turn is recorded and does not reconnect the stream", async () => {
    const h = eventHarness({ agentFails: true });
    h.loop.start();
    h.push("speech", "hola");
    await until(() => h.runs.length === 1);
    h.runs[0]!.release();
    await until(() => h.logs.includes("voice.turn_failed"));
    expect(h.loop.snapshot().lastError).toBe("model error");
    h.push("speech", "¿seguís ahí?");
    await until(() => h.runs.length === 2);
    h.runs[1]!.release();
    expect(h.connects()).toBe(1);
    await h.loop.stop();
  });

  test("a failing stop_everything is recorded and does not reconnect the stream", async () => {
    const h = eventHarness({ stopFails: true });
    h.loop.start();
    h.push("stop", "emergencia");
    await until(() => h.logs.includes("voice.stop_failed"));
    expect(h.loop.snapshot().lastError).toBe("bridge went away");
    h.push("speech", "hola");
    await until(() => h.runs.length === 1);
    h.runs[0]!.release();
    expect(h.connects()).toBe(1);
    await h.loop.stop();
  });

  test("robot-mic audio is transcribed and runs the agent with the text", async () => {
    const asked: string[] = [];
    const h = eventHarness({
      transcribeAudio: async (id) => {
        asked.push(id);
        return "caminá hasta la puerta";
      },
    });
    h.loop.start();
    h.push("audio", "ab12");
    await until(() => h.runs.length === 1);
    expect(asked).toEqual(["ab12"]);
    expect(h.texts()).toEqual(["caminá hasta la puerta"]);
    expect(h.logs).toContain("voice.utterance");
    h.runs[0]!.release();
    await h.loop.stop();
  });

  test("a failed transcription is recorded and runs no agent", async () => {
    const h = eventHarness({
      transcribeAudio: async () => {
        throw new Error("transcription_unavailable");
      },
    });
    h.loop.start();
    h.push("audio", "ab12");
    await until(() => h.logs.includes("voice.transcribe_failed"));
    expect(h.loop.snapshot().lastError).toBe("transcription_unavailable");
    await new Promise((r) => setTimeout(r, 10));
    expect(h.runs).toEqual([]);
    await h.loop.stop();
  });

  test("a stop while transcribing drops the utterance and is not delayed", async () => {
    let finish: (text: string) => void = () => {};
    const h = eventHarness({
      transcribeAudio: () => new Promise((r) => (finish = r)),
    });
    h.loop.start();
    h.push("audio", "ab12");
    h.push("stop", "emergencia");
    await until(() => h.calls.includes("stop_everything"));
    finish("caminá");
    await until(() => h.logs.includes("voice.dropped_after_stop"));
    expect(h.runs).toEqual([]);
    await h.loop.stop();
  });

  test("a transcription from before stop() does not run after start()", async () => {
    let finish: (text: string) => void = () => {};
    const h = eventHarness({
      transcribeAudio: () => new Promise((r) => (finish = r)),
    });
    h.loop.start();
    h.push("audio", "ab12");
    await new Promise((r) => setTimeout(r, 10));
    await h.loop.stop();
    h.loop.start();
    finish("caminá hasta la puerta");
    await new Promise((r) => setTimeout(r, 20));
    expect(h.runs).toEqual([]);
    expect(h.logs).not.toContain("voice.utterance");
    await h.loop.stop();
  });

  test("audio left waiting behind a slow transcription is dropped as stale", async () => {
    let finishFirst: (text: string) => void = () => {};
    const asked: string[] = [];
    const h = eventHarness({
      staleAudioMs: 20,
      transcribeAudio: (id) => {
        asked.push(id);
        return id === "slow"
          ? new Promise((r) => (finishFirst = r))
          : Promise.resolve("andá a la cocina");
      },
    });
    h.loop.start();
    h.push("audio", "slow");
    h.push("audio", "late");
    await new Promise((r) => setTimeout(r, 50));
    finishFirst("hola");
    await until(() => h.logs.includes("voice.dropped_stale"));
    await until(() => h.runs.length === 1);
    expect(asked).toEqual(["slow"]);
    expect(h.texts()).toEqual(["hola"]);
    h.runs[0]!.release();
    await h.loop.stop();
  });

  test("slow first, fast second keeps spoken order", async () => {
    // Fails without the transcribeTail chain: "segundo" would finish first.
    let finishFirst: (text: string) => void = () => {};
    const h = eventHarness({
      transcribeAudio: (id) =>
        id === "a"
          ? new Promise((r) => (finishFirst = r))
          : Promise.resolve("segundo"),
    });
    h.loop.start();
    h.push("audio", "a");
    h.push("audio", "b");
    await new Promise((r) => setTimeout(r, 10));
    finishFirst("primero");
    await until(() => h.runs.length >= 1);
    h.runs[0]!.release();
    await until(() => h.texts().join(" ").split(" ").length >= 2);
    for (const run of h.runs) run.release();
    expect(h.texts().join(" ")).toBe("primero segundo");
    await h.loop.stop();
  });

  test("speech events never touch the transcriber", async () => {
    let asked = 0;
    const h = eventHarness({
      transcribeAudio: async () => {
        asked += 1;
        return "x";
      },
    });
    h.loop.start();
    h.push("speech", "hola");
    await until(() => h.runs.length === 1);
    expect(h.texts()).toEqual(["hola"]);
    expect(asked).toBe(0);
    h.runs[0]!.release();
    await h.loop.stop();
  });
});
