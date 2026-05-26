import { defineSkill, t } from "./define";

export const squat = defineSkill({
  name: "squat",
  description:
    "Enter Squat mode — robot crouches to a lowered stance. G1 firmware Squat (mode 2) " +
    "and SquatUp (706) collapse to the same physical pose at different control gains. " +
    "From Squat the FSM only accepts a transition back to Damp.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
