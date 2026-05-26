import { defineSkill, t } from "./define";

export const clap = defineSkill({
  name: "clap",
  description:
    "Bring hands together to clap. G1 firmware 'clap' (api_id=7106, data=17). " +
    "Requires a locomotion-active FSM state.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_in_{walk,walk_waist,run}"],
  expectedDurationSeconds: 2,
  cancellable: false,
  typicalFailureModes: ["fsm_not_locomotion_state", "transport_unsupported"],
  classification: "gesture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
