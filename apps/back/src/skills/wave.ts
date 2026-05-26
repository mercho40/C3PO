import { defineSkill, t } from "./define";

export const wave = defineSkill({
  name: "wave",
  description:
    "Wave the upper arm — friendly greeting. G1 firmware 'high wave' gesture " +
    "(api_id=7106, data=26). Requires a locomotion-active FSM state (Walk / Walk(waist) / Run) " +
    "on real hardware.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_in_{walk,walk_waist,run}"],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["fsm_not_locomotion_state", "transport_unsupported"],
  classification: "gesture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
