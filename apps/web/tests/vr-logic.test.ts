/**
 * /vr-control's decision rules.
 *
 * What is pinned here is honesty, not layout: an offline robot is not "in the
 * wrong posture", a step's success is not erased by the posture change it
 * caused, a precautionary stop is not reported as a refusal, a missing catalogue
 * entry is not "verified", and the arm mirror is not offered when the bridge has
 * already said no.
 */

import { describe, expect, test } from "bun:test";
import {
  BRING_UP_STEPS,
  GESTURE_CATALOGUE_FALLBACK,
  GESTURE_LABELS,
  OUTCOME_SETTLE_MS,
  OUTCOME_TTL_MS,
  WALK_FSM,
  armReadiness,
  bringUpProgress,
  catalogueHealth,
  envBanner,
  estopHalts,
  gestureCaveat,
  gestureNamesFromSchema,
  handsLabel,
  isAdbLink,
  isRealHardware,
  moreGestures,
  needsHoldConfirm,
  outcomeDetail,
  postureGate,
  precautionaryStopNeeded,
  pruneOutcome,
  pruneOutcomes,
  realBadge,
  stampOutcome,
  stopInFlight,
  walkResultAction,
  xrErrorCopy,
} from "../src/lib/robot/vr-logic";
import {
  classifyHttpError,
  classifySkillResult,
  type SkillOutcome,
} from "../src/lib/robot/skill-outcome";
import type { TeleopStatus } from "../src/lib/teleop/stream";

const ok: SkillOutcome = classifySkillResult({ status: "completed" });

describe("postureGate", () => {
  test("offline is its own answer, never a stale posture", () => {
    expect(postureGate("walk_waist", false, WALK_FSM)).toBe("offline");
  });
  test("a walk program passes", () => {
    expect(postureGate("walk_waist", true, WALK_FSM)).toBe("ok");
    expect(postureGate("walk", true, WALK_FSM)).toBe("ok");
  });
  test("standing prepared is not walking", () => {
    expect(postureGate("preparation", true, WALK_FSM)).toBe("wrong_posture");
    expect(postureGate(null, true, WALK_FSM)).toBe("wrong_posture");
  });
});

describe("bringUpProgress", () => {
  test("damp: step 1 is where the robot is, step 2 is next", () => {
    const p = bringUpProgress("damp", true);
    expect(p.known).toBe(true);
    expect(p.marks).toEqual({
      damp: "current",
      prepare: "next",
      start_walking_waist: "todo",
    });
    expect(p.next).toBe("prepare");
  });
  test("prepared: 1 done, 2 current, 3 next", () => {
    const p = bringUpProgress("preparation", true);
    expect(p.marks.damp).toBe("done");
    expect(p.marks.prepare).toBe("current");
    expect(p.next).toBe("start_walking_waist");
  });
  test("walking program: the sequence is complete", () => {
    const p = bringUpProgress("walk_waist", true);
    expect(p.marks.start_walking_waist).toBe("current");
    expect(p.next).toBeNull();
  });
  test("zero torque: nothing done, damp first", () => {
    const p = bringUpProgress("zero_torque", true);
    expect(p.marks.damp).toBe("next");
    expect(p.next).toBe("damp");
  });
  test("a known posture off the sequence points back at damp", () => {
    const p = bringUpProgress("seating", true);
    expect(p.known).toBe(true);
    expect(p.offSequence).toBe(true);
    expect(p.next).toBe("damp");
  });
  test("offline or unrecognised: no markers at all, rather than guessed ones", () => {
    for (const p of [
      bringUpProgress("walk_waist", false),
      bringUpProgress("unknown(803)", true),
      bringUpProgress(null, true),
    ]) {
      expect(p.known).toBe(false);
      expect(p.next).toBeNull();
      expect(p.marks.damp).toBeNull();
    }
  });
  test("the order is damp → prepare → 501", () => {
    expect(BRING_UP_STEPS.map((s) => s.name)).toEqual([
      "damp",
      "prepare",
      "start_walking_waist",
    ]);
  });
});

describe("pruneOutcome", () => {
  const t0 = 1_000_000;
  test("a success survives the posture change it caused", () => {
    // Step 3 answered "Listo" while /state still said preparation…
    const o = stampOutcome(ok, t0, "preparation");
    // …and the poll caught up two seconds later.
    const kept = pruneOutcome(o, t0 + 2000, "walk_waist");
    expect(kept).not.toBeNull();
    expect(kept!.posture).toBe("walk_waist");
  });
  test("after settling, a posture change clears it", () => {
    const o = stampOutcome(ok, t0, "walk_waist");
    expect(pruneOutcome(o, t0 + OUTCOME_SETTLE_MS + 1, "damp")).toBeNull();
  });
  test("unchanged posture keeps it until the TTL", () => {
    const o = stampOutcome(ok, t0, "walk_waist");
    expect(pruneOutcome(o, t0 + 30_000, "walk_waist")).toBe(o);
    expect(pruneOutcome(o, t0 + OUTCOME_TTL_MS + 1, "walk_waist")).toBeNull();
  });
  test("offline is no information: neither clears nor re-stamps", () => {
    const o = stampOutcome(ok, t0, "walk_waist");
    expect(pruneOutcome(o, t0 + 20_000, null)).toBe(o);
  });
  test("an outcome stamped while offline adopts the first posture seen", () => {
    const o = stampOutcome(ok, t0, null);
    expect(pruneOutcome(o, t0 + 20_000, "damp")!.posture).toBe("damp");
  });
  test("pruneOutcomes returns the same object when nothing changed", () => {
    const rec = { wave: stampOutcome(ok, t0, "walk") };
    expect(pruneOutcomes(rec, t0 + 10_000, "walk")).toBe(rec);
    expect(pruneOutcomes(rec, t0 + 10_000, "damp")).toEqual({});
  });
});

describe("outcomeDetail", () => {
  test("a bridge note is shown and marked English", () => {
    const o = classifySkillResult({
      status: "completed",
      phase: "acked_no_transition",
      note: "FSM did not change; check the remote",
    });
    expect(outcomeDetail(o)).toEqual({
      text: "FSM did not change; check the remote",
      lang: "en",
    });
  });
  test("a detail that only repeats the code is dropped", () => {
    const o = classifySkillResult({
      status: "failed",
      error: "rpc_error_code_7401",
    });
    expect(outcomeDetail(o)).toBeNull();
  });
  test("tool_error shows what the tool raised", () => {
    const o = classifyHttpError(502, { error: "tool_error", detail: "boom" });
    expect(outcomeDetail(o)).toEqual({ text: "boom", lang: "en" });
  });
  test("our own Spanish details are not marked English", () => {
    const o = classifyHttpError(502, { error: "bridge_unavailable" });
    expect(outcomeDetail(o)!.lang).toBe("es");
  });
});

describe("walkResultAction", () => {
  const base = {
    dispatch: "move" as const,
    phase: null,
    streamHolds: false,
    movedSinceStop: false,
  };
  test("a refused move is a refusal — HTTP 200 or not", () => {
    const o = classifySkillResult({
      status: "failed",
      phase: "rpc_error",
      error: "rpc_error_code_7302",
    });
    expect(walkResultAction(o, { ...base, phase: "rpc_error" })).toBe("refuse");
  });
  test("not_applicable (Isaac) is a refusal, not a walk", () => {
    const o = classifySkillResult({
      status: "not_applicable",
      message: "real only",
    });
    expect(walkResultAction(o, base)).toBe("refuse");
  });
  test("admin_required on a move is a refusal", () => {
    const o = classifyHttpError(403, { error: "admin_required" });
    expect(walkResultAction(o, base)).toBe("refuse");
  });
  test("an accepted move clears an earlier refusal", () => {
    expect(walkResultAction(ok, base)).toBe("clear");
  });
  test("a precautionary stop nobody needed is not reported", () => {
    const o = classifyHttpError(403, { error: "admin_required" });
    expect(walkResultAction(o, { ...base, dispatch: "stop" })).toBe("ignore");
  });
  test("a stop after our own motion that fails is reported", () => {
    const o = classifyHttpError(502, { error: "bridge_unavailable" });
    expect(
      walkResultAction(o, { ...base, dispatch: "stop", movedSinceStop: true }),
    ).toBe("stop_failed");
  });
  test("teleop_active while OUR stream holds locomotion is us, not another operator", () => {
    const o = classifySkillResult({
      status: "failed",
      phase: "teleop_active",
      error: "a teleoperation session is driving the robot right now.",
    });
    expect(
      walkResultAction(o, {
        ...base,
        phase: "teleop_active",
        streamHolds: true,
      }),
    ).toBe("ignore");
    expect(walkResultAction(o, { ...base, phase: "teleop_active" })).toBe(
      "refuse",
    );
  });
});

describe("PARAR and this page's own locomotion", () => {
  test("every press halts the page — it always passes through `sending`", () => {
    expect(estopHalts("idle", "sending")).toBe(true);
    // A second press while the first one's failure is still on screen.
    expect(estopHalts("unreachable", "sending")).toBe(true);
    expect(estopHalts("stopped", "sending")).toBe(true);
  });
  test("a stop that left idle without `sending` still halts", () => {
    expect(estopHalts("idle", "accepted")).toBe(true);
    expect(estopHalts("idle", "stillMoving")).toBe(true);
  });
  test("the same press moving on does not halt again", () => {
    // Re-halting here would cut a walk the operator deliberately re-armed.
    expect(estopHalts("sending", "accepted")).toBe(false);
    expect(estopHalts("accepted", "stopped")).toBe(false);
    expect(estopHalts("accepted", "unconfirmed")).toBe(false);
    expect(estopHalts("unconfirmed", "accepted")).toBe(false);
    expect(estopHalts("stopped", "idle")).toBe(false);
    expect(estopHalts("sending", "sending")).toBe(false);
  });
  test("on mount, only a stop still going out halts", () => {
    expect(estopHalts(null, "sending")).toBe(true);
    expect(estopHalts(null, "accepted")).toBe(true);
    // A "Detenido" left over from the dashboard is history.
    expect(estopHalts(null, "stopped")).toBe(false);
    expect(estopHalts(null, "holdingGait")).toBe(false);
    expect(estopHalts(null, "idle")).toBe(false);
  });
  test("'Parando…' is in flight; a settled answer is not", () => {
    expect(stopInFlight("sending")).toBe(true);
    expect(stopInFlight("accepted")).toBe(true);
    for (const p of [
      "idle",
      "stopped",
      "unconfirmed",
      "unverifiable",
      "holdingGait",
      "stillMoving",
      "notDamped",
      "unreachable",
      "noAnswer",
      "rejected",
      "expired",
    ] as const) {
      expect(stopInFlight(p)).toBe(false);
    }
  });
});

describe("precautionaryStopNeeded", () => {
  const idle = {
    movedSinceStop: false,
    walking: false,
    loopActive: false,
    vrActive: false,
  };
  test("leaving an idle page sends nothing", () => {
    expect(precautionaryStopNeeded(idle)).toBe(false);
  });
  test("any motion of ours that may be outstanding keeps the stop", () => {
    expect(precautionaryStopNeeded({ ...idle, movedSinceStop: true })).toBe(
      true,
    );
    expect(precautionaryStopNeeded({ ...idle, walking: true })).toBe(true);
    expect(precautionaryStopNeeded({ ...idle, loopActive: true })).toBe(true);
    expect(precautionaryStopNeeded({ ...idle, vrActive: true })).toBe(true);
  });
});

describe("isRealHardware", () => {
  test("live real is real", () => {
    expect(isRealHardware("real", "real")).toBe(true);
  });
  test("a dropped link keeps the last reading, not the page-load seed", () => {
    // Offline: liveEnv is null, the poller's last reading said real.
    expect(isRealHardware(null, "real")).toBe(true);
  });
  test("live beats history in both directions", () => {
    expect(isRealHardware("stub", "real")).toBe(false);
    expect(isRealHardware("real", "stub")).toBe(true);
  });
  test("nothing known is not real", () => {
    expect(isRealHardware(null, null)).toBe(false);
    expect(isRealHardware(undefined, undefined)).toBe(false);
  });
});

describe("gestureCaveat", () => {
  test("a known wrong posture puts the reason on every tile", () => {
    expect(gestureCaveat("wrong_posture")).toContain("marcha");
  });
  test("offline is unknown, not a predicted refusal", () => {
    expect(gestureCaveat("offline")).toBeNull();
    expect(gestureCaveat("ok")).toBeNull();
  });
});

function status(arm: Partial<TeleopStatus["arm"]> = {}): TeleopStatus {
  return {
    frames_received: 10,
    frames_rejected: 0,
    calibrated: true,
    arm_length_m: 0.6,
    deadman_tripped: false,
    stopped_by_estop: false,
    task_id: "tsk_x",
    moving: false,
    hands: "none",
    arm: {
      engaged: false,
      weight: 0,
      enabled_by_env: true,
      sim_mode: "real",
      ...arm,
    },
    arm_error: null,
  };
}

describe("armReadiness", () => {
  test("no stream, no mirror", () => {
    const r = armReadiness({ streaming: false, vrActive: true, status: null });
    expect(r.kind).toBe("no_stream");
    expect(r.canEngage).toBe(false);
  });
  test("disabled by env is said before the press, in Spanish", () => {
    const r = armReadiness({
      streaming: true,
      vrActive: true,
      status: status({ enabled_by_env: false }),
    });
    expect(r.kind).toBe("disabled_env");
    expect(r.canEngage).toBe(false);
    expect(r.text).toContain("TELEOP_ARM_ENABLED");
  });
  test("not real hardware", () => {
    const r = armReadiness({
      streaming: true,
      vrActive: true,
      status: status({ sim_mode: "isaac" }),
    });
    expect(r.kind).toBe("not_real");
    expect(r.text).toContain("isaac");
  });
  test("ready but not in VR says to enter VR", () => {
    const r = armReadiness({
      streaming: true,
      vrActive: false,
      status: status(),
    });
    expect(r.kind).toBe("enter_vr");
    expect(r.canEngage).toBe(false);
  });
  test("ready and engaged", () => {
    expect(
      armReadiness({ streaming: true, vrActive: true, status: status() }).kind,
    ).toBe("ready");
    expect(
      armReadiness({
        streaming: true,
        vrActive: true,
        status: status({ engaged: true, weight: 0.8 }),
      }).text,
    ).toContain("0.8");
  });
});

describe("handsLabel", () => {
  test("known drivers are named; unknown ones pass through marked raw", () => {
    expect(handsLabel("none")).toEqual({ text: "Sin manos", known: true });
    expect(handsLabel("dex3").text).toBe("Dex3");
    expect(handsLabel("inspire")).toEqual({ text: "inspire", known: false });
  });
});

describe("xrErrorCopy", () => {
  test("known DOMException names get Spanish, with the raw message kept", () => {
    const c = xrErrorCopy({
      name: "NotSupportedError",
      message: "The specified session configuration is not supported.",
    });
    expect(c.text).toContain("no soporta");
    expect(c.raw).toBe("The specified session configuration is not supported.");
  });
  test("NotAllowedError, SecurityError and InvalidStateError are named", () => {
    expect(
      xrErrorCopy({ name: "NotAllowedError", message: "x" }).text,
    ).toContain("permiso");
    expect(xrErrorCopy({ name: "SecurityError", message: "x" }).text).toContain(
      "HTTPS",
    );
    expect(
      xrErrorCopy({ name: "InvalidStateError", message: "x" }).text,
    ).toContain("abierta");
  });
  test("our own Spanish errors pass through", () => {
    const c = xrErrorCopy(
      new Error("WebXR no está disponible en este navegador."),
    );
    expect(c).toEqual({
      text: "WebXR no está disponible en este navegador.",
      raw: null,
    });
  });
  test("anything else gets a generic line and the raw text", () => {
    const c = xrErrorCopy({ name: "OperationError", message: "GPU lost" });
    expect(c.text).toBe("No se pudo iniciar la sesión VR.");
    expect(c.raw).toBe("GPU lost");
  });
});

describe("isAdbLink", () => {
  test("only a headset on localhost is the USB case", () => {
    expect(isAdbLink("localhost", true)).toBe(true);
    expect(isAdbLink("localhost", false)).toBe(false);
    expect(isAdbLink("console.lan", true)).toBe(false);
  });
});

describe("envBanner", () => {
  test("offline is unknown, not 'not real'", () => {
    expect(envBanner(false, null, "real")).toEqual({
      kind: "offline",
      lastEnv: "real",
    });
  });
  test("online and not real", () => {
    expect(envBanner(true, "isaac", "isaac")).toEqual({
      kind: "not_real",
      env: "isaac",
    });
  });
  test("online and real: no banner", () => {
    expect(envBanner(true, "real", "real")).toBeNull();
  });
});

describe("catalogueHealth", () => {
  test("a 200 with no skills and a bridge_error is a failure", () => {
    const h = catalogueHealth({
      httpFailed: false,
      bridgeError: "connect ECONNREFUSED",
      skillCount: 0,
      source: "bridge",
      ageSeconds: 0,
    });
    expect(h.failed).toBe(true);
    expect(h.error).toBe("connect ECONNREFUSED");
  });
  test("a cached catalogue is usable but stale, with its age", () => {
    const h = catalogueHealth({
      httpFailed: false,
      bridgeError: "timeout",
      skillCount: 30,
      source: "cache",
      ageSeconds: 412,
    });
    expect(h.failed).toBe(false);
    expect(h.staleAgeS).toBe(412);
  });
  test("an HTTP failure is a failure", () => {
    expect(catalogueHealth({ httpFailed: true, skillCount: 0 }).failed).toBe(
      true,
    );
  });
  test("a live catalogue is healthy", () => {
    expect(
      catalogueHealth({ httpFailed: false, skillCount: 30, source: "bridge" }),
    ).toEqual({ failed: false, staleAgeS: null, error: null });
  });
});

describe("realBadge", () => {
  test("a missing entry is unknown, never verified", () => {
    expect(realBadge({}, "dance")).toBe("unknown");
    expect(realBadge({ dance: false }, "dance")).toBe("untested");
    expect(realBadge({ wave: true }, "wave")).toBeNull();
  });
});

describe("needsHoldConfirm", () => {
  test("high danger from the catalogue needs a hold", () => {
    expect(
      needsHoldConfirm("start_walking_waist", { start_walking_waist: "high" }),
    ).toBe(true);
    expect(needsHoldConfirm("wave", { wave: "low" })).toBe(false);
  });
  test("an unreadable catalogue keeps the friction on step 3", () => {
    expect(needsHoldConfirm("start_walking_waist", {})).toBe(true);
    expect(needsHoldConfirm("damp", {})).toBe(false);
  });
  test("the catalogue wins over the fallback", () => {
    expect(
      needsHoldConfirm("start_walking_waist", {
        start_walking_waist: "medium",
      }),
    ).toBe(false);
  });
});

describe("gestures", () => {
  test("the fallback mirrors the firmware's 23 actions, each with a label", () => {
    expect(GESTURE_CATALOGUE_FALLBACK.length).toBe(23);
    for (const name of GESTURE_CATALOGUE_FALLBACK) {
      expect(GESTURE_LABELS[name]).not.toBeUndefined();
    }
  });
  test("a schema enum wins over the mirror", () => {
    expect(
      gestureNamesFromSchema({
        properties: { name: { type: "string", enum: ["high_five", "refuse"] } },
      }),
    ).toEqual(["high_five", "refuse"]);
    expect(
      gestureNamesFromSchema({ properties: { name: { type: "string" } } }),
    ).toBeNull();
    expect(gestureNamesFromSchema(null)).toBeNull();
  });
  test("'Más gestos' leaves out what the preset grid already has", () => {
    const more = moreGestures(GESTURE_CATALOGUE_FALLBACK, [
      "wave",
      "dance",
      "shake_hand",
      "hug",
      "clap",
      "release_arm",
    ]);
    const names = more.map((g) => g.name);
    expect(names).not.toContain("hug");
    expect(names).not.toContain("wave_above_head");
    expect(names).not.toContain("clamp");
    expect(names).toContain("high_five");
    expect(more.length).toBe(18);
  });
  test("an unknown firmware name is kept, marked as raw", () => {
    const [g] = moreGestures(["moonwalk"], []);
    expect(g).toEqual({
      name: "moonwalk",
      label: "moonwalk",
      known: false,
      hint: null,
    });
  });
});
