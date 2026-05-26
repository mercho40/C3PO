import { defineSkill, t } from "./define";

export const prepare = defineSkill({
  name: "prepare",
  description:
    "Enter Preparation mode — required gateway to Walk / Walk(waist) / Run. On the G1 FSM, " +
    "legal only from Damp. From Preparation you can transition to Walk, Walk(waist), Run, " +
    "or back to Damp.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_is_damp"],
  expectedDurationSeconds: 2,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
