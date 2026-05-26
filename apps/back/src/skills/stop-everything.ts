import { defineSkill, t } from "./define";

export const stopEverything = defineSkill({
  name: "stop_everything",
  description:
    "Halt all motion immediately and cancel any in-flight tasks. Safety-critical and fast " +
    "(<1 s). Cancels every running task in the registry AND sends a zero-velocity burst to " +
    "the locomotion channel. Use this when something looks wrong, when humans approach the " +
    "robot, or when you want to abort a sequence cleanly.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.5,
  cancellable: false,
  typicalFailureModes: ["bridge_disconnected"],
  classification: "safety",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
