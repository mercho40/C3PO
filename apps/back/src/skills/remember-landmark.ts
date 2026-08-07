import { defineSkill, t } from "./define";

export const rememberLandmark = defineSkill({
  name: "remember_landmark",
  description:
    "Save the robot's current world-frame pose under a name (e.g. 'kitchen', " +
    "'charging_dock') for later recall_landmark. Needs a live pose from get_state — " +
    "on real G1 today that's null until a world-frame pose source is wired (Phase 1b), " +
    "so this fails with no_pose there for now. In-memory only: landmarks don't survive " +
    "a bridge restart.",
  parameters: t.Object({
    name: t.String({
      minLength: 1,
      maxLength: 64,
      description: "Name to save the current pose under.",
    }),
  }),
  preconditions: ["pose_available"],
  expectedDurationSeconds: 0.1,
  cancellable: false,
  typicalFailureModes: ["no_pose"],
  classification: "memory",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: false },
});
