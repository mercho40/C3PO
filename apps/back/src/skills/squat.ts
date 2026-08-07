import { defineSkill, t } from "./define";

export const squat = defineSkill({
  name: "squat",
  description:
    "Enter Squat mode — robot crouches to a lowered stance. Dispatches G1 firmware mode " +
    "706 (SquatUp) — the reference implementation never sends the separate Squat index " +
    "(2); treat it as unverified. From Squat the FSM only accepts a transition back to Damp.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 3,
  cancellable: false,
  typicalFailureModes: ["fsm_transition_rejected", "transport_unsupported"],
  classification: "posture",
  dangerLevel: "low",
  status: "real",
  works: { sim: false, real: true },
});
