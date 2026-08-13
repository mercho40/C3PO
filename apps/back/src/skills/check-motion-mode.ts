import { defineSkill, t } from "./define";

export const checkMotionMode = defineSkill({
  name: "check_motion_mode",
  description:
    "Report which motion controller currently owns the robot. Read-only, instant, no motion. " +
    "CALL THIS FIRST whenever the robot accepts a command and then does nothing. The sport " +
    "service answers rpc code 0 both when a state change is refused AND when there is no " +
    "controller loaded to perform it, so those two very different situations are " +
    "indistinguishable from the reply alone — this tool tells them apart in one call. " +
    "An empty mode_name means NO controller is loaded (the robot is in debug mode): nothing " +
    "will move, get_state will report fsm_id null and posture 'unknown', and no amount of " +
    "retrying postures or walk commands will help. That state is normal after someone has " +
    "run a teleoperation session, which releases the mode and does not restore it. Recovering " +
    "it requires SelectMode('ai'), which this bridge deliberately cannot send — loading a " +
    "controller onto a robot someone else may be driving is an operator decision, not a " +
    "tool call. Tell the operator instead.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.1,
  cancellable: false,
  typicalFailureModes: ["bridge_disconnected", "motion_switcher_no_answer"],
  classification: "introspection",
  dangerLevel: "low",
  status: "real",
  works: { sim: false, real: true },
});
