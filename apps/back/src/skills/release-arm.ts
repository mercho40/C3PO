import { defineSkill, t } from "./define";

export const releaseArm = defineSkill({
  name: "release_arm",
  description:
    "Return arms to a neutral / idle position. G1 firmware 'release arm' " +
    "(api_id=7106, data=99). Call this after any other gesture to settle the arms " +
    "back to their default hanging pose.",
  parameters: t.Object({}),
  preconditions: ["fsm_state_in_{walk,walk_waist,run}"],
  expectedDurationSeconds: 1,
  cancellable: false,
  typicalFailureModes: ["fsm_not_locomotion_state", "transport_unsupported"],
  classification: "gesture",
  dangerLevel: "low",
  status: "stub",
  works: { sim: false, real: true },
});
