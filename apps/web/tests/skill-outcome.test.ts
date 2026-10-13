/**
 * The one classifier every surface uses to say what a skill result means.
 *
 * The properties pinned here are the honesty ones: a firmware refusal that
 * arrives as HTTP 200 is a failure, a step the robot acked but did not take is a
 * failure, a stub that only logged is not a success, and an unreachable bridge
 * is not "retry".
 */

import { describe, expect, test } from "bun:test";
import {
  classifyHttpError,
  classifyInvoke,
  classifySkillResult,
} from "../src/lib/robot/skill-outcome";

describe("classifySkillResult", () => {
  test("a firmware refusal returned as a 200 body is an error, with a named hint", () => {
    const o = classifySkillResult({
      status: "failed",
      phase: "rpc_error",
      error: "rpc_error_code_7401",
    });
    expect(o.kind).toBe("error");
    expect(o.code).toBe("rpc_error_code_7401");
    expect(o.text).toContain("brazo");
  });

  test("an unknown firmware code still fails, and keeps its code", () => {
    const o = classifySkillResult({
      status: "failed",
      error: "rpc_error_code_9999",
    });
    expect(o.kind).toBe("error");
    expect(o.code).toBe("rpc_error_code_9999");
  });

  test("acked but no transition is an error, not 'Completado'", () => {
    const o = classifySkillResult({
      status: "completed",
      phase: "acked_no_transition",
      error: null,
      result: { transitioned: false },
    });
    expect(o.kind).toBe("error");
  });

  test("transitioned null is unverified, distinct from ok and from failed", () => {
    const o = classifySkillResult({
      status: "completed",
      result: { transitioned: null },
    });
    expect(o.kind).toBe("unverified");
  });

  test("transitioned true is ok", () => {
    expect(
      classifySkillResult({
        status: "completed",
        result: { transitioned: true },
      }).kind,
    ).toBe("ok");
  });

  test("a walk that timed out short of its target is a warning", () => {
    const o = classifySkillResult({
      status: "completed",
      phase: "timeout",
      error: null,
      result: { arrived: false },
    });
    expect(o.kind).toBe("warn");
    expect(o.text).toContain("tiempo");
  });

  test("stub and logged_only are never success", () => {
    for (const phase of ["stub", "logged_only"]) {
      const o = classifySkillResult({ status: "completed", phase });
      expect(o.kind).toBe("warn");
      expect(o.simulated).toBe(true);
    }
  });

  test("cancelled and not_applicable are warnings", () => {
    expect(classifySkillResult({ status: "cancelled" }).kind).toBe("warn");
    expect(
      classifySkillResult({ status: "not_applicable", message: "x" }).kind,
    ).toBe("warn");
  });

  test("ok:false (e.g. cancel_task) is a refusal", () => {
    const o = classifySkillResult({ ok: false, reason: "task_not_found" });
    expect(o.kind).toBe("error");
  });

  test("a stop whose damp fallback failed is an error even on a 200", () => {
    const o = classifySkillResult({
      cancelled: 2,
      real_damp_fallback_succeeded: false,
    });
    expect(o.kind).toBe("error");
  });

  test("teleop_active is named", () => {
    const o = classifySkillResult({ status: "failed", phase: "teleop_active" });
    expect(o.kind).toBe("error");
    expect(o.text).toContain("teleoperación");
  });

  test("a stub bridge's {status: ok, stub: true} is not success", () => {
    const o = classifySkillResult({ status: "ok", stub: true });
    expect(o.kind).toBe("warn");
    expect(o.simulated).toBe(true);
  });

  test("result.note is read as the explanation", () => {
    const o = classifySkillResult({
      status: "failed",
      phase: "arm_sdk_engaged",
      result: { note: "arm mirror holds rt/arm_sdk" },
    });
    expect(o.detail).toBe("arm mirror holds rt/arm_sdk");
  });

  test("a plain completed body is ok", () => {
    expect(
      classifySkillResult({ status: "completed", phase: "done" }).kind,
    ).toBe("ok");
    expect(classifySkillResult(null).kind).toBe("ok");
  });
});

describe("classifyHttpError", () => {
  test("bridge_unavailable is named, not a generic retry", () => {
    const o = classifyHttpError(502, { error: "bridge_unavailable" });
    expect(o.code).toBe("bridge_unavailable");
    expect(o.text).toContain("puente");
  });

  test("a network failure has no status", () => {
    expect(classifyHttpError(null, null).code).toBe("network");
  });

  test("403 is admin_required, as a warning", () => {
    const o = classifyHttpError(403, { error: "admin_required" });
    expect(o.kind).toBe("warn");
    expect(o.code).toBe("admin_required");
  });

  test("tool_error carries the bridge's detail", () => {
    const o = classifyHttpError(502, { error: "tool_error", detail: "boom" });
    expect(o.detail).toBe("boom");
  });
});

describe("classifyInvoke", () => {
  test("Eden's 503-with-Error (fetch threw) is a network failure, not HTTP 503", () => {
    const o = classifyInvoke({
      error: { status: 503, value: new Error("Failed to fetch") },
    });
    expect(o.code).toBe("network");
  });

  test("back's 504 bridge_timeout says the order went out", () => {
    const o = classifyInvoke({
      error: { status: 504, value: { error: "bridge_timeout" } },
    });
    expect(o.code).toBe("bridge_timeout");
  });

  test("routes an Eden error to the HTTP classifier and data to the body classifier", () => {
    expect(
      classifyInvoke({
        error: { status: 502, value: { error: "bridge_unavailable" } },
      }).code,
    ).toBe("bridge_unavailable");
    expect(
      classifyInvoke({ data: { status: "failed", error: "x" } }).kind,
    ).toBe("error");
  });
});
