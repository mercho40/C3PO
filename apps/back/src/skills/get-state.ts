import { defineSkill, t } from "./define";

export const getState = defineSkill({
  name: "get_state",
  description:
    "Return the robot's current state: pose (world frame), posture (FSM mode label), " +
    "battery percentage, fault list, and raw telemetry. Fast (~ms); use freely to " +
    "ground reasoning about position and capability before issuing locomotion commands.",
  parameters: t.Object({}),
  preconditions: [],
  expectedDurationSeconds: 0.05,
  cancellable: false,
  typicalFailureModes: ["bridge_disconnected", "no_state_received_yet"],
  classification: "introspection",
  dangerLevel: "low",
  status: "real",
  works: { sim: true, real: true },
});
