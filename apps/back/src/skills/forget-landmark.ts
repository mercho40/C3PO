import { defineSkill, t } from "./define";

export const forgetLandmark = defineSkill({
  name: "forget_landmark",
  description: "Delete a saved landmark. Returns status=not_found if the name doesn't exist.",
  parameters: t.Object({
    name: t.String({
      minLength: 1,
      maxLength: 64,
      description: "Landmark name to delete.",
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
