import { defineSkill, t } from "./define";

export const listLandmarks = defineSkill({
  name: "list_landmarks",
  description:
    "List all saved landmarks, most recently saved first. Use before recall_landmark when " +
    "you don't already know the exact name.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.05,
  cancellable: false,
  typicalFailureModes: [],
  classification: "memory",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
