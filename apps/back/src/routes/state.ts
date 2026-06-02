/**
 * /state — current robot state, proxied from the bridge's `get_state` tool.
 *
 * Read-only and safe to poll. Returns the bridge's state dict (pose, battery,
 * posture, faults, env). 502 if the bridge is unreachable.
 */

import { Elysia } from "elysia";

import { callTool, BridgeUnavailableError } from "../bridge/client";

/** Shape returned by the bridge's `get_state` tool. */
export type RobotState = {
  pose: {
    x_meters_world: number;
    y_meters_world: number;
    yaw_radians_world: number;
  };
  battery_pct: number | null;
  posture: string;
  faults: string[];
  env: string;
  stub?: boolean;
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
