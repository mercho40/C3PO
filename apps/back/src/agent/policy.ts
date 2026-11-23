/**
 * Which skills a model is offered, by mode. ONE rule for the typed agent and
 * both voice engines (`voice/realtime.ts` uses it too).
 */

import { LOOK_TOOL, VERIFY_TOOL } from "./look";

export type AgentMode = "operator" | "voice";

/**
 * Tools that run here in back, not on the bridge (agent/tools.ts). Each takes
 * its name from the bridge's same-named Gemini tool, which is then not offered.
 */
export const BACK_TOOL_NAMES: ReadonlySet<string> = new Set([
  LOOK_TOOL.name,
  VERIFY_TOOL.name,
]);

export function agentToolExclusions(mode: AgentMode): ReadonlySet<string> {
  // zero_torque and damp go limp: a standing robot falls. No sentence a model
  // can be given makes that the right next step (a "make it safe" after PARAR,
  // a misheard "relajate"); the operator keeps both on the console, the
  // headset bring-up and the remote. Never offered.
  const always = ["zero_torque", "damp"];
  // The voice host owns both ends of the audio channel. Giving these tools to
  // the model permits duplicate speech and competing reads from one buffer.
  // ask_user speaks too, so it is the host's as well.
  return new Set(
    mode === "voice" ? [...always, "listen", "say", "ask_user"] : always,
  );
}

/** Stops are never trimmed away: a model asked to stop must always have the tool. */
const ALWAYS_OFFERED: ReadonlySet<string> = new Set([
  "stop_everything",
  "soft_stop",
  "cancel_task",
  "cancel_navigation",
]);

export type OfferedSkill = {
  name: string;
  dangerLevel?: string;
  tier?: string;
  works: { sim: boolean; real: boolean };
};

/**
 * Whether a skill is offered to the model in this mode.
 *
 * `allow` is AGENT_TOOLS (`config.agent.tools`): only those bridge tools, plus
 * the stops. null: everything. The whole catalogue went into every turn
 * (17k–70k input tokens on 2026-11-13), which ran the free fallback key into
 * 429 after ~12 turns at the stand.
 *
 * Voice drops high-danger skills as well as the host's own two. A spoken turn
 * comes from whatever the microphone overheard — anybody standing near the
 * robot, a video playing in the room — and the legacy engine (the default)
 * offered it everything: `arm_navigation`, `start_walking_waist`, `move_arm`.
 * The realtime engine already filtered them; the two now cannot disagree.
 *
 * T2 (arm/hand) IS offered, whatever its danger level: the bridge refuses
 * every T2 call until an operator presses "Confirmar" on the console, so that
 * gate — not this list — decides whether an overheard request moves an arm.
 * `high` stays for T1 until Nav2 has driven the robot under the governor: it
 * is what keeps `arm_navigation` off voice.
 *
 * Voice also drops a skill nobody has watched work on either target: on 'real'
 * the bridge dispatches it live all the same, and squat, sit_g1, dance and
 * lie_up are T1, so no confirmation stands between a misheard word and the
 * posture. T0 never moves, T2 waits for «Confirmar», and cancel_navigation
 * only ends motion, so those stay. No tier (an older bridge) is not T0.
 */
export function offeredInMode(
  skill: OfferedSkill,
  mode: AgentMode,
  allow: ReadonlySet<string> | null = null,
): boolean {
  if (agentToolExclusions(mode).has(skill.name)) return false;
  // back's own look/verify (agent/look.ts) take the names; the bridge's Gemini ones are not offered.
  if (BACK_TOOL_NAMES.has(skill.name)) return false;
  if (allow && !allow.has(skill.name) && !ALWAYS_OFFERED.has(skill.name))
    return false;
  if (mode !== "voice") return true;
  const unwatched = !skill.works.sim && !skill.works.real;
  if (
    unwatched &&
    skill.tier !== "T0" &&
    skill.tier !== "T2" &&
    skill.name !== "cancel_navigation"
  )
    return false;
  return skill.tier === "T2" || skill.dangerLevel !== "high";
}

/** `offeredInMode` under one allowlist, and its typo check. */
export function createToolPolicy(allow: ReadonlySet<string> | null) {
  let warnedUnknownTools = false;
  return {
    allow,
    offered: (skill: OfferedSkill, mode: AgentMode) =>
      offeredInMode(skill, mode, allow),
    /** Names in AGENT_TOOLS the bridge does not have, logged once (a typo hides a tool). */
    warnUnknown(catalogue: readonly { name: string }[]): string[] {
      if (!allow) return [];
      const known = new Set([
        ...catalogue.map((s) => s.name),
        ...BACK_TOOL_NAMES,
      ]);
      const unknown = [...allow].filter((n) => !known.has(n));
      if (unknown.length && !warnedUnknownTools) {
        warnedUnknownTools = true;
        console.warn(
          `[agent] AGENT_TOOLS names the bridge does not have: ${unknown.join(", ")}`,
        );
      }
      return unknown;
    },
  };
}

export type ToolPolicy = ReturnType<typeof createToolPolicy>;
