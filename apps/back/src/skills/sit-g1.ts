import { defineSkill, t } from "./define";

export const sitG1 = defineSkill({
  name: "sit_g1",
  description:
    "Enter Seating mode — robot adopts a seated posture. G1 firmware mode 3 on " +
    "api_id=7101. Calm, low-energy presentation state. Typically reached from Damp.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_is_damp"],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
