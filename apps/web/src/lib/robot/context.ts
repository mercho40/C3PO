/**
 * One `RobotLive` per console session, shared through Svelte context.
 *
 * The dashboard and the live map each used to construct their own instance,
 * which meant two independent 2s polls against `GET /state` and — worse — a
 * trail and odometer that reset to zero every time the operator moved between
 * the two pages. The protected layout now owns a single instance and hands it
 * down, so the path the robot has walked survives navigation and the topbar can
 * show connection status on every page.
 */

import { getContext, setContext } from "svelte";
import type { RobotLive } from "./live-state.svelte";

const KEY = Symbol("robot-live");

export function setRobotLive(live: RobotLive): RobotLive {
  return setContext(KEY, live);
}

export function getRobotLive(): RobotLive {
  const live = getContext<RobotLive | undefined>(KEY);
  if (!live) {
    throw new Error(
      "getRobotLive() must be called under the (protected) layout, which provides it.",
    );
  }
  return live;
}
