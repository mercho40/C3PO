/**
 * /voice's reading of the loop's status. Pinned: realtime never shows a stops
 * counter (spoken stops do nothing under Realtime), a failed realtime session
 * shows as an error rather than "off", the mic caveat appears only while
 * running and is worded by what the bridge says the mic is, another
 * operator's session reads as theirs, counters say nothing while nothing
 * runs, the status line does not change on every turn, a held reading is
 * never shown as current, and the transcript list never says "no hay" about
 * chats it did not scan.
 */

import { describe, expect, test } from "bun:test";
import {
  counters,
  countersScope,
  lastTurnMetrics,
  micWarning,
  ownership,
  phaseView,
  sessionStatus,
  transcriptsCopy,
} from "../src/lib/components/voice/voice-view";
import type { VoiceLoopState } from "../src/lib/robot/voice-loop";

function state(overrides: Partial<VoiceLoopState> = {}): VoiceLoopState {
  return {
    running: true,
    engine: "legacy",
    utterancesHeard: 3,
    agentRuns: 2,
    stopsTriggered: 1,
    micEverOpen: true,
    alwaysListening: false,
    lastError: null,
    lastHeard: null,
    conversation: { phase: "idle", lastTurn: null },
    ...overrides,
  };
}

describe("counters", () => {
  test("legacy shows stops", () => {
    expect(counters(state()).map((c) => c.label)).toEqual([
      "Escuchado",
      "Turnos",
      "Paradas",
    ]);
  });

  test("realtime shows tool calls and NO stops counter", () => {
    const c = counters(
      state({
        engine: "openai-realtime",
        stopsTriggered: null,
        toolCalls: 4,
        alwaysListening: null,
      }),
    );
    expect(c.map((x) => x.label)).toEqual([
      "Escuchado",
      "Respuestas",
      "Herramientas",
    ]);
    expect(c[2]!.value).toBe("4");
  });

  test("a null legacy counter renders as a dash, never 0", () => {
    expect(counters(state({ stopsTriggered: null }))[2]!.value).toBe("—");
  });

  test("nothing running counts nothing: dashes, not the last numbers", () => {
    const c = counters(
      state({ running: false, utterancesHeard: 12, agentRuns: 0 }),
    );
    expect(c.map((x) => x.value)).toEqual(["—", "—", "—"]);
  });

  test("the scope line says legacy counters do not restart per session", () => {
    expect(countersScope(state()) ?? "").toContain(
      "desde que arrancó el servidor",
    );
    expect(countersScope(state({ engine: "openai-realtime" })) ?? "").toContain(
      "esta sesión",
    );
    expect(countersScope(state({ running: false }))).toBeNull();
  });
});

describe("sessionStatus", () => {
  const base = { reason: null, robotLink: null };

  test("turns do not change the line: listening and speaking both read Activa", () => {
    const a = sessionStatus({
      ...base,
      state: state({ engine: "openai-realtime", phase: "listening" }),
    });
    const b = sessionStatus({
      ...base,
      state: state({ engine: "openai-realtime", phase: "speaking" }),
    });
    expect(a.text).toBe("Activa");
    expect(b.text).toBe(a.text);
  });

  test("connecting is said once, then on", () => {
    expect(
      sessionStatus({
        ...base,
        state: state({ engine: "openai-realtime", phase: "connecting" }),
      }).text,
    ).toBe("Conectando…");
  });

  test("a held reading keeps its words, loses its colour, and says so", () => {
    const st = sessionStatus({
      ...base,
      reason: "No se pudo contactar al servidor",
      state: state(),
    });
    expect(st.text).toContain("última lectura");
    expect(st.tone).toBe("mute");
  });

  test("on, but the robot link is down, is not green", () => {
    const st = sessionStatus({
      ...base,
      robotLink: "Sin puente",
      state: state(),
    });
    expect(st.tone).toBe("warn");
    expect(st.text).toContain("Sin puente");
  });

  test("a failed realtime session is an error; nothing yet is not 'off'", () => {
    expect(
      sessionStatus({
        ...base,
        state: state({
          engine: "openai-realtime",
          phase: "error",
          running: false,
        }),
      }).tone,
    ).toBe("danger");
    expect(sessionStatus({ ...base, state: null }).text).toBe("Consultando…");
  });
});

describe("transcriptsCopy", () => {
  // GET /chats filters by channel server-side, so an empty answer is "none".
  test("no voice chats can say so", () => {
    const c = transcriptsCopy({
      engine: "realtime",
      count: 0,
      capped: false,
      scanned: 200,
    });
    expect(c.empty ?? "").toContain("Todavía no hay");
  });

  test("the engine note follows the engine actually in use", () => {
    expect(
      transcriptsCopy({
        engine: "legacy",
        count: 0,
        capped: false,
        scanned: 200,
      }).engine,
    ).toContain("no guarda");
    expect(
      transcriptsCopy({
        engine: "realtime",
        count: 3,
        capped: false,
        scanned: 200,
      }).engine,
    ).not.toContain("no guarda");
  });

  test("a capped list says it may be partial", () => {
    expect(
      transcriptsCopy({ engine: null, count: 4, capped: true, scanned: 200 })
        .scope ?? "",
    ).toContain("más antiguas");
  });
});

describe("phaseView", () => {
  test("realtime connecting → listening → speaking", () => {
    const v = phaseView(
      state({ engine: "openai-realtime", phase: "listening" }),
    );
    expect(v.steps.map((s) => s.status)).toEqual(["done", "active", "todo"]);
    expect(v.current).toBe("Escuchando");
  });

  test("a failed realtime session is an error, not off", () => {
    const v = phaseView(
      state({ engine: "openai-realtime", phase: "error", running: false }),
    );
    expect(v.error).toBe(true);
  });

  test("legacy streaming reads as thinking", () => {
    const v = phaseView(
      state({ conversation: { phase: "streaming", lastTurn: null } }),
    );
    expect(v.current).toBe("Pensando");
  });

  test("nothing running has no active step", () => {
    const v = phaseView(state({ running: false }));
    expect(v.current).toBeNull();
    expect(v.steps.every((s) => s.status === "todo")).toBe(true);
  });
});

describe("micWarning", () => {
  test("silent when nothing runs — the Oye panel owns that story", () => {
    expect(
      micWarning(state({ running: false, micEverOpen: false })),
    ).toBeNull();
  });

  test("silent once audio has arrived", () => {
    expect(micWarning(state({ micEverOpen: true }))).toBeNull();
  });

  test("push-to-talk blames the button", () => {
    expect(
      micWarning(state({ micEverOpen: false, alwaysListening: false })) ?? "",
    ).toContain("pulsar para hablar");
  });

  test("an always-on mic does not blame push-to-talk", () => {
    const w =
      micWarning(state({ micEverOpen: false, alwaysListening: true })) ?? "";
    expect(w).toContain("siempre abierto");
    expect(w).not.toContain("pulsar");
  });

  test("realtime (null) makes no claim about the mic type", () => {
    const w =
      micWarning(state({ micEverOpen: false, alwaysListening: null })) ?? "";
    expect(w).not.toContain("pulsar");
    expect(w).not.toContain("siempre");
  });
});

describe("ownership", () => {
  test("another operator's realtime session is theirs", () => {
    expect(
      ownership(state({ engine: "openai-realtime", ownedByYou: false })),
    ).toBe("other");
    expect(
      ownership(state({ engine: "openai-realtime", ownedByYou: true })),
    ).toBe("yours");
  });

  test("the legacy loop is shared; nothing running is none", () => {
    expect(ownership(state({ ownedByYou: null }))).toBe("shared");
    expect(ownership(state({ running: false }))).toBe("none");
  });
});

describe("lastTurnMetrics", () => {
  test("present only when the loop reported a turn", () => {
    expect(lastTurnMetrics(state())).toEqual([]);
    const m = lastTurnMetrics(
      state({
        conversation: {
          phase: "idle",
          lastTurn: {
            modelFirstDeltaMs: 410.4,
            firstSentenceMs: 900,
            firstSpeechMs: null,
            completionMs: 1500,
            totalMs: 2300,
            speechCalls: 2,
            characters: 80,
          },
        },
      }),
    );
    expect(m.find((x) => x.label === "Primera voz")?.value).toBe("—");
    expect(m.find((x) => x.label === "Turno completo")?.value).toBe("2300 ms");
  });
});
