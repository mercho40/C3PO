import { defineSkill, t } from "./define";

export const lieUp = defineSkill({
  name: "lie_up",
  description:
    "Enter Lie-Up mode — robot transitions to a face-up lying pose. G1 firmware mode " +
    "702 on api_id=7101. Legal target from Damp. Useful pre-storage state and the " +
    "starting pose for recovery sequences.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_is_damp"],
  expectedDurationSeconds: 4,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "medium",
  status: "stub",
  works: { sim: false, real: true },
});
