/**
 * /state — current robot state, proxied from the bridge's `get_state` tool.
 *
 * Read-only and safe to poll. Returns the bridge's state dict (pose, battery,
 * posture, faults, env). 502 if the bridge is unreachable.
 */

import { Elysia } from "elysia";

import { callTool, BridgeUnavailableError } from "../bridge/client";

/**
 * Diagnostic detail behind the headline fields — `bridge/sdk/state.py`'s `raw`
 * block. Every age is seconds since the last message on that channel; null
 * means "never received", not "fine". Absent entirely in stub mode.
 */
export type RobotStateRaw = {
  tick: number;
  mode_machine: number;
  motor_count: number;
  lowstate_messages_received: number;
  lowstate_age_s?: number;
  pose_source: string;
  pose_messages_received: number;
  pose_age_s?: number | null;
  fsm_id?: number | null;
  fsm_mode?: number | null;
  fsm_age_s?: number | null;
  battery_soh_pct?: number | null;
  /** Signed: negative means discharging. */
  battery_current_ma?: number | null;
  battery_messages_received?: number;
  battery_age_s?: number | null;
};

/** Shape returned by the bridge's `get_state` tool. */
export type RobotState = {
  /** Null until the first odometry message arrives (real hardware). */
  pose: {
    x_meters_world: number;
    y_meters_world: number;
    z_meters_world?: number;
    yaw_radians_world: number;
  } | null;
  battery_pct: number | null;
  posture: string;
  faults: string[];
  env: string;
  /** True when the bridge is in stub mode: every value is fabricated. */
  stub?: boolean;
  raw?: RobotStateRaw;
};

export const stateRoutes = new Elysia().get(
  "/state",
  async ({ status }) => {
    try {
      return (await callTool("get_state")) as RobotState;
    } catch (err) {
      if (err instanceof BridgeUnavailableError)
        return status(502, { error: "bridge_unavailable" });
      return status(502, { error: "bridge_error" });
    }
  },
  {
    detail: {
      summary: "Current robot state (pose, battery, posture, faults).",
      tags: ["state"],
    },
  },
);
