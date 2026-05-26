import { defineSkill, t } from "./define";

export const hug = defineSkill({
  name: "hug",
  description:
    "Open arms wide for a hug. G1 firmware 'hug' (api_id=7106, data=19). Requires a " +
    "locomotion-active FSM state. Hidden by firmware while in Run — prefer Walk or " +
    "Walk(waist).",
  parameters: t.Object({}),
  preconditions: ["fsm_state_in_{walk,walk_waist}"],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["fsm_not_locomotion_state", "transport_unsupported"],
  classification: "gesture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
