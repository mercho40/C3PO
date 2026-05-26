import { defineSkill, t } from "./define";

export const startWalking = defineSkill({
  name: "start_walking",
  description:
    "Enter Walk mode — locomotion FSM activates and arm gestures become available. " +
    "On the G1 FSM: legal only from Preparation. Until this runs, walk_to / turn / " +
    "wave / point_at won't produce meaningful motion on real hardware. Typical " +
    "sequence: damp → prepare → start_walking → walk_to / wave.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_is_preparation"],
  expectedDurationSeconds: 2,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "medium",
  status: "stub",
  works: { sim: false, real: true },
});
