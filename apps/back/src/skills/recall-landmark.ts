import { defineSkill, t } from "./define";

export const recallLandmark = defineSkill({
  name: "recall_landmark",
  description:
    "Recall a previously saved landmark pose — feed x_meters_world/y_meters_world straight " +
    "into walk_to. Returns status=not_found if the name was never saved (or the bridge " +
    "restarted since — landmarks are in-memory only).",
  parameters: t.Object({
    name: t.String({
      minLength: 1,
      maxLength: 64,
      description: "Landmark name to recall.",
    }),
  }),
  preconditions: [],
  expectedDurationSeconds: 0.05,
  cancellable: false,
  typicalFailureModes: ["not_found"],
  classification: "memory",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
