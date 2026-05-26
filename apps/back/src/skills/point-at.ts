import { defineSkill, t } from "./define";

export const pointAt = defineSkill({
  name: "point_at",
  description:
    "Extend the right arm forward — closest available 'point' gesture on the G1. Firmware " +
    "'forward push' (api_id=7106, data=36). Requires a locomotion-active FSM state on real " +
    "hardware. For pointing at a specific world-frame target, the LLM should orient the robot " +
    "with `turn` first.",
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
