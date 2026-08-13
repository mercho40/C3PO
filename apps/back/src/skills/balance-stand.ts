import { defineSkill, t } from "./define";

export const balanceStand = defineSkill({
  name: "balance_stand",
  description:
    "Engage the robot's stand-and-balance controller (SetBalanceMode 0). Distinct from the " +
    "FSM postures: it sets the balance controller's mode rather than requesting a state " +
    "change, so it does not alter fsm_id and get_state will look unchanged afterwards. " +
    "Sent while the robot is standing. Accepted by the firmware on this robot, but its " +
    "effect has never been confirmed — it did not unblock the walk transition it was added " +
    "to investigate.",
  parameters: t.Object({}),
  preconditions: ["robot_upright"],
  expectedDurationSeconds: 1,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "medium",
  status: "real",
  // Accepted with rpc code 0 on hardware, but with no observable effect, so
  // "works" would overstate it. The agent should not reach for this
  // autonomously while what it does is unknown.
  works: { sim: false, real: false },
});
