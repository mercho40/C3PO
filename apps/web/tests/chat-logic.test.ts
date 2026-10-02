/**
 * The decisions behind /chat that can hurt someone if they are wrong.
 *
 * Pinned here: a tool call that ended long ago never reads as running; a
 * timed-out or stub walk never reads as a green success; a failed turn that
 * already dispatched a skill is never one-click retried; and a voice transcript
 * is only polled when it is one.
 */

import { describe, expect, test } from "bun:test";
import {
  classifyToolPart,
  INTERRUPTED_NOTE,
  toolName,
} from "../src/lib/chat/tool-part";
import {
  awaitingModel,
  continuationPrompt,
  planRecovery,
  replyText,
  type MessageLike,
} from "../src/lib/chat/turn";
import {
  cleanCause,
  describeChatError,
  ERROR_LEAD,
} from "../src/lib/chat/chat-error";
import {
  shouldPollTranscript,
  transcriptFreshness,
  transcriptSignature,
} from "../src/lib/chat/transcript";
import { normalizeTitle, titleOf } from "../src/lib/chat/title";
import {
  canLoadMoreHistory,
  HISTORY_MAX,
  HISTORY_PAGE,
  shouldPinDraft,
} from "../src/lib/chat/history";
import { newId } from "../src/lib/chat/ids";

const tool = (state: string, extra: Record<string, unknown> = {}) => ({
  type: "tool-walk_to",
  state,
  ...extra,
});

describe("classifyToolPart — liveness", () => {
  test("input-available is running only while live", () => {
    const live = classifyToolPart(tool("input-available"), { live: true });
    expect(live.tone).toBe("running");
    expect(live.label).toBe("Ejecutando");
    expect(live.defaultOpen).toBe(true);
  });

  test("a stuck input-available after the turn ended is interrupted, collapsed, and may have run", () => {
    const d = classifyToolPart(tool("input-available"), { live: false });
    expect(d.tone).toBe("warn");
    expect(d.label).toBe("Interrumpido");
    expect(d.note).toBe(INTERRUPTED_NOTE);
    expect(d.defaultOpen).toBe(false);
  });

  test("input-streaming is 'Preparando' live and interrupted otherwise", () => {
    expect(
      classifyToolPart(tool("input-streaming"), { live: true }).label,
    ).toBe("Preparando");
    expect(
      classifyToolPart(tool("input-streaming"), { live: false }).label,
    ).toBe("Interrumpido");
  });

  test("approval-requested is never 'interrupted' (the chat is ready while it waits)", () => {
    expect(
      classifyToolPart(tool("approval-requested"), { live: false }).label,
    ).toBe("Requiere aprobación");
  });
});

describe("classifyToolPart — outcomes", () => {
  test("a real arrival is ok", () => {
    const d = classifyToolPart(
      tool("output-available", {
        output: { status: "completed", result: { arrived: true } },
      }),
      { live: false },
    );
    expect(d.tone).toBe("ok");
    expect(d.label).toBe("Completado");
  });

  test("a walk that timed out short of its goal is a warning, not a green check", () => {
    const d = classifyToolPart(
      tool("output-available", {
        output: {
          status: "completed",
          phase: "timeout",
          error: null,
          result: { arrived: false },
        },
      }),
      { live: false },
    );
    expect(d.tone).toBe("warn");
    expect(d.label).toBe("Se agotó el tiempo sin llegar");
  });

  test("a cancelled task is a warning", () => {
    const d = classifyToolPart(
      tool("output-available", { output: { status: "cancelled" } }),
      { live: false },
    );
    expect(d.tone).toBe("warn");
    expect(d.label).toBe("Cancelado");
  });

  test("a stub run is flagged simulated", () => {
    const d = classifyToolPart(
      tool("output-available", {
        output: { status: "completed", stub: true, phase: "stub" },
      }),
      { live: false },
    );
    expect(d.simulated).toBe(true);
    expect(d.tone).toBe("warn");
  });

  test("acked_no_transition is an error, even with status completed", () => {
    const d = classifyToolPart(
      {
        type: "tool-prepare",
        state: "output-available",
        output: {
          status: "completed",
          error: null,
          phase: "acked_no_transition",
          result: { transitioned: false },
        },
      },
      { live: false },
    );
    expect(d.tone).toBe("error");
    expect(d.label).toBe("Error");
    expect(d.errorText).toBe("Aceptado, pero el robot NO cambió de estado");
    expect(d.defaultOpen).toBe(true);
  });

  test("runtime's {error} wrapper is unwrapped: bridge_unavailable", () => {
    const d = classifyToolPart(
      tool("output-available", { output: { error: "bridge_unavailable" } }),
      { live: false },
    );
    expect(d.tone).toBe("error");
    expect(d.errorText).toBe("El puente no respondió.");
    expect(d.output).toBeUndefined();
  });

  test("runtime's {error} wrapper is unwrapped: tool_error keeps the detail verbatim", () => {
    const d = classifyToolPart(
      tool("output-available", { output: { error: "tool_error: boom" } }),
      { live: false },
    );
    expect(d.errorText).toBe("El puente devolvió un error.");
    expect(d.detail).toBe("boom");
  });

  test("a firmware refusal keeps its JSON visible next to the Spanish hint", () => {
    const output = {
      status: "failed",
      phase: "rpc_error",
      error: "rpc_error_code_7401",
    };
    const d = classifyToolPart(tool("output-available", { output }), {
      live: false,
    });
    expect(d.tone).toBe("error");
    expect(d.output).toEqual(output);
  });

  test("an unverified bring-up is neutral", () => {
    const d = classifyToolPart(
      tool("output-available", {
        output: { status: "completed", result: { transitioned: null } },
      }),
      { live: false },
    );
    expect(d.tone).toBe("neutral");
  });

  test("output-error is an error with the SDK text as detail", () => {
    const d = classifyToolPart(
      tool("output-error", { errorText: "Invalid input" }),
      { live: false },
    );
    expect(d.tone).toBe("error");
    expect(d.detail).toBe("Invalid input");
  });

  test("toolName handles static and dynamic tools", () => {
    expect(toolName({ type: "tool-walk_to" })).toBe("walk_to");
    expect(toolName({ type: "dynamic-tool", toolName: "wave" })).toBe("wave");
  });
});

const user = (id = "u1"): MessageLike => ({
  id,
  role: "user",
  parts: [{ type: "text", text: "Caminá" }],
});
const assistant = (parts: MessageLike["parts"]): MessageLike => ({
  id: "a1",
  role: "assistant",
  parts,
});

describe("planRecovery", () => {
  test("no reply yet: one-click retry is safe", () => {
    expect(planRecovery([user()])).toEqual({ kind: "retry" });
  });

  test("text-only partial reply: one-click retry", () => {
    expect(
      planRecovery([
        user(),
        assistant([{ type: "step-start" }, { type: "text", text: "Voy…" }]),
      ]),
    ).toEqual({ kind: "retry" });
  });

  test("a skill that came back blocks one-click retry", () => {
    const plan = planRecovery([
      user(),
      assistant([
        { type: "tool-walk_to", state: "output-available" },
        { type: "text", text: "Listo, ahora…" },
      ]),
    ]);
    expect(plan).toEqual({ kind: "continue", ran: ["walk_to"], maybe: [] });
  });

  test("a skill handed out with no answer is 'maybe'", () => {
    const plan = planRecovery([
      user(),
      assistant([
        { type: "tool-walk_to", state: "output-available" },
        { type: "tool-turn", state: "input-available" },
      ]),
    ]);
    expect(plan).toEqual({
      kind: "continue",
      ran: ["walk_to"],
      maybe: ["turn"],
    });
  });

  test("a call whose arguments never finished streaming was never dispatched", () => {
    expect(
      planRecovery([
        user(),
        assistant([{ type: "tool-walk_to", state: "input-streaming" }]),
      ]),
    ).toEqual({ kind: "retry" });
  });

  test("duplicates collapse, and ran wins over maybe", () => {
    const plan = planRecovery([
      user(),
      assistant([
        { type: "tool-wave", state: "output-available" },
        { type: "tool-wave", state: "output-available" },
        { type: "tool-wave", state: "input-available" },
      ]),
    ]);
    expect(plan).toEqual({ kind: "continue", ran: ["wave"], maybe: [] });
  });

  test("the continuation prompt names what ran", () => {
    const text = continuationPrompt({ ran: ["walk_to"], maybe: ["turn"] });
    expect(text).toContain("walk_to");
    expect(text).toContain("turn");
    expect(text).toContain("No repitas");
  });
});

describe("awaitingModel", () => {
  test("submitted always shows the indicator", () => {
    expect(awaitingModel("submitted", [user()])).toBe(true);
  });

  test("streaming before the reply exists", () => {
    expect(awaitingModel("streaming", [user()])).toBe(true);
  });

  test("streaming between steps: after a finished tool", () => {
    expect(
      awaitingModel("streaming", [
        user(),
        assistant([{ type: "tool-walk_to", state: "output-available" }]),
      ]),
    ).toBe(true);
  });

  test("streaming between steps: on a fresh step-start", () => {
    expect(
      awaitingModel("streaming", [
        user(),
        assistant([{ type: "text", text: "a" }, { type: "step-start" }]),
      ]),
    ).toBe(true);
  });

  test("not while text or a running tool is visibly streaming", () => {
    expect(
      awaitingModel("streaming", [
        user(),
        assistant([{ type: "text", text: "Camino" }]),
      ]),
    ).toBe(false);
    expect(
      awaitingModel("streaming", [
        user(),
        assistant([{ type: "tool-walk_to", state: "input-available" }]),
      ]),
    ).toBe(false);
  });

  test("never when idle or failed", () => {
    expect(awaitingModel("ready", [user()])).toBe(false);
    expect(awaitingModel("error", [user()])).toBe(false);
  });
});

describe("replyText", () => {
  test("joins text parts and skips tools and reasoning", () => {
    expect(
      replyText([
        { type: "reasoning", text: "hmm" },
        { type: "text", text: "Primero " },
        { type: "tool-walk_to", state: "output-available" },
        { type: "text", text: "Después" },
      ]),
    ).toBe("Primero\n\nDespués");
  });
});

describe("describeChatError", () => {
  test("always leads with the Spanish sentence and keeps the raw cause", () => {
    const v = describeChatError("Something odd", null);
    expect(v.lead).toBe(ERROR_LEAD);
    expect(v.cause).toBe("Something odd");
    expect(v.hint).toBeNull();
  });

  test("409 is a read-only voice chat, not retryable", () => {
    const v = describeChatError("Conflict", 409);
    expect(v.hint).toContain("transcripción de voz");
    expect(v.retryable).toBe(false);
    expect(v.suggestNewChat).toBe(true);
  });

  test("403 is someone else's chat", () => {
    expect(describeChatError("Forbidden", 403).hint).toContain("otra cuenta");
  });

  test("a mid-stream gateway failure is recognised from its text", () => {
    const v = describeChatError(
      "Failed after 3 attempts. Last error: Bad Gateway",
      null,
    );
    expect(v.hint).toContain("TIC AI");
    expect(v.retryable).toBe(true);
  });

  test("502/503 by status", () => {
    expect(describeChatError("", 503).hint).toContain("TIC AI");
  });

  test("a missing API key is a configuration problem, not retryable", () => {
    const v = describeChatError("AGENT_API_KEY is not set — …", 500);
    expect(v.retryable).toBe(false);
    expect(v.hint).toContain("AGENT_API_KEY");
  });

  test("an nginx HTML body is reduced to its text", () => {
    expect(
      cleanCause(
        "<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center><hr><center>nginx</center></body></html>",
      ),
    ).toBe("502 Bad Gateway 502 Bad Gateway nginx");
  });

  test("empty causes are null and huge ones are capped", () => {
    expect(cleanCause("   ")).toBeNull();
    expect((cleanCause("x".repeat(5000)) ?? "").length).toBe(600);
  });
});

describe("voice transcript polling", () => {
  test("only a loaded voice chat is polled", () => {
    expect(
      shouldPollTranscript({ channel: "voice", selectedStatus: "ok" }),
    ).toBe(true);
    expect(
      shouldPollTranscript({ channel: "text", selectedStatus: "ok" }),
    ).toBe(false);
    expect(
      shouldPollTranscript({ channel: "voice", selectedStatus: "error" }),
    ).toBe(false);
    expect(
      shouldPollTranscript({ channel: undefined, selectedStatus: "none" }),
    ).toBe(false);
  });

  test("signature changes when a row is added or rewritten", () => {
    const a = [{ id: "1", parts: [{ type: "text", text: "hola" }] }];
    const b = [{ id: "1", parts: [{ type: "text", text: "hola, qué tal" }] }];
    expect(transcriptSignature(a)).toBe(transcriptSignature([...a]));
    expect(transcriptSignature(a)).not.toBe(transcriptSignature(b));
    expect(transcriptSignature(a)).not.toBe(
      transcriptSignature([...a, { id: "2", parts: [] }]),
    );
  });

  test("freshness says how old the copy is, and stops claiming current on failure", () => {
    const now = 100_000;
    expect(
      transcriptFreshness({
        lastOkAt: now - 3000,
        failures: 0,
        gone: false,
        now,
      }).text,
    ).toBe("Actualizado hace 3 s");
    const failing = transcriptFreshness({
      lastOkAt: now - 20_000,
      failures: 2,
      gone: false,
      now,
    });
    expect(failing.tone).toBe("warn");
    expect(failing.text).toContain("hace 20 s");
    expect(
      transcriptFreshness({ lastOkAt: null, failures: 0, gone: true, now })
        .tone,
    ).toBe("warn");
  });

  test("the announcement has no clock in it, so it does not re-read every second", () => {
    const now = 1_700_000_000_000;
    const at = (t: number, failures: number) =>
      transcriptFreshness({
        lastOkAt: now - 20_000,
        failures,
        gone: false,
        now: t,
      });
    expect(at(now, 0).announce).toBe("");
    expect(at(now, 2).announce).not.toBe("");
    // The visible text ticks; the announcement does not.
    expect(at(now, 2).text).not.toBe(at(now + 1000, 2).text);
    expect(at(now, 2).announce).toBe(at(now + 1000, 3).announce);
    expect(
      transcriptFreshness({ lastOkAt: null, failures: 0, gone: true, now })
        .announce,
    ).toContain("ya no existe");
  });
});

describe("history rail — Ver más", () => {
  test("a full page means there may be more", () => {
    expect(
      canLoadMoreHistory({
        historyError: false,
        fetched: 50,
        limit: HISTORY_PAGE,
      }),
    ).toBe(true);
    expect(
      canLoadMoreHistory({
        historyError: false,
        fetched: 12,
        limit: HISTORY_PAGE,
      }),
    ).toBe(false);
  });

  test("decided by what was fetched, so a local delete cannot hide it", () => {
    // 50 fetched, one deleted on screen: still 50 fetched.
    expect(
      canLoadMoreHistory({ historyError: false, fetched: 50, limit: 50 }),
    ).toBe(true);
  });

  test("not past the backend's cap, and not over a failed list", () => {
    expect(
      canLoadMoreHistory({
        historyError: false,
        fetched: 200,
        limit: HISTORY_MAX,
      }),
    ).toBe(false);
    expect(
      canLoadMoreHistory({ historyError: true, fetched: 50, limit: 50 }),
    ).toBe(false);
  });
});

describe("draft id → URL", () => {
  const base = {
    pinned: false,
    messageCount: 1,
    hasSelected: false,
    accepted: true,
  };

  test("pinned once the backend has accepted the first turn", () => {
    expect(shouldPinDraft(base)).toBe(true);
    expect(shouldPinDraft({ ...base, messageCount: 0 })).toBe(false);
    expect(shouldPinDraft({ ...base, hasSelected: true })).toBe(false);
  });

  test("not on a send the backend never took — no row, no link", () => {
    // Back down: the user's message is on screen, the request failed.
    expect(shouldPinDraft({ ...base, accepted: false })).toBe(false);
  });

  test("never re-pinned: 'Nuevo chat' must not write the old ?id= back", () => {
    // Between the navigation to /chat and afterNavigate minting a new draft,
    // the old (pinned) conversation is still on screen with its messages.
    expect(shouldPinDraft({ ...base, pinned: true, messageCount: 4 })).toBe(
      false,
    );
  });
});

describe("newId", () => {
  const UUID4 =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  test("uses randomUUID where the context allows it", () => {
    const id = newId({
      randomUUID: () => "from-randomUUID",
      getRandomValues: (a) => a,
    });
    expect(id).toBe("from-randomUUID");
  });

  test("plain http (no randomUUID) still gets a v4 UUID", () => {
    // What a phone on http://<lan-ip>:3001 sees: getRandomValues only.
    const insecure = {
      getRandomValues: (a: Uint8Array<ArrayBuffer>) =>
        globalThis.crypto.getRandomValues(a),
    };
    const a = newId(insecure);
    const b = newId(insecure);
    expect(UUID4.test(a)).toBe(true);
    expect(a === b).toBe(false);
    // Version and variant bits are forced even from all-ones input.
    const ones = newId({ getRandomValues: (arr) => arr.fill(0xff) });
    expect(UUID4.test(ones)).toBe(true);
  });
});

describe("titles", () => {
  test("normalizeTitle trims, collapses and rejects empty", () => {
    expect(normalizeTitle("  Prueba   de  marcha ")).toBe("Prueba de marcha");
    expect(normalizeTitle("   ")).toBeNull();
    expect((normalizeTitle("a".repeat(500)) ?? "").length).toBe(120);
  });

  test("titleOf falls back to 'Sin título'", () => {
    expect(titleOf({ title: null })).toBe("Sin título");
    expect(titleOf({ title: "  " })).toBe("Sin título");
    expect(titleOf({ title: "Ronda" })).toBe("Ronda");
  });
});
