/**
 * /state — current robot state, proxied from the bridge's `get_state` tool.
 *
 * Read-only and safe to poll. Returns the bridge's state dict (pose, battery,
 * posture, faults, env). 502 if the bridge is unreachable, and 502
 * `bridge_bad_payload` (naming the field) if it answers a state that is not
 * the contract's: the console then says it has no current state, instead of
 * drawing a pose or a posture out of `undefined`. Never on the stop path — the
 * stop is `/skills/stop_everything/invoke`, which checks nothing.
 */

import { Elysia } from "elysia";

import type { Static } from "@sinclair/typebox";

import { BridgeUnavailableError, type BridgeMcp } from "../bridge/client";
import {
  BridgeFailureBody,
  RobotStateSchema,
  badPayload,
  payloadCheck,
} from "../bridge/payloads";

/**
 * Shape returned by the bridge's `get_state` tool — `contracts/`'s, derived
 * (bridge/payloads.ts), no longer restated here. `raw` is the diagnostic
 * detail behind the headline fields (every age is seconds since the last
 * message on that channel; null means "never received"); absent in stub mode.
 */
export type RobotState = Static<typeof RobotStateSchema>;
export type RobotStateRaw = NonNullable<RobotState["raw"]>;

const checkState = payloadCheck(RobotStateSchema);

export const stateRoutes = (deps: { bridge: Pick<BridgeMcp, "callTool"> }) =>
  new Elysia().get(
    "/state",
    async ({ status }) => {
      let state: unknown;
      try {
        state = await deps.bridge.callTool("get_state");
      } catch (err) {
        if (err instanceof BridgeUnavailableError)
          return status(502, { error: "bridge_unavailable" });
        return status(502, { error: "bridge_error" });
      }
      const checked = checkState(state);
      return checked.ok
        ? checked.value
        : status(502, badPayload("/state", checked));
    },
    {
      response: { 200: RobotStateSchema, 502: BridgeFailureBody },
      detail: {
        summary: "Current robot state (pose, battery, posture, faults).",
        tags: ["state"],
      },
    },
  );
