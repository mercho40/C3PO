import { defineSkill, t } from "./define";

export const zeroTorque = defineSkill({
  name: "zero_torque",
  description:
    "Enter Zero Torque mode — actuators receive no command. The 'fully off' terminal " +
    "state. On the G1 FSM: legal only from Damp. From ZeroTorque you can only go back " +
    "to Damp.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_is_damp"],
  expectedDurationSeconds: 1,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
