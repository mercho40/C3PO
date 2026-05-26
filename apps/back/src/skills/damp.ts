import { defineSkill, t } from "./define";

export const damp = defineSkill({
  name: "damp",
  description:
    "Engage damping mode — set all joints to zero stiffness. The canonical 'come to rest' " +
    "state for the G1 FSM. Only legal from Preparation, Walk, Walk(waist), Run, Squat, or " +
    "ZeroTorque. From Damp you can transition to ZeroTorque, Preparation, SquatUp, or LieUp.",
  parameters: t.Object({}),
  preconditions: [
    "fsm_state_in_{preparation,walk,walk_waist,run,squat,zero_torque}",
  ],
  expectedDurationSeconds: 1,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
