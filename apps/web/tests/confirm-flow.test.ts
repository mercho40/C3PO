/**
 * «Confirmar» is one action: the console asks back to grant AND run the
 * refused call, and relays back's words to the agent. Nothing on the console
 * tells the agent to retry any more.
 */
import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { decideCall } from "../src/lib/chat/confirm";
import { pendingConfirmations } from "../src/lib/components/voice/voice-view";
import type { VoiceLoopState } from "../src/lib/robot/voice-loop";

const pick = { label: "Dixie cup", shape: "power", side: "auto" };

function backAnswering(status: number, body: unknown) {
  const sent: { url: string; body: unknown }[] = [];
  const impl = (async (url: string, init: RequestInit) => {
    sent.push({ url, body: JSON.parse(String(init.body)) });
    return Response.json(body, { status });
  }) as unknown as typeof fetch;
  return { sent, impl };
}

describe("decideCall", () => {
  test("Confirmar posts the refused call's id and exact arguments, once, and relays the result", async () => {
    const back = backAnswering(200, {
      tool: "pick",
      result: { status: "completed", phase: "lifted" },
      message: "El operador confirmó y se ejecutó pick. Resultado: …",
    });
    const r = await decideCall(
      "http://back",
      "pick",
      "c1",
      pick,
      "confirm",
      back.impl,
    );
    expect(back.sent).toEqual([
      {
        url: "http://back/skills/pick/confirm",
        body: { callId: "c1", args: pick },
      },
    ]);
    expect(r.outcome.kind).toBe("ok");
    expect(r.outcome.text).toBe("Completado");
    expect(
      r.message?.startsWith("El operador confirmó y se ejecutó pick"),
    ).toBe(true);
  });

  test("a failed run shows the phase and still tells the agent", async () => {
    const back = backAnswering(200, {
      result: { status: "failed", phase: "target_lost" },
      message: "El operador confirmó y se ejecutó pick. Resultado: failed",
    });
    const r = await decideCall(
      "http://back",
      "pick",
      "c1",
      pick,
      "confirm",
      back.impl,
    );
    expect(r.outcome.kind).toBe("error");
    expect(r.outcome.code).toBe("target_lost");
    expect(r.message).not.toBeNull();
  });

  test("a second press (not pending) shows why and tells the agent nothing", async () => {
    const back = backAnswering(409, {
      error: "not_pending",
      detail: "Esa confirmación ya no está pendiente…",
    });
    const r = await decideCall(
      "http://back",
      "pick",
      "c1",
      pick,
      "confirm",
      back.impl,
    );
    expect(r.outcome.kind).toBe("warn");
    expect(r.outcome.text).toBe("Ya no está pendiente");
    expect(r.message).toBeNull();
  });

  test("Cancelar posts only the call id", async () => {
    const back = backAnswering(200, {
      cancelled: true,
      message: "El operador canceló pick",
    });
    const r = await decideCall(
      "http://back",
      "pick",
      "c1",
      pick,
      "cancel",
      back.impl,
    );
    expect(back.sent[0]).toEqual({
      url: "http://back/skills/pick/cancel",
      body: { callId: "c1" },
    });
    expect(r.outcome.text).toBe("Cancelado: no se ejecutó");
  });

  test("back unreachable: no message, and it does not claim nothing ran", async () => {
    const r = await decideCall(
      "http://back",
      "pick",
      "c1",
      pick,
      "confirm",
      (async () => {
        throw new TypeError("fetch failed");
      }) as unknown as typeof fetch,
    );
    expect(r.message).toBeNull();
    expect(r.outcome.text).toBe("No se pudo contactar al servidor");
  });
});

const src = (path: string) =>
  readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");

describe("the /voice page's pending confirmations", () => {
  const state = {
    running: true,
    confirmations: [
      { id: "c1", tool: "pick", args: pick, askedAt: "2026-11-05T12:00:00Z" },
    ],
  } as unknown as VoiceLoopState;

  test("lists the refused call with the exact arguments a Confirmar runs", () => {
    expect(pendingConfirmations(state)).toEqual([
      { id: "c1", tool: "pick", args: pick, argsText: JSON.stringify(pick) },
    ]);
    expect(pendingConfirmations(null)).toEqual([]);
  });

  test("renders a Confirmar and a Cancelar per call, and is on the page", () => {
    const panel = src(
      "../src/lib/components/voice/pending-confirmations.svelte",
    );
    expect(panel).toContain("{#each rows as row (row.id)}");
    expect(panel).toContain('onclick={() => decide(row, "confirm")}');
    expect(panel).toContain('onclick={() => decide(row, "cancel")}');
    expect(panel).toContain("`Confirmar ${row.tool}`");
    expect(panel).toContain("Cancelar");
    expect(panel).toContain("{row.argsText}");
    expect(src("../src/routes/(protected)/voice/+page.svelte")).toContain(
      "<PendingConfirmations />",
    );
  });
});

describe("no surface asks the agent to retry", () => {
  for (const file of [
    "../src/lib/components/chat/tool-card.svelte",
    "../src/routes/(protected)/chat/+page.svelte",
    "../src/lib/robot/vr-voice.svelte.ts",
  ])
    test(file, () => {
      const text = src(file);
      expect(text).not.toContain("podés ejecutar");
      expect(text).not.toContain("/confirm`");
    });

  test("the headset sends back the call on screen with its push-to-talk", () => {
    const voice = src("../src/lib/robot/vr-voice.svelte.ts");
    expect(voice).toContain('q.set("pending", shown.id)');
    expect(voice).toContain("/voice/transcribe?${q}");
    expect(voice).toContain("this.#spoken(body.confirmation)");
  });
});
