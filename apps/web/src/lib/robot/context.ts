/**
 * Console-wide live state, shared through Svelte context.
 *
 * The dashboard and the live map each used to construct their own instance,
 * which meant two independent 2s polls against `GET /state` and — worse — a
 * trail and odometer that reset to zero every time the operator moved between
 * the two pages. The protected layout now owns a single instance and hands it
 * down, so the path the robot has walked survives navigation and the topbar can
 * show connection status on every page.
 *
 * The voice loop's switch is shared the same way: whether the robot is listening
 * is a privacy state, and the topbar has to be able to say so on every page.
 */

import { getContext, setContext } from "svelte";
import type { RobotLive } from "./live-state.svelte";
import type { VoiceLoopControl } from "./voice-loop.svelte";
import type { Gate } from "./gate.svelte";

const KEY = Symbol("robot-live");
const VOICE_KEY = Symbol("voice-loop");
const GATE_KEY = Symbol("nav-gate");

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

export function setVoiceLoop(voice: VoiceLoopControl): VoiceLoopControl {
  return setContext(VOICE_KEY, voice);
}

export function getVoiceLoop(): VoiceLoopControl {
  const voice = getContext<VoiceLoopControl | undefined>(VOICE_KEY);
  if (!voice) {
    throw new Error(
      "getVoiceLoop() must be called under the (protected) layout, which provides it.",
    );
  }
  return voice;
}

export function setGate(gate: Gate): Gate {
  return setContext(GATE_KEY, gate);
}

/** The Nav2 gate poller — armed-for-autonomous-driving is shown on every page. */
export function getGate(): Gate {
  const gate = getContext<Gate | undefined>(GATE_KEY);
  if (!gate) {
    throw new Error(
      "getGate() must be called under the (protected) layout, which provides it.",
    );
  }
  return gate;
}
