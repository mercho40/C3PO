import { defineSkill, t } from "./define";

export const listActiveTasks = defineSkill({
  name: "list_active_tasks",
  description:
    "List running tasks. Optionally include recently-completed tasks so you can inspect " +
    "the last skill's result.",
  parameters: t.Object({
    include_recent: t.Boolean({
      default: false,
      description:
        "If true, also include up to 10 recently-completed tasks (≤5 min old).",
    }),
  }),
  preconditions: [],
  expectedDurationSeconds: 0.05,
  cancellable: false,
  typicalFailureModes: [],
  classification: "task",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
