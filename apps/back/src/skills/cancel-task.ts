import { defineSkill, t } from "./define";

export const cancelTask = defineSkill({
  name: "cancel_task",
  description:
    "Request graceful cancellation of an in-flight task. The skill observes the cancel " +
    "signal between iterations and ramps motion down cleanly. Use the task_id returned by " +
    "walk_to / turn / similar long-running tools.",
  parameters: t.Object({
    task_id: t.String({
      minLength: 1,
      description: "Task ID returned by a long-running tool.",
    }),
  }),
  preconditions: [],
  expectedDurationSeconds: 0.3,
  cancellable: false,
  typicalFailureModes: [
    "unknown_task_id",
    "cancel_already_requested",
    "task_not_running",
  ],
  classification: "task",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
