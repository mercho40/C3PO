/**
 * Shared robot formatting and fault translation.
 *
 * Pinned because these used to be copy-pasted per panel and had drifted: the
 * same battery reading coloured differently in the topbar and the dashboard,
 * and fault tokens printed verbatim.
 */

import { describe, expect, test } from "bun:test";
import {
  batteryLevel,
  formatAge,
  formatDistance,
  formatPose,
  yawDegrees,
} from "../src/lib/robot/format";
import { readFault, readFaults, staleness } from "../src/lib/robot/faults";
import { linkFromError } from "../src/lib/robot/link-copy";

describe("format", () => {
  test("battery bands", () => {
    expect(batteryLevel(null)).toBe("unknown");
    expect(batteryLevel(15)).toBe("critical");
    expect(batteryLevel(20)).toBe("critical");
    expect(batteryLevel(35)).toBe("low");
    expect(batteryLevel(55)).toBe("ok");
    expect(batteryLevel(87)).toBe("full");
  });

  test("distance stays in metres for a lab session", () => {
    expect(formatDistance(0)).toBe("0.0 m");
    expect(formatDistance(4.26)).toBe("4.3 m");
    expect(formatDistance(42.4)).toBe("42 m");
    expect(formatDistance(1500)).toBe("1.50 km");
  });

  test("ages never read as 'now' when unknown", () => {
    expect(formatAge(null)).toBe("—");
    expect(formatAge(0.2)).toBe("ahora");
    expect(formatAge(12)).toBe("hace 12 s");
    expect(formatAge(300)).toBe("hace 5 min");
  });

  test("pose and yaw", () => {
    expect(formatPose(null)).toBe("—");
    expect(formatPose({ x_meters_world: 1.244, y_meters_world: -0.5 })).toBe(
      "x 1.24 · y -0.50 m",
    );
    expect(yawDegrees(Math.PI / 2)).toBe(90);
    expect(yawDegrees(null)).toBeNull();
  });
});

describe("faults", () => {
  test("bridge health tokens are translated and scoped to the bridge", () => {
    const f = readFault(
      "fsm_rpc_failing_12_polls__restart_the_bridge_if_this_persists",
    );
    expect(f.scope).toBe("bridge");
    expect(f.severity).toBe("danger");
    expect(f.text).toContain("12");
    expect(f.raw).toContain("fsm_rpc_failing");
  });

  test("low battery is the robot's", () => {
    const f = readFault("low_battery_15pct");
    expect(f.scope).toBe("robot");
    expect(f.text).toContain("15");
  });

  test("unknown tokens are kept, not hidden", () => {
    const f = readFault("something_new");
    expect(f.raw).toBe("something_new");
  });

  test("danger sorts before warn", () => {
    const list = readFaults(["stale_pose_34s", "low_battery_10pct"]);
    expect(list[0].raw).toBe("low_battery_10pct");
  });

  test("staleness reads posture/pose/link ages", () => {
    const s = staleness([
      "stale_fsm_12s",
      "stale_pose_34s",
      "stale_lowstate_1.3s",
    ]);
    expect(s.posture).toBe(12);
    expect(s.pose).toBe(34);
    expect(s.link).toBe(1.3);
    expect(staleness([]).pose).toBeNull();
  });
});

describe("link vocabulary", () => {
  test("classifies back errors", () => {
    expect(linkFromError(401, null)).toBe("expired");
    expect(linkFromError(502, { error: "bridge_unavailable" })).toBe(
      "noBridge",
    );
    expect(linkFromError(null, null)).toBe("noApi");
    expect(linkFromError(500, {})).toBe("offline");
    expect(linkFromError(503, new Error("Failed to fetch"))).toBe("noApi");
  });
});
