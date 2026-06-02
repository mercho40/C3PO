/**
 * /state — current robot state, proxied from the bridge's `get_state` tool.
 *
 * Read-only and safe to poll. Returns the bridge's state dict (pose, battery,
 * posture, faults, env). 502 if the bridge is unreachable.
 */

import { Elysia } from "elysia";

import { callTool, BridgeUnavailableError } from "../bridge/client";

export const stateRoutes = new Elysia().get(
  "/state",
  async ({ status }) => {
    try {
      return await callTool("get_state");
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
