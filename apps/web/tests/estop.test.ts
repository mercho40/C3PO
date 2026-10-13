/**
 * The e-stop's decisions.
 *
 * Pinned because each of these was once wrong in the direction a stop button
 * must never err: an unreachable bridge read as "Reintentar", a failed damp read
 * as success, and an unconfirmed stop cleared itself back to a calm "Parar".
 */

import { describe, expect, test } from "bun:test";
import {
  advance,
  classifyStopResponse,
  estopMessage,
  estopView,
  isStopHotkey,
  isTypingTarget,
  STILL_MOVING_MS,
  type Telemetry,
} from "../src/lib/robot/estop";

const T0 = 1_000_000;

const tele = (over: Partial<Telemetry> = {}): Telemetry => ({
  online: true,
  moving: false,
  known: true,
  sampledAt: T0 + 500,
  env: "real",
  ...over,
});

describe("a posture nobody can vouch for", () => {
  // readPosture maps "unknown" (the post-reboot race), no_data_yet, a stale
  // FSM and a stub's "standing" all to not-moving. None of them is a stop.
  test("an unknown/stale/stub posture never confirms the stop", () => {
    expect(
      advance("accepted", T0, tele({ known: false, moving: false }), T0 + 600),
    ).toBe("unverifiable");
    expect(estopView("unverifiable").tone).toBe("unconfirmed");
  });

  test("a stale 'walk' does not raise Sigue en movimiento from history", () => {
    expect(
      advance(
        "accepted",
        T0,
        tele({ known: false, moving: true, sampledAt: T0 + STILL_MOVING_MS }),
        T0 + STILL_MOVING_MS + 100,
      ),
    ).toBe("unverifiable");
  });

  test("a known posture arriving later resolves it", () => {
    expect(advance("unverifiable", T0, tele(), T0 + 900)).toBe("stopped");
  });

  test("Detenido does not outlive the robot walking again", () => {
    expect(
      advance(
        "stopped",
        T0,
        tele({ moving: true, sampledAt: T0 + 2000 }),
        T0 + 2100,
      ),
    ).toBe("idle");
    // ...but an offline or unknown reading does not clear it either way.
    expect(
      advance("stopped", T0, tele({ online: false, moving: true }), T0 + 2100),
    ).toBe("stopped");
  });

  test("back's 504 bridge_timeout is 'no answer', not 'never left'", () => {
    expect(
      classifyStopResponse({
        error: { status: 504, value: { error: "bridge_timeout" } },
      }).phase,
    ).toBe("noAnswer");
  });
});

describe("classifyStopResponse", () => {
  test("bridge_unavailable is unreachable, not a retry", () => {
    const r = classifyStopResponse({
      error: { status: 502, value: { error: "bridge_unavailable" } },
    });
    expect(r.phase).toBe("unreachable");
    expect(estopView(r.phase).label).toBe("Sin puente");
  });

  test("Eden's 503 for a fetch that threw is unreachable", () => {
    const r = classifyStopResponse({
      error: { status: 503, value: new Error("fetch failed") },
    });
    expect(r.phase).toBe("unreachable");
  });

  test("a thrown request is unreachable; a timed-out one is unknown", () => {
    expect(classifyStopResponse({ threw: true }).phase).toBe("unreachable");
    expect(classifyStopResponse({ timedOut: true }).phase).toBe("noAnswer");
  });

  test("tool_error is a rejection worth retrying", () => {
    const r = classifyStopResponse({
      error: { status: 502, value: { error: "tool_error", detail: "boom" } },
    });
    expect(r.phase).toBe("rejected");
    expect(estopView(r.phase).label).toBe("Reintentar");
  });

  test("401 is a session problem", () => {
    expect(
      classifyStopResponse({ error: { status: 401, value: null } }).phase,
    ).toBe("expired");
  });

  test("a 200 with a failed damp fallback is NOT success", () => {
    const r = classifyStopResponse({
      data: {
        cancelled_task_ids: [],
        real_damp_fallback_succeeded: false,
        real_damp_fallback_attempts: 3,
      },
      error: null,
    });
    expect(r.phase).toBe("notDamped");
    expect(estopMessage(r.phase)?.level).toBe("alert");
  });

  test("a clean 200 is accepted — not yet stopped", () => {
    const r = classifyStopResponse({
      data: { cancelled_task_ids: [], real_damp_fallback_succeeded: true },
      error: null,
    });
    expect(r.phase).toBe("accepted");
    expect(estopView(r.phase).label).toBe("Parando…");
  });

  test("sim's null damp flag is accepted", () => {
    const r = classifyStopResponse({
      data: { real_damp_fallback_succeeded: null },
      error: null,
    });
    expect(r.phase).toBe("accepted");
  });
});

describe("advance", () => {
  test("a reading taken after the order confirms the stop", () => {
    expect(advance("accepted", T0, tele(), T0 + 600)).toBe("stopped");
  });

  test("a reading from before the order confirms nothing", () => {
    expect(
      advance("accepted", T0, tele({ sampledAt: T0 - 100 }), T0 + 600),
    ).toBe("accepted");
  });

  test("offline is never confirmation", () => {
    expect(
      advance("accepted", T0, tele({ online: false, moving: false }), T0 + 600),
    ).toBe("unconfirmed");
  });

  test("unconfirmed holds until telemetry returns, then resolves", () => {
    const offline = tele({ online: false });
    expect(advance("unconfirmed", T0, offline, T0 + 60_000)).toBe(
      "unconfirmed",
    );
    expect(advance("unconfirmed", T0, tele(), T0 + 60_000)).toBe("stopped");
  });

  test("real robot still walking after the grace period is an alarm", () => {
    const walking = tele({ moving: true, sampledAt: T0 + STILL_MOVING_MS });
    expect(advance("accepted", T0, walking, T0 + 1000)).toBe("accepted");
    expect(advance("accepted", T0, walking, T0 + STILL_MOVING_MS + 1)).toBe(
      "stillMoving",
    );
  });

  test("sim keeps its walking controller after a stop — not an alarm", () => {
    const walking = tele({
      moving: true,
      env: "isaac",
      sampledAt: T0 + STILL_MOVING_MS,
    });
    expect(advance("accepted", T0, walking, T0 + STILL_MOVING_MS + 1)).toBe(
      "holdingGait",
    );
  });

  test("stillMoving survives losing telemetry, and clears when it stops", () => {
    expect(
      advance("stillMoving", T0, tele({ online: false }), T0 + 20_000),
    ).toBe("stillMoving");
    expect(advance("stillMoving", T0, tele(), T0 + 20_000)).toBe("stopped");
  });

  test("failures are never advanced by telemetry", () => {
    for (const phase of [
      "unreachable",
      "rejected",
      "notDamped",
      "noAnswer",
      "expired",
    ] as const) {
      expect(advance(phase, T0, tele(), T0 + 600)).toBe(phase);
    }
  });
});

describe("hotkey", () => {
  const key = (over: Partial<Parameters<typeof isStopHotkey>[0]> = {}) => ({
    key: " ",
    code: "Space",
    shiftKey: true,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    repeat: false,
    target: { tagName: "BODY" },
    ...over,
  });

  test("Shift+Space on the page stops", () => {
    expect(isStopHotkey(key())).toBe(true);
    expect(isStopHotkey(key({ target: { tagName: "BUTTON" } }))).toBe(true);
  });

  test("plain Space, other modifiers and key repeat do not", () => {
    expect(isStopHotkey(key({ shiftKey: false }))).toBe(false);
    expect(isStopHotkey(key({ ctrlKey: true }))).toBe(false);
    expect(isStopHotkey(key({ metaKey: true }))).toBe(false);
    expect(isStopHotkey(key({ repeat: true }))).toBe(false);
    expect(isStopHotkey(key({ key: "B", code: "KeyB" }))).toBe(false);
  });

  test("typing a space into a field is left alone", () => {
    expect(isStopHotkey(key({ target: { tagName: "INPUT" } }))).toBe(false);
    expect(
      isStopHotkey(key({ target: { tagName: "INPUT", type: "email" } })),
    ).toBe(false);
    expect(isStopHotkey(key({ target: { tagName: "TEXTAREA" } }))).toBe(false);
    expect(isStopHotkey(key({ target: { tagName: "SELECT" } }))).toBe(false);
    expect(
      isStopHotkey(
        key({ target: { tagName: "DIV", isContentEditable: true } }),
      ),
    ).toBe(false);
  });

  test("non-text inputs are not typing", () => {
    expect(isTypingTarget({ tagName: "INPUT", type: "range" })).toBe(false);
    expect(isTypingTarget({ tagName: "INPUT", type: "checkbox" })).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
