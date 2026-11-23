/**
 * Spoken confirmation end to end through /voice/transcribe: the admin's own
 * push-to-talk text is decided HERE, before any model sees it. «confirmo» with
 * the one refused call on screen grants and runs exactly that call; anything
 * else comes back as plain text for the agent.
 */
import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";

import { fixtureSkill, testApp, type TestApp } from "../testing/fakes";
import { RealtimeVoiceSession } from "../voice/realtime";

const pick = { label: "Dixie cup", shape: "power", side: "auto" };
let h: TestApp;
let heard = "confirmo";
let grants: unknown[] = [];
let dispatched: unknown[][] = [];
let restore: (() => void)[] = [];

function say(query: string) {
  return h.request(`/voice/transcribe${query}`, {
    method: "POST",
    headers: { "content-type": "audio/wav" },
    body: new Uint8Array(64),
  });
}
const refuse = (id: string, tool = "pick") =>
  h.services.confirmations.note({
    id,
    tool,
    args: pick,
    ownerId: "u1",
    chatId: "chat_1",
    loop: false,
  });

const t2 = (name: string) =>
  fixtureSkill(name, {
    classification: "task",
    dangerLevel: "medium",
    tier: "T2",
  });

describe("spoken confirmation on /voice/transcribe", () => {
  beforeEach(() => {
    heard = "confirmo";
    grants = [];
    dispatched = [];
    restore = [];
    h = testApp({
      env: { AGENT_API_KEY: "k" },
      skills: [t2("pick"), t2("place")],
      fetch: (url, init) => {
        if (String(url).endsWith("/audio/transcriptions"))
          return Response.json({ text: heard });
        grants.push(JSON.parse(String(init.body)));
        return Response.json({ granted: true });
      },
      callTool: async (name, args) => {
        dispatched.push([name, args]);
        return { status: "completed", phase: "lifted" };
      },
    });
    h.signIn({ id: "u1", role: "admin" });
  });
  afterEach(() => {
    for (const r of restore) r();
  });

  test("«Sí, confirmo» with the one call on screen runs exactly it, once", async () => {
    heard = "Sí, confirmo.";
    refuse("c1");
    const res = await say("?chatId=chat_1&pending=c1");
    const body = (await res.json()) as {
      confirmation: { action: string; message: string; result: unknown };
    };
    expect(body.confirmation.action).toBe("confirmed");
    expect(body.confirmation.message).toStartWith(
      "El operador confirmó y se ejecutó pick",
    );
    expect(grants).toEqual([{ tool: "pick", args: pick }]);
    expect(dispatched).toEqual([["pick", pick]]);
    // Said again: nothing left to run.
    const again = (await (await say("?chatId=chat_1&pending=c1")).json()) as {
      confirmation?: unknown;
    };
    expect(again.confirmation).toBeUndefined();
    expect(dispatched.length).toBe(1);
  });

  test("with nothing pending, «confirmo» is just text for the agent", async () => {
    const body = await (await say("?chatId=chat_1&pending=c1")).json();
    expect(body).toEqual({ text: "confirmo" });
    expect(dispatched).toEqual([]);
  });

  test("with two pending, it asks which and runs neither", async () => {
    refuse("c1");
    refuse("c2", "place");
    const body = (await (await say("?chatId=chat_1&pending=c2")).json()) as {
      confirmation: unknown;
    };
    expect(body.confirmation).toEqual({ action: "ambiguous", count: 2 });
    expect(dispatched).toEqual([]);
    expect(grants).toEqual([]);
  });

  test("a long sentence containing «confirmo» goes to the agent, not the arm", async () => {
    heard = "confirmo que el vaso está a la izquierda, agarralo";
    refuse("c1");
    const body = await (await say("?chatId=chat_1&pending=c1")).json();
    expect(body).toEqual({ text: heard });
    expect(dispatched).toEqual([]);
    expect(h.services.confirmations.list("u1", "chat_1").length).toBe(1);
  });

  test("«cancelar» clears it and runs nothing", async () => {
    heard = "Cancelar.";
    refuse("c1");
    const body = (await (await say("?chatId=chat_1&pending=c1")).json()) as {
      confirmation: { action: string; message: string };
    };
    expect(body.confirmation.action).toBe("cancelled");
    expect(body.confirmation.message).toContain("canceló pick");
    expect(h.services.confirmations.list("u1", "chat_1")).toEqual([]);
    expect(dispatched).toEqual([]);
  });

  test("a client that names no call on screen gets no spoken confirmation", async () => {
    refuse("c1");
    expect(await (await say("?chatId=chat_1")).json()).toEqual({
      text: "confirmo",
    });
    expect(dispatched).toEqual([]);
  });

  test("the realtime session of that chat is told the outcome, confirmed or cancelled", async () => {
    const told: [string, string][] = [];
    const inform = spyOn(
      RealtimeVoiceSession.prototype,
      "informOutcome",
    ).mockImplementation((chatId, message) => {
      told.push([chatId, message]);
    });
    restore.push(() => inform.mockRestore());
    refuse("c1");
    await say("?chatId=chat_1&pending=c1");
    heard = "cancelar";
    refuse("c2");
    await say("?chatId=chat_1&pending=c2");
    expect(told.map(([chatId]) => chatId)).toEqual(["chat_1", "chat_1"]);
    expect(told[0]?.[1]).toContain("El operador confirmó y se ejecutó pick");
    expect(told[1]?.[1]).toContain("El operador canceló pick");
  });

  test("each stage of the request is timed in the log", async () => {
    const log = spyOn(console, "log").mockImplementation(() => {});
    restore.push(() => log.mockRestore());
    refuse("c1");
    await say("?chatId=chat_1&pending=c1");
    const lines = log.mock.calls.map((c) => String(c[0]));
    expect(
      lines.some((l) =>
        /^\[voice\] transcribe 64 B: upload \d+ ms, transcribe \d+ ms, confirm \d+ ms$/.test(
          l,
        ),
      ),
    ).toBe(true);
    heard = "hola";
    await say("?chatId=chat_1");
    const last = log.mock.calls[log.mock.calls.length - 1];
    expect(String(last?.[0])).toMatch(
      /^\[voice\] transcribe 64 B: upload \d+ ms, transcribe \d+ ms$/,
    );
  });
});

// 2026-11-13 13:56:59: «Confirmado: ejecuté pick.» cut into a turn's 8.8 s say.
describe("the legacy loop's spoken lines", () => {
  test("go out one at a time, each waiting for the one before to finish", async () => {
    const said: string[] = [];
    let finish!: () => void;
    const h = testApp({
      callTool: async (name, args) => {
        if (name !== "say") return {};
        said.push(String(args?.text));
        expect(args?.wait_for_completion).toBe(true);
        if (said.length === 1) await new Promise<void>((r) => (finish = r));
        return { status: "ok" };
      },
    });
    const { confirmations } = h.services;
    confirmations.publish({ kind: "loop", message: "m1", spoken: "uno" });
    confirmations.publish({ kind: "loop", message: "m2", spoken: "dos" });
    await Bun.sleep(5);
    expect(said).toEqual(["uno"]);
    finish();
    await Bun.sleep(5);
    expect(said).toEqual(["uno", "dos"]);
  });
});
